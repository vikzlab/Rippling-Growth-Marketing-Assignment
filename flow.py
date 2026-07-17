"""
CrewAI Flow orchestrator for the competitor research pipeline.
Each step reads/writes shared AgentState; no argument-passing between steps.
"""

import os

from dotenv import load_dotenv

# Must be called before importing any local step modules — they instantiate
# Anthropic clients at module level via os.getenv(), so the .env values must
# be present before those import-time assignments run.
load_dotenv()

from crewai.flow.flow import Flow, listen, or_, router, start

from state import AgentState, SourceStatus
from steps.adaptive_replan import adaptive_replan, needs_replan
from steps.clarify import check_needs_clarification
from steps.embed_and_retrieve import embed_and_retrieve
from steps.plan_research import apply_plan_to_state, plan_research
from steps.route_followup import route_followup  # used by app.py for Flow C
from steps.run_research import run_research, retry_source
from steps.synthesize import synthesize
from steps.volume_router import route_by_volume


class CompetitorResearchFlow(Flow[AgentState]):
    """The end-to-end competitor marketing intelligence pipeline.

    Usage:
        flow = CompetitorResearchFlow()
        flow.kickoff(inputs={
            "competitor": "Gusto",
            "competitor_domain": "gusto.com",
        })
        # results now live in flow.state.brief_markdown, flow.state.claims,
        # and flow.state.to_output_json()

    The `inputs` dict populates the matching fields on AgentState before the
    @start method runs -- that's how a competitor name gets into the flow.
    """

    # ------------------------------------------------------------------ #
    # STEP 1 -- CLARIFY (cheap model)
    # ------------------------------------------------------------------ #
    @start()
    def clarify(self):
        """Entry point. Decide whether the request is ambiguous enough to
        need a clarifying question before spending any research budget.

        NOTE ON HITL: in a pure-CLI or pure-Flow run we don't block for a
        human answer here -- the clarifying question, if any, is surfaced to
        the user by app.py BEFORE the flow is kicked off. By the time the
        flow runs, any needed clarification is already in
        state.user_clarifications. This method exists so the decision is
        logged in state (and so the design is complete), not to pause
        execution mid-flow.
        """
        if not self.state.user_clarifications:
            result = check_needs_clarification(self.state.competitor)
            # Record the question (if any) for transparency; app.py is
            # responsible for having already asked it interactively.
            if result.get("needs_clarification"):
                self.state.conversation_log.append(
                    {
                        "clarifying_question": result.get("question", ""),
                        "note": "surfaced to user before flow kickoff",
                    }
                )

    # ------------------------------------------------------------------ #
    # STEP 2 -- PLAN RESEARCH (cheap model)  --  the "real agentic loop" pt 1
    # ------------------------------------------------------------------ #
    @listen(clarify)
    def plan(self):
        """Decide which sources are worth checking for THIS competitor, and
        record the reasoning. This is the first half of what makes the system
        a real agent and not a linear script -- the plan is a decision, not a
        hardcoded list.
        """
        plan_result = plan_research(
            self.state.competitor, self.state.user_clarifications
        )
        apply_plan_to_state(self.state, plan_result)

    # ------------------------------------------------------------------ #
    # STEP 3 -- RUN RESEARCH (plain code, no LLM)
    # ------------------------------------------------------------------ #
    @listen(plan)
    def research(self):
        """Execute the plan: call the source tools (website, ad libraries,
        press) for each source the planner chose. Pure dispatch -- the
        judgment already happened in plan(). Results, including EMPTY/FAILED
        outcomes, are written into state.sources.
        """
        run_research(self.state)

    # ------------------------------------------------------------------ #
    # STEP 4 -- ROUTER: does anything need adaptive re-planning?
    # ------------------------------------------------------------------ #
    @router(research)
    def check_for_gaps(self):
        """Inspect the research results and route accordingly. This is the
        cost-control gate for the mid-run reasoning call: we only pay for
        adaptive_replan if a source actually came back EMPTY or FAILED. If
        everything succeeded cleanly, there's nothing to adapt to, so we
        route straight past it.

        Returns a string that maps to a @listen("...") method below.
        """
        if needs_replan(self.state):
            return "has_gaps"
        return "clean"

    # ------------------------------------------------------------------ #
    # STEP 5a -- ADAPTIVE REPLAN (cheap model)  --  "real agentic loop" pt 2
    # ------------------------------------------------------------------ #
    @listen("has_gaps")
    def replan(self):
        """A source came back empty or failed. Reason about it: is it worth
        retrying, or a signal to deprioritize a related source? This is the
        second half of the agentic loop -- reacting to what was actually
        found, not just executing a fixed plan. The reasoning is appended to
        state.planner_reasoning so it shows up in the final transparency
        trail.

        After replanning, fall through to the same volume-routing step the
        clean path uses -- both paths converge at volume routing.
        """
        result = adaptive_replan(self.state)
        for source_name in result.get("retry_sources", []):
            retry_source(self.state, source_name)

    @listen("clean")
    def no_replan_needed(self):
        """The clean path: every source succeeded, skip the replan call."""
        pass

    # ------------------------------------------------------------------ #
    # STEP 6 -- ROUTER: is there too much content for one synthesis prompt?
    # ------------------------------------------------------------------ #
    @router(or_(replan, no_replan_needed))
    def check_volume(self):
        """The content-volume decision (PRD Section 5.4). If total retrieved
        content is small enough to fit usefully in one prompt, go direct. If
        it's large (a Microsoft-scale competitor footprint), route into the
        embedding path first to retrieve only the most relevant chunks.

        Uses or_() to listen to both the replan and no_replan_needed branches
        -- whichever fires, volume routing runs next. This is the correct
        CrewAI fan-in pattern: @router methods propagate their return string
        as a routing event; @listen methods' return values are silently dropped.
        """
        decision = route_by_volume(self.state)  # "direct" or "embed"
        return decision

    # ------------------------------------------------------------------ #
    # STEP 7a -- EMBED & RETRIEVE (plain code + local vectors, no paid API)
    # ------------------------------------------------------------------ #
    @listen("embed")
    def reduce_content(self):
        """The large-footprint path. Chunk, embed, and retrieve only the
        most relevant content per source, shrinking it down before synthesis.
        """
        embed_and_retrieve(self.state)

    @listen("direct")
    def skip_reduction(self):
        """The small-footprint path: content already fits, pass straight through."""
        pass

    # ------------------------------------------------------------------ #
    # STEP 8 -- SYNTHESIZE (FRONTIER model)  --  the one high-judgment call
    # ------------------------------------------------------------------ #
    @listen(or_(reduce_content, skip_reduction))
    def synthesize_brief(self):
        """The single most important step. Takes all retrieved content and
        produces the graded deliverables: the markdown brief (including the
        specific, actionable 'Relevance to Rippling' section) and the list of
        grounded, source-cited claims. This is the only frontier-model call
        in the whole pipeline -- everything upstream existed to make this
        call's input as clean and focused as possible.

        Uses or_() so it fires after whichever of the two volume paths ran.
        """
        synthesize(self.state)

    # ------------------------------------------------------------------ #
    # STEP 9 -- FINALIZE (plain code)
    # ------------------------------------------------------------------ #
    @listen(synthesize_brief)
    def finalize(self):
        """Write the JSON deliverable to disk and return the final state.
        Plain code -- just serialization. Returns the output dict so
        flow.kickoff() has a meaningful return value.
        """
        import json

        os.makedirs("output", exist_ok=True)
        safe_name = self.state.competitor.lower().replace(" ", "_").replace("/", "_")

        # Write the markdown brief.
        with open(f"output/{safe_name}_brief.md", "w") as f:
            f.write(self.state.brief_markdown or "# No brief generated")

        # Write the structured JSON.
        output_json = self.state.to_output_json()
        with open(f"output/{safe_name}_output.json", "w") as f:
            json.dump(output_json, f, indent=2, default=str)

        return output_json


def run_competitor_research(competitor: str, domain: str | None = None,
                            clarifications: dict | None = None) -> AgentState:
    """Convenience wrapper for a single end-to-end run. app.py and the CLI
    both call this rather than driving the Flow class directly.

    Returns the final AgentState, from which the caller can read
    .brief_markdown, .claims, and .to_output_json().
    """
    flow = CompetitorResearchFlow()
    flow.kickoff(
        inputs={
            "competitor": competitor,
            "competitor_domain": domain,
            "user_clarifications": clarifications or {},
        }
    )
    return flow.state


if __name__ == "__main__":
    # Minimal CLI entry point for testing without the Streamlit UI.
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else input("Competitor name: ").strip()
    dom = sys.argv[2] if len(sys.argv) > 2 else input("Domain (optional): ").strip() or None

    print(f"\nResearching {name}...\n")
    final_state = run_competitor_research(name, dom)

    print("\n" + "=" * 60)
    print(final_state.brief_markdown)
    print("=" * 60)
    print(f"\nSaved to output/. {len(final_state.claims)} grounded claim(s).")
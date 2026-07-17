"""
Streamlit UI for the competitor marketing intelligence agent.
"""

import json

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from state import AgentState, SourceStatus
from steps.clarify import check_needs_clarification
from steps.route_followup import route_followup
from steps.run_research import run_research
from steps.synthesize import synthesize
from flow import run_competitor_research
from tools.web_fetch import find_pricing_links, fetch_page
from tools.press_search import search_press, search_social
from tools.scrapecreators_facebook import search_meta_ads
from tools.serpapi_google import search_google_ads


# ---------------------------------------------------------------------- #
# FOLLOW-UP HANDLERS (Flow C)
# Dict-based dispatch matching the _SOURCE_FETCHERS pattern in run_research.py.
# Each handler mutates one or more sources in state; the caller then
# re-synthesizes only against the updated state.
# ---------------------------------------------------------------------- #

def _handle_pricing_followup(state: AgentState) -> None:
    if not state.competitor_domain:
        return
    url = state.competitor_domain
    if not url.startswith("http"):
        url = f"https://{url}"
    pricing_links = find_pricing_links(url)
    if pricing_links:
        deeper = fetch_page(pricing_links[0])
        deeper.depth = "deep"
        state.sources["website"] = deeper


def _handle_press_followup(state: AgentState) -> None:
    state.sources["press"] = search_press(state.competitor)


def _handle_social_followup(state: AgentState) -> None:
    state.sources["social"] = search_social(state.competitor)


def _handle_ads_followup(state: AgentState) -> None:
    state.sources["meta_ads"] = search_meta_ads(state.competitor)
    state.sources["google_ads"] = search_google_ads(
        state.competitor, state.competitor_domain
    )


FOLLOWUP_HANDLERS = {
    "pricing": _handle_pricing_followup,
    "recent_changes": _handle_press_followup,
    "press": _handle_press_followup,
    "social": _handle_social_followup,
    "ads": _handle_ads_followup,
}

st.set_page_config(page_title="Competitor Intelligence Agent", page_icon="🔍", layout="wide")

st.title("Competitor Marketing Intelligence Agent")
st.caption("Built for Rippling GTM · CrewAI Flow + Claude · cost-tiered, grounded, source-cited")

# ---------------------------------------------------------------------- #
# Session state: hold the AgentState across reruns so follow-ups work
# without re-running the whole pipeline.
# ---------------------------------------------------------------------- #
if "agent_state" not in st.session_state:
    st.session_state.agent_state = None
if "pending_clarification" not in st.session_state:
    st.session_state.pending_clarification = None
if "messages" not in st.session_state:
    st.session_state.messages = []


def _render_state(state: AgentState) -> None:
    """Render a completed AgentState: the brief, the sources (including honest
    gaps), the planner's reasoning trail, and the grounded claims."""
    st.markdown(state.brief_markdown or "_No brief generated._")

    with st.expander("Sources checked (including gaps)", expanded=True):
        for name, result in state.sources.items():
            icon = {
                SourceStatus.SUCCESS: "✅",
                SourceStatus.EMPTY: "⚪",
                SourceStatus.FAILED: "❌",
                SourceStatus.NOT_CHECKED: "➖",
            }[result.status]
            st.write(f"{icon} **{name}** — {result.status.value}. {result.note or ''}")

    with st.expander("Agent's reasoning trail"):
        st.write(state.planner_reasoning or "_No reasoning recorded._")

    with st.expander(f"Grounded claims ({len(state.claims)})"):
        for c in state.claims:
            st.write(f"- _{c.section}_ ({c.confidence:.0%}, src: {c.source}): {c.text}")

    st.download_button(
        "Download JSON output",
        data=json.dumps(state.to_output_json(), indent=2, default=str),
        file_name=f"{state.competitor.lower().replace(' ', '_')}_output.json",
        mime="application/json",
    )


# ---------------------------------------------------------------------- #
# INITIAL RESEARCH FORM
# ---------------------------------------------------------------------- #
with st.form("research_form"):
    col1, col2 = st.columns([2, 1])
    with col1:
        competitor = st.text_input("Competitor name", placeholder="e.g. Gusto")
    with col2:
        domain = st.text_input("Domain (optional)", placeholder="gusto.com")
    submitted = st.form_submit_button("Research →", type="primary")

if submitted and competitor:
    # Step 1: check for genuine ambiguity BEFORE spending research budget.
    # This is the interactive half of steps/clarify.py -- the flow itself
    # doesn't block for a human, the UI does.
    clar = check_needs_clarification(competitor)
    if clar.get("needs_clarification"):
        st.session_state.pending_clarification = {
            "competitor": competitor,
            "domain": domain or None,
            "question": clar.get("question", "Could you clarify your focus?"),
        }
    else:
        with st.spinner(f"Researching {competitor}… (1–2 minutes)"):
            st.session_state.agent_state = run_competitor_research(competitor, domain or None)
        st.session_state.messages = []

# ---------------------------------------------------------------------- #
# CLARIFYING QUESTION (only shown if the agent genuinely needs one)
# ---------------------------------------------------------------------- #
if st.session_state.pending_clarification:
    pc = st.session_state.pending_clarification
    st.info(f"**Before I start:** {pc['question']}")
    answer = st.text_input("Your answer")
    if st.button("Continue with this context"):
        with st.spinner(f"Researching {pc['competitor']}…"):
            st.session_state.agent_state = run_competitor_research(
                pc["competitor"],
                pc["domain"],
                clarifications={"focus": answer},
            )
        st.session_state.pending_clarification = None
        st.session_state.messages = []

# ---------------------------------------------------------------------- #
# RESULTS + CONVERSATIONAL FOLLOW-UPS (Flow C)
# ---------------------------------------------------------------------- #
if st.session_state.agent_state is not None:
    state = st.session_state.agent_state
    st.divider()
    st.subheader(f"Brief: {state.competitor}")
    _render_state(state)

    st.divider()
    st.subheader("Follow up")
    st.caption('Try: "dig deeper on their pricing" · "what about their recent news" · "run this for Deel"')

    followup = st.chat_input("Ask a follow-up…")
    if followup:
        # Route the follow-up to a category WITHOUT re-running everything.
        routing = route_followup(state, followup)
        category = routing.get("category", "unclear")

        if category == "unclear":
            st.warning("I'm not sure which part you mean — try naming a specific area "
                       "like pricing, positioning, ads, or recent news.")

        elif category == "new_competitor":
            new_name = routing.get("new_competitor_name") or followup
            with st.spinner(f"Researching {new_name}…"):
                st.session_state.agent_state = run_competitor_research(new_name)
            st.rerun()

        else:
            # Scoped follow-up: act ONLY on the category the router identified,
            # reusing everything else already in state.
            handler = FOLLOWUP_HANDLERS.get(category)
            if handler:
                with st.spinner(f"Digging deeper on {category}…"):
                    handler(state)
                    synthesize(state)
                    st.session_state.agent_state = state
                st.rerun()
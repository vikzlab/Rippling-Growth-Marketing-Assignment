"""
Eval harness: runs the agent against test competitors and scores against rubric.

Run:  python -m eval.run_eval
"""
(from the project root, so imports resolve)
"""

import json
import sys

# Ensure project root is importable when run as `python -m eval.run_eval`.
sys.path.insert(0, ".")

from eval.rubric import score_brief
from flow import run_competitor_research

# The test set. (name, domain) -- domain optional but improves website + the
# Google ads lookup. The third entry is intentionally a smaller company to
# force at least one graceful-degradation case into every eval run.
TEST_COMPETITORS = [
    ("Gusto", "gusto.com"),
    ("Deel", "deel.com"),
    ("Justworks", "justworks.com"),
]


def run_eval() -> None:
    """Runs the agent on every test competitor, scores each, prints a report,
    and writes the full scorecard to output/eval_report.json.
    """
    scorecards = []

    for name, domain in TEST_COMPETITORS:
        print(f"\n{'=' * 60}\nEVALUATING: {name}\n{'=' * 60}")
        try:
            state = run_competitor_research(name, domain)
            card = score_brief(state)
            scorecards.append(card)
            _print_card(card)
        except Exception as exc:
            # An eval harness should never crash on one bad run -- record the
            # failure and continue, so one broken competitor doesn't lose the
            # whole report.
            print(f"  RUN FAILED: {exc}")
            scorecards.append({
                "competitor": name,
                "overall_score": 0.0,
                "error": str(exc),
            })

    _print_summary(scorecards)

    # Persist the full report as a deliverable artifact.
    import os
    os.makedirs("output", exist_ok=True)
    with open("output/eval_report.json", "w") as f:
        json.dump(scorecards, f, indent=2, default=str)
    print("\nFull scorecard written to output/eval_report.json")


def _print_card(card: dict) -> None:
    """Prints one competitor's scorecard, check by check."""
    print(f"  Overall: {card['overall_score']:.2f}")
    for check_name, result in card.get("checks", {}).items():
        status = "PASS" if result["pass"] else "FAIL"
        print(f"    [{status}] {check_name}: {result['score']:.2f} — {result['detail']}")


def _print_summary(scorecards: list[dict]) -> None:
    """Prints the cross-competitor summary at the end."""
    print(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for card in scorecards:
        print(f"  {card['competitor']}: {card['overall_score']:.2f}")

    valid = [c["overall_score"] for c in scorecards if "error" not in c]
    if valid:
        print(f"\n  Mean overall score: {sum(valid) / len(valid):.2f}")


if __name__ == "__main__":
    run_eval()
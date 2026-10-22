"""Read-only acceptance probe using recorded public material and a fake provider.

No HTTP requests, candidate edits, private fixtures, or independent scores.
Run: PYTHONPATH=src python3 outputs/acceptance-review-20261020/reproduce_review_boundary.py
"""
import copy
import json
from pathlib import Path

from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.feedback import make_feedback


class RecordedClaim:
    def __init__(self, checks):
        self.checks = checks
        self.calls = 0

    def complete_json(self, request):
        self.calls += 1
        return ModelResponse(json.dumps({
            "checks": self.checks,
            "failure_kind": "UNKNOWN",
            "rationale": "Synthetic incorrect claim: build success proves all obligations.",
        }), {})


def main():
    root = Path(__file__).resolve().parents[2]
    directory = root / "outputs/deepseek-optimized-pilot-v2-20261020/cases/R3-KPT-4673/rounds/0"
    plan = json.loads((directory / "public-review-plan.json").read_text())
    previous = json.loads((directory / "feedback.json").read_text())
    front = json.loads((directory / "front.trace.json").read_text())
    observations = previous["public_observations"]
    quote = '"status": "EXECUTION_PASS"'
    assert quote in observations[0]["excerpt"]
    results = []
    for explicit_unobservable in (False, True):
        tested_plan = copy.deepcopy(plan)
        checks = []
        for kind, prefix in [
            ("postconditions", ""),
            ("preserve_constraints", "preserve:"),
            ("open_obligations", "open:"),
        ]:
            for item in tested_plan.get(kind, []):
                if explicit_unobservable:
                    item["verification_kind"] = "NOT_PUBLICLY_OBSERVABLE"
                checks.append({
                    "id": prefix + item["id"], "status": "PASS",
                    "rationale": "Synthetic incorrect claim based only on build success.",
                    "evidence": [{"observation_id": observations[0]["id"], "quote": quote}],
                })
        provider = RecordedClaim(checks)
        review = ModelPublicReviewer(provider, len, {
            "model": "offline-fixture", "max_context_tokens": 1000000,
            "review_max_output_tokens": 1000, "max_format_retries": 0,
        }).review(front["requirement_spec"]["raw_text"], tested_plan, {
            "status": "EXECUTION_PASS",
            "observations": observations,
            "public_execution_capabilities": plan["public_execution_capabilities"],
        })
        identity = {k: previous[k] for k in [
            "case_id", "run_id", "input_digest", "round_id",
            "from_snapshot", "to_snapshot", "patch_digest",
        ]}
        feedback = make_feedback(
            **identity, execution_status="EXECUTION_PASS", observations=observations,
            plan=tested_plan, review=review,
        )
        results.append({
            "explicit_unobservable": explicit_unobservable,
            "fake_provider_calls": provider.calls,
            "review_decision": review["decision"],
            "feedback_semantic_outcome": feedback["semantic_outcome"],
            "pending_obligations": len(feedback["pending_obligations"]),
            "route": feedback["route"],
        })
    result = {
        "method_version": "0.4.7",
        "api_calls": 0,
        "private_materials_loaded": False,
        "source": str(directory),
        "observation_boundary": plan["public_execution_capabilities"]["environment_observation_limit"],
        "synthetic_claim": "All obligations pass because the public package build succeeded.",
        "results": results,
        "interpretation": (
            "Current-code regression using the original legacy public records; capability-unknown old evidence is not upgraded. "
            "The original recorded KPT review stayed UNKNOWN. Exact fresh quotes alone cannot discharge an untyped obligation."
        ),
    }
    destination = Path(__file__).with_name("review-boundary-probe.json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Capture actual Full production requests and selection for offline refactoring checks."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
from unittest.mock import patch

from contractedge.development import public_demo
from contractedge.model.provider import ModelResponse


def capture(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    signatures = {}
    factory = public_demo._provider
    for case in ("config-binding", "reload-state", "two-consumers", "malformed-once", "unknown-response"):
        target = root / case
        config = root / (case + ".config.json")
        config.write_text(json.dumps({"run_id": "c1-full-fixture", "candidate_guard_enabled": True,
                                      "max_format_retries": 1}))
        class Wrapped:
            def __init__(self, inner):
                self.inner, self.failed = inner, False
            def complete_json(self, request):
                if case == "unknown-response":
                    raise TimeoutError("fixture unknown dispatch")
                if case == "malformed-once" and request.prompt_version.startswith("diagnosis.") and not self.failed:
                    self.failed = True
                    return ModelResponse('{"malformed_diagnosis":true}', {"zero_cost": True})
                return self.inner.complete_json(request)
        def provider(config, example, runner):
            inner, counter = factory(config, example, runner)
            return Wrapped(inner), counter
        # Freeze only the in-process scheduling clock. Candidate subprocesses
        # still really execute; this avoids comparing incidental persistence time
        # as if it were a change to the final request's declared budget.
        with patch.object(public_demo, "_provider", provider), patch("time.monotonic", return_value=1000.0):
            summary = public_demo.run_demo(case if case in public_demo.CASES else "reload-state",
                                            target, provider_config=config)
        repair = target / "repair"
        trace = json.loads((repair / "repair.trace.json").read_text())
        calls = json.loads((repair / "model_calls.json").read_text())
        def normalize(value):
            if isinstance(value, dict):
                return {k: normalize(v) for k, v in value.items()}
            if isinstance(value, list):
                return [normalize(v) for v in value]
            if isinstance(value, str):
                return value.replace(str(root), "<FIXTURE_ROOT>")
            return value
        signatures[case] = normalize({
            "status": trace["status"],
            "actual_requests": [call["request"] for call in calls],
            "call_count": len(calls),
            "reserved_per_call": [[c["prompt_tokens_reserved"], c["output_tokens_reserved"]] for c in calls],
            "budget": {k: v for k, v in trace["budget"].items() if k != "elapsed_seconds"},
            "routes": [r["route"] for r in trace["rounds"]],
            "selected_snapshot": trace["final_snapshot_id"],
            "selected_patch_sha256": (trace["candidate"] or {}).get("patch_sha256"),
            "feedback": {k: trace["last_feedback"].get(k) for k in
                         ("route", "semantic_outcome", "failure_kind", "build_state", "pending_obligations")}
                        if trace["last_feedback"] else None,
        })
    (root / "signature.json").write_text(json.dumps(signatures, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({case: {k: v for k, v in value.items() if k in
                      {"status", "call_count", "routes", "selected_snapshot"}}
                      for case, value in signatures.items()}, sort_keys=True))


if __name__ == "__main__":
    capture(sys.argv[1])

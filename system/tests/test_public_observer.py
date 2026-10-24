"""Host unit tests execute the frozen trusted checker only, never a candidate."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from contractedge.contracts.validation import canonical_digest
from contractedge.execution import public_observer as observer_module


def public_scenarios():
    return {"scenarios": [
        {"id": "selected", "config": {"enabled": True},
         "warm_config": {"selected_name": "warm"}, "expected": {"name": "orion"}},
        {"id": "disabled", "config": {"enabled": False}, "expected": {"name": "default"}},
        {"id": "stable", "config": {"enabled": True}, "expected": {"mode": "stable"}},
    ]}


def actual_output(*, selected="orion"):
    return {"schema_version": "ce.public_process_outputs.v1", "results": [
        {"id": "selected", "actual": {"name": selected, "mode": "stable"}},
        {"id": "disabled", "actual": {"name": "default", "mode": "stable"}},
        {"id": "stable", "actual": {"name": "orion", "mode": "stable"}},
    ]}


def run_trusted_checker(directory, public_spec, raw_output, *, code=None):
    """Only paths change; the assertion/parser code is the production checker."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    spec = directory / "spec.json"
    output = directory / "stdout.bin"
    spec.write_text(json.dumps(public_spec), encoding="utf-8")
    output.write_bytes(raw_output if isinstance(raw_output, bytes) else raw_output.encode("utf-8"))
    script = observer_module._OBSERVER if code is None else code
    script = script.replace("'/checks/spec.json'", repr(str(spec)))
    script = script.replace("'/observations/stdout.bin'", repr(str(output)))
    if "/checks/spec.json" in script or "/observations/stdout.bin" in script:
        raise AssertionError("Frozen checker mount paths were not localized")
    return subprocess.run([sys.executable, "-I", "-B", "-c", script], cwd=directory,
                          capture_output=True, text=True, timeout=5, check=False)


class TrustedCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ce-public-checker-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = {**public_scenarios(), "snapshot_id": "S0", "input_digest": "a" * 64}
        self.counter = 0

    def checker(self, body, *, spec=None):
        self.counter += 1
        raw = json.dumps(body) if isinstance(body, dict) else body
        return run_trusted_checker(self.root / str(self.counter), self.spec if spec is None else spec, raw)

    def test_actual_public_values_are_checked_by_production_checker(self):
        result = self.checker(actual_output())
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["snapshot_id"], "S0")
        self.assertEqual({row["status"] for row in body["checks"]}, {"PASS"})

    def test_candidate_self_reported_pass_cannot_replace_actual_values(self):
        for body in (
            {"schema_version": "ce.public_process_outputs.v1", "status": "PASS"},
            {"schema_version": "ce.public_process_outputs.v1", "results":
                [{"id": item, "status": "PASS"} for item in ("selected", "disabled", "stable")]},
            {"status": "PASS", "checks": [{"id": "selected", "status": "PASS"}]},
        ):
            with self.subTest(body=body):
                self.assertNotEqual(self.checker(body).returncode, 0)

    def test_wrong_actual_value_fails_even_if_candidate_embeds_pass(self):
        body = actual_output(selected="wrong")
        for row in body["results"]:
            row["actual"]["status"] = "PASS"
        result = self.checker(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        checks = {row["id"]: row["status"] for row in json.loads(result.stdout)["checks"]}
        self.assertEqual(checks["selected"], "FAIL")

    def test_duplicate_top_level_or_nested_keys_are_rejected(self):
        valid = json.dumps(actual_output())
        nested = valid.replace('"name": "orion"', '"name": "wrong", "name": "orion"', 1)
        top = valid[:-1] + ', "results":[]}'
        for raw in (nested, top):
            with self.subTest(raw=raw):
                self.assertNotEqual(self.checker(raw).returncode, 0)

    def test_duplicate_missing_extra_or_wrong_result_ids_cannot_pass(self):
        variants = []
        body = actual_output()
        body["results"][1]["id"] = "selected"
        variants.append(body)
        body = actual_output()
        body["results"].pop()
        variants.append(body)
        body = actual_output()
        body["results"].append({"id": "extra", "actual": {"name": "orion", "mode": "stable"}})
        variants.append(body)
        body = actual_output()
        body["results"][0]["id"] = "other"
        variants.append(body)
        for body in variants:
            with self.subTest(body=body):
                self.assertNotEqual(self.checker(body).returncode, 0)

    def test_missing_expected_public_output_field_cannot_pass(self):
        body = actual_output()
        del body["results"][0]["actual"]["name"]
        self.assertNotEqual(self.checker(body).returncode, 0)

    def test_missing_expected_assertion_in_spec_cannot_pass(self):
        spec = copy.deepcopy(self.spec)
        del spec["scenarios"][0]["expected"]
        self.assertNotEqual(self.checker(actual_output(), spec=spec).returncode, 0)


class PublicInputProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ce-public-input-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "config.yaml").write_text(
            "selected_name: orion\ndefault_name: default\nbase_flag: true\n", encoding="utf-8")
        self.inputs = self.root / "inputs.json"

    def test_expected_values_are_not_in_candidate_requests_and_warm_config_is_preserved(self):
        body = public_scenarios()
        body["scenarios"][0]["expected"] = {"name": "expected-only-marker"}
        self.inputs.write_text(json.dumps(body))
        public, candidate = observer_module._requests(self.source, self.inputs)
        self.assertEqual(public, body)
        self.assertNotIn("expected-only-marker", json.dumps(candidate))
        for request in candidate["requests"]:
            self.assertNotIn("expected", request)
        warm = next(row for row in candidate["requests"] if row["id"] == "selected")["warm_config"]
        self.assertEqual(warm["selected_name"], "warm")
        self.assertEqual(warm["default_name"], "default")
        self.assertIs(warm["base_flag"], True)

    def test_missing_or_empty_expected_assertion_is_rejected(self):
        for empty in (None, {}):
            body = public_scenarios()
            if empty is None:
                del body["scenarios"][0]["expected"]
            else:
                body["scenarios"][0]["expected"] = empty
            self.inputs.write_text(json.dumps(body))
            with self.subTest(empty=empty), self.assertRaises(ValueError):
                observer_module._requests(self.source, self.inputs)

    def test_duplicate_scenario_ids_are_rejected(self):
        body = public_scenarios()
        body["scenarios"][1]["id"] = "selected"
        self.inputs.write_text(json.dumps(body))
        with self.assertRaises(ValueError):
            observer_module._requests(self.source, self.inputs)


class _CheckerOnlySession:
    """Fixture runtime supplies bytes; it never executes the candidate invocation."""
    def __init__(self, root, raw_output):
        self.root = Path(root)
        self.raw_output = raw_output
        self.candidate_calls = 0

    def run_candidate(self, invocation, **kwargs):
        if invocation.argv[-1] != observer_module._CANDIDATE:
            raise AssertionError("Unexpected candidate bridge")
        self.candidate_calls += 1
        self.root.mkdir(parents=True)
        raw_path = self.root / "candidate-stdout.bin"
        raw_path.write_text(self.raw_output, encoding="utf-8")
        return {"status": "EXECUTION_PASS", "stdout_path": str(raw_path)}

    def run_observer(self, invocation, *, bounded_output, public_check_spec):
        if invocation.argv[-1] != observer_module._OBSERVER:
            raise AssertionError("Expected the actual production trusted checker")
        result = run_trusted_checker(self.root / "trusted", json.loads(Path(public_check_spec).read_text()),
            Path(bounded_output["stdout_path"]).read_bytes(), code=invocation.argv[-1])
        stdout = self.root / "trusted-checks.stdout"
        stdout.write_text(result.stdout, encoding="utf-8")
        return {"status": "EXECUTION_PASS" if result.returncode == 0 else "EXECUTION_FAILED",
                "stdout_path": str(stdout)}

    def close(self):
        return {"status": "CLEANUP_CONFIRMED", "fixture_only": True}


class RequiredPublicProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ce-public-profile-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        # A host import or execution of this file would fail; only trusted checker code runs.
        (self.source / "app.py").write_text('raise AssertionError("candidate must never execute in host unit tests")\n')
        (self.source / "config.yaml").write_text("selected_name: orion\ndefault_name: default\n")
        self.inputs = self.root / "public-inputs.json"
        self.inputs.write_text(json.dumps(public_scenarios()))
        self.counter = 0

    def observation(self, *, selected="orion"):
        self.counter += 1
        fixture = _CheckerOnlySession(self.root / ("fixture-" + str(self.counter)),
                                      json.dumps(actual_output(selected=selected)))
        observer = observer_module.IsolatedPublicObserver(image="sha256:" + "a" * 64,
            docker_host="unix:///not-used-by-host-unit.sock")
        with patch.object(observer_module.RuntimeSession, "open", return_value=fixture):
            report = observer.observe(self.source, self.inputs, self.root / ("report-" + str(self.counter)))
        self.assertEqual(fixture.candidate_calls, 1)
        self.assertEqual(report["status"], "EXECUTION_PASS", report["errors"])
        return report

    def assert_cannot_pass(self, observation):
        try:
            result = observer_module.enforce_public_profile({"decision": "PASS", "rationale": "model says yes"}, observation)
        except ValueError:
            return  # Explicit rejection is fail-closed too.
        self.assertNotEqual(result.get("decision"), "PASS", result)

    def test_real_checker_profile_can_pass_but_cannot_override_model_failure(self):
        report = self.observation()
        result = observer_module.enforce_public_profile({"decision": "PASS"}, report)
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(observer_module.enforce_public_profile({"decision": "FAIL"}, report)["decision"], "FAIL")

    def test_model_pass_cannot_override_fixed_public_failure_or_unknown(self):
        report = self.observation(selected="wrong")
        result = observer_module.enforce_public_profile({"decision": "PASS"}, report)
        self.assertEqual(result["decision"], "FAIL")
        report = self.observation()
        report["required_public_profile"]["status"] = "INCOMPLETE"
        self.assert_cannot_pass(report)
        report = self.observation()
        report["required_public_profile"]["checks"][0]["status"] = "UNKNOWN"
        self.assert_cannot_pass(report)

    def test_profile_identity_and_declared_check_ids_cannot_be_tampered(self):
        original = self.observation()
        mutations = [
            ("snapshot", lambda r: r["required_public_profile"].update(snapshot_id="STALE")),
            ("input", lambda r: r["required_public_profile"].update(input_digest="b" * 64)),
            ("observer", lambda r: r["required_public_profile"].update(observer_sha256="b" * 64)),
            ("observation-digest", lambda r: r["required_public_profile"].update(observations_digest="b" * 64)),
            ("declared-check-ids", lambda r: r["required_public_profile"].update(check_ids=["selected"])),
            ("runtime-status", lambda r: r.update(status="INFRA_ERROR")),
        ]
        for name, mutate in mutations:
            report = copy.deepcopy(original)
            mutate(report)
            with self.subTest(name=name):
                self.assert_cannot_pass(report)

    def test_profile_status_cannot_be_rewritten_over_actual_checker_failure(self):
        report = self.observation(selected="wrong")
        for check in report["required_public_profile"]["checks"]:
            check["status"] = "PASS"
        self.assert_cannot_pass(report)

    def test_profile_check_expected_actual_and_ids_are_bound_to_observations(self):
        original = self.observation()
        mutations = [
            ("missing-expected", lambda c: c.pop("expected")),
            ("changed-expected", lambda c: c.update(expected={"name": "different"})),
            ("changed-actual", lambda c: c.update(actual={"name": "different"})),
            ("changed-id", lambda c: c.update(id="other")),
        ]
        for name, mutate in mutations:
            report = copy.deepcopy(original)
            mutate(report["required_public_profile"]["checks"][0])
            with self.subTest(name=name):
                self.assert_cannot_pass(report)

    def test_rehashed_observation_identity_and_content_tampering_cannot_pass(self):
        original = self.observation()
        mutations = [
            ("missing-observation", lambda r: r["observations"].pop()),
            ("duplicate-observation", lambda r: r["observations"].append(copy.deepcopy(r["observations"][0]))),
            ("stale-observation", lambda r: r["observations"][0].update(snapshot_id="STALE")),
            ("wrong-content-digest", lambda r: r["observations"][0].update(content_digest="b" * 64)),
            ("wrong-probe", lambda r: r["observations"][0]["behavior_check"].update(probe_sha256="b" * 64)),
            ("status-conflict", lambda r: r["observations"][0]["behavior_check"].update(status="FAIL")),
            ("duplicate-check-id", lambda r: r["observations"][0]["behavior_check"].update(
                id=r["observations"][1]["behavior_check"]["id"])),
        ]
        for name, mutate in mutations:
            report = copy.deepcopy(original)
            mutate(report)
            report["required_public_profile"]["observations_digest"] = canonical_digest(report["observations"])
            with self.subTest(name=name):
                self.assert_cannot_pass(report)


if __name__ == "__main__":
    unittest.main()

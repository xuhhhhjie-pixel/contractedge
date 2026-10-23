"""Freeze checks fail on real byte/file-set drift without external fixtures."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_development_snapshot.py"
spec = importlib.util.spec_from_file_location("development_snapshot_verifier", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def frozen(tmp_path):
    root = tmp_path / "system"
    contents = {"src/contractedge/a.py": "VALUE = 1\n", "src/contractedge/schema.json": '{}\n',
                "configs/fixture.json": '{"provider":"fixture"}\n', "tests/test_fixed.py": "def test_public(): pass\n",
                "scripts/public_tool.py": "print('fixture')\n"}
    for name, body in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    source = {p.relative_to(root / "src").as_posix(): sha(p) for p in (root / "src").rglob("*") if p.is_file()}
    configuration = {"stage_configuration_files": {"configs/fixture.json": sha(root / "configs/fixture.json")}, "real_model_http_calls": 0}
    panel = ["tests/test_fixed.py"]
    tools = {"scripts/public_tool.py": sha(root / "scripts/public_tool.py")}
    workflow = root.parent / ".github/workflows/development.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: Development\n")
    repository = {".github/workflows/development.yml": sha(workflow)}
    data = {"schema_version": "ce.development_snapshot.v1", "development_snapshot_id": "fixture-current",
            "formal_ready": False, "files": source, "source_sha256": verifier.canonical_digest(source),
            "configuration": configuration, "configuration_sha256": verifier.canonical_digest(configuration),
            "fixture_panel": panel, "fixture_panel_sha256": verifier.canonical_digest(panel),
            "fixture_files": {name: sha(root / name) for name in panel},
            "tooling_files": tools, "tooling_files_sha256": verifier.canonical_digest(tools),
            "repository_files": repository, "repository_files_sha256": verifier.canonical_digest(repository)}
    return root, data


def test_complete_freeze_and_current_status_cli(frozen):
    root, data = frozen
    assert verifier.verify_snapshot(root, data)["status"] == "PASS"
    (root / "current.json").write_text(json.dumps(data))
    (root / "CURRENT_STATUS.json").write_text(json.dumps({"current_development": {"snapshot": "current.json"}}))
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["coverage"] == {"source": 2, "fixture": 1, "configuration": 1, "tooling_files": 1, "repository_files": 1}


@pytest.mark.parametrize("name", ["src/contractedge/a.py", "src/contractedge/schema.json", "configs/fixture.json",
                                   "tests/test_fixed.py", "scripts/public_tool.py", "../.github/workflows/development.yml"])
@pytest.mark.parametrize("change", ["modify", "remove"])
def test_rejects_modified_or_missing_frozen_file(frozen, name, change):
    root, data = frozen
    if change == "modify": (root / name).write_text("tampered\n")
    else: (root / name).unlink()
    report = verifier.verify_snapshot(root, data)
    assert report["status"] == "FAIL"
    assert any("Modified" in error or "Missing" in error for error in report["errors"])


@pytest.mark.parametrize("name", ["added.py", "schema/added.json"])
def test_rejects_extra_source_file(frozen, name):
    root, data = frozen
    path = root / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    assert f"Extra source file: {name}" in verifier.verify_snapshot(root, data)["errors"]


@pytest.mark.parametrize("field", ["source_sha256", "configuration_sha256", "fixture_panel_sha256", "tooling_files_sha256", "repository_files_sha256"])
def test_rejects_changed_digest(frozen, field):
    root, data = frozen
    data[field] = "0" * 64
    assert f"Digest mismatch: {field}" in verifier.verify_snapshot(root, data)["errors"]


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_panel_hash_map_must_bind_exact_panel(frozen, change):
    root, data = frozen
    if change == "missing":
        data["fixture_panel"].append("tests/not-bound.py")
    else:
        data["fixture_files"]["scripts/public_tool.py"] = sha(root / "scripts/public_tool.py")
    data["fixture_panel_sha256"] = verifier.canonical_digest(data["fixture_panel"])
    assert any("exactly fixture_panel" in error for error in verifier.verify_snapshot(root, data)["errors"])


def test_existing_v1_aliases_and_explicit_legacy_scope(frozen):
    root, data = frozen
    data["fixture_configuration"] = data.pop("configuration")
    data["tool_files"] = data.pop("tooling_files")
    data["tool_files_sha256"] = data.pop("tooling_files_sha256")
    assert verifier.verify_snapshot(root, data)["status"] == "PASS"
    data.pop("fixture_files")
    assert verifier.verify_snapshot(root, data)["status"] == "FAIL"
    report = verifier.verify_snapshot(root, data, allow_legacy_unbound=True)
    assert report["status"] == "LEGACY_UNBOUND" and report["unbound_legacy_fixture_files"] == data["fixture_panel"]


@pytest.mark.parametrize("name", ["../outside.py", "/outside.py", "a/../outside.py", "a\\outside.py", "a//outside.py", "C:/outside.py"])
def test_untrusted_path_cannot_escape_source_root(frozen, name):
    root, data = frozen
    data["files"] = {name: "0" * 64}
    assert any("Invalid relative path" in error for error in verifier.verify_snapshot(root, data)["errors"])


def test_symlink_source_cannot_bypass_freeze(frozen):
    root, data = frozen
    target = root / "src/contractedge/a.py"
    body = target.read_text()
    target.unlink()
    outside = root.parent / "outside.py"
    outside.write_text(body)
    try: target.symlink_to(outside)
    except (OSError, NotImplementedError): pytest.skip("Host cannot create symlinks")
    assert any("Symlink" in error for error in verifier.verify_snapshot(root, data)["errors"])


def test_wrong_schema_or_formal_ready_claim_fails(frozen):
    root, data = frozen
    for field, value in [("schema_version", "unknown"), ("formal_ready", True)]:
        changed = deepcopy(data)
        changed[field] = value
        assert verifier.verify_snapshot(root, changed)["status"] == "FAIL"


def test_cli_fails_on_stale_freeze(frozen):
    root, data = frozen
    (root / "frozen.json").write_text(json.dumps(data))
    (root / "src/extra.py").write_text("added")
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--snapshot", "frozen.json"], capture_output=True, text=True)
    assert result.returncode == 1 and json.loads(result.stdout)["status"] == "FAIL"


def test_conflicting_tool_file_aliases_are_rejected(frozen):
    root, data = frozen
    data["tool_files"] = dict(data["tooling_files"])
    assert any("Conflicting" in error for error in verifier.verify_snapshot(root, data)["errors"])


def test_extra_source_symlink_directory_is_rejected(frozen):
    root, data = frozen
    outside = root.parent / "extra-source"
    outside.mkdir()
    (outside / "unexpected.py").write_text("raise AssertionError('not frozen')")
    try: (root / "src/extra").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError): pytest.skip("Host cannot create symlinks")
    assert any("Symlink" in error for error in verifier.verify_snapshot(root, data)["errors"])


@pytest.mark.parametrize("change", ["configuration_files", "tools", "tool_digest"])
def test_current_snapshot_requires_file_bindings(frozen, change):
    root, data = frozen
    if change == "configuration_files":
        data["configuration"].pop("stage_configuration_files")
        data["configuration_sha256"] = verifier.canonical_digest(data["configuration"])
    elif change == "tools":
        data.pop("tooling_files")
    else:
        data.pop("tooling_files_sha256")
    assert verifier.verify_snapshot(root, data, require_current_bindings=True)["status"] == "FAIL"


def test_legacy_inspection_cannot_relax_the_default_current_entry(frozen):
    root, data = frozen
    (root / "current.json").write_text(json.dumps(data))
    (root / "CURRENT_STATUS.json").write_text(json.dumps({"current_development": {"snapshot": "current.json"}}))
    result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), "--allow-legacy-unbound"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "explicit --snapshot" in json.loads(result.stdout)["errors"][0]

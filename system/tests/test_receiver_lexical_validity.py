"""Receiver declaration locations must name Go code, not documentation text."""

from pathlib import Path

import pytest

from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def make_source(root: Path, kind: str, *, actual_owner: bool):
    text = "type worker struct { fake bool }"
    documentation = (
        "const example = `\n" + text + "\n`\n"
        if kind == "raw_string" else "/*\n" + text + "\n*/\n"
    )
    (root / "go.mod").write_text("module example.org/check\n")
    (root / "dispatch.go").write_text(
        "package check\nfunc (w *worker) Dispatch() bool { return true }\n"
    )
    (root / "worker.go").write_text("package check\n" + documentation)
    if actual_owner:
        (root / "types.go").write_text("package check\ntype worker struct { actual bool }\n")
    snapshot = compute_snapshot(root)
    seed = parse_source_units(
        "dispatch.go", (root / "dispatch.go").read_text(), snapshot.snapshot_id
    )
    return snapshot, seed


def locate(snapshot, seed, index):
    return locate_gap_roles(snapshot, [{
        "id": "G1",
        "summary": "Locate worker configuration carrier",
        "locator_hint": {"path": "dispatch.go", "symbol": "Dispatch"},
    }], {"max_new_files": 4, "seed_units": seed, "seed_declarations": index})


@pytest.mark.parametrize("kind", ["raw_string", "block_comment"])
@pytest.mark.parametrize("indexed", [False, True])
def test_documented_receiver_is_not_delivered_as_a_declaration(tmp_path, kind, indexed):
    snapshot, seed = make_source(tmp_path, kind, actual_owner=False)
    index = list(seed)
    if indexed:
        # The legacy lexical parser can index this documentation. The locator
        # must still verify the declaration against actual code on fresh read.
        index.extend(parse_source_units(
            "worker.go", (tmp_path / "worker.go").read_text(), snapshot.snapshot_id
        ))
    roles, audit = locate(snapshot, seed, index)
    assert not any(u.get("symbol") == "worker" for u in roles), audit
    assert not any(r.get("receiver_type") == "worker" and r.get("status") == "FOUND"
                   for r in audit["records"]), audit


@pytest.mark.parametrize("kind", ["raw_string", "block_comment"])
def test_documentation_does_not_create_false_ambiguity_for_real_receiver(tmp_path, kind):
    snapshot, seed = make_source(tmp_path, kind, actual_owner=True)
    roles, audit = locate(snapshot, seed, seed)
    owners = [u for u in roles if u.get("symbol") == "worker"]
    assert len(owners) == 1 and owners[0]["path"] == "types.go", audit
    assert not any(r.get("status") == "RECEIVER_DECLARATION_AMBIGUOUS"
                   for r in audit["records"]), audit

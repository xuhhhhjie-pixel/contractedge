"""Public synthetic locator counterexamples; no model or request construction.

Run against a chosen source tree using PYTHONPATH. The first two tests preserve
the original red counterexamples from the frozen v4 implementation.
"""

from pathlib import Path

import pytest

from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def fixture(tmp_path: Path, files: dict[str, str]):
    for name, content in {"go.mod": "module example.com/toy\n", **files}.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return compute_snapshot(tmp_path)


def declarations(snapshot, path):
    return parse_source_units(
        path,
        (Path(snapshot.source_root) / path).read_text(),
        snapshot.snapshot_id,
        max_lines=768,
        max_chars=32000,
    )


def gap(path, search_term="BindingTimeout"):
    return [{
        "id": "G1",
        "summary": "The binding timeout configuration carrier is not supplied.",
        "locator_hint": {
            "path": path,
            "symbol": "Dispatch",
            "search_term": search_term,
        },
    }]


def receiver_fixture(tmp_path):
    endpoint = "pkg/worker/dispatch.go"
    snapshot = fixture(tmp_path, {
        endpoint: """package worker

func (w *worker) Dispatch() bool {
    return w.enabled
}
""",
        "pkg/worker/worker.go": """package worker

type worker struct {
    enabled bool
}

func New(enabled bool) *worker {
    return &worker{enabled: enabled}
}
""",
    })
    return snapshot, endpoint


def test_same_package_receiver_can_be_located_without_seed_index(tmp_path):
    snapshot, endpoint = receiver_fixture(tmp_path)
    seed = declarations(snapshot, endpoint)
    roles, audit = locate_gap_roles(snapshot, gap(endpoint), {
        "max_new_files": 4,
        "seed_units": seed,
        "seed_declarations": seed,
    })
    assert any(
        u.get("symbol") == "worker"
        and u["path"] == "pkg/worker/worker.go"
        for u in roles
    ), audit


def test_unrelated_function_cannot_spend_endpoint_connector_budget(tmp_path):
    endpoint = "pkg/worker/dispatch.go"
    snapshot = fixture(tmp_path, {
        endpoint: """package worker

import api "example.com/toy/pkg/client"

func Dispatch() bool {
    return true
}

func unrelated() {
    client := api.NewClient()
    _ = client.ListBindings()
}
""",
        "pkg/client/binding.go": "package client\nfunc BindingType() {}\n",
        "pkg/client/a.go": "package client\nfunc A() {}\n",
        "pkg/client/b.go": "package client\nfunc B() {}\n",
        "pkg/client/client.go": """package client
type Client struct {}
func NewClient() *Client { return &Client{} }
func (c *Client) ListBindings() []string { return nil }
""",
    })
    seed = declarations(snapshot, endpoint)
    _, audit = locate_gap_roles(snapshot, gap(endpoint), {
        "max_new_files": 4,
        "seed_units": seed,
        "seed_declarations": seed,
    })
    assert audit["inspected_files"] == 1, audit


def test_explicit_method_in_endpoint_keeps_connector(tmp_path):
    endpoint = "pkg/worker/dispatch.go"
    snapshot = fixture(tmp_path, {
        endpoint: """package worker

import api "example.com/toy/pkg/client"

func Dispatch() {
    client := api.NewClient()
    _ = client.ListBindings()
}
""",
        "pkg/client/client.go": """package client
type Client struct {}
func NewClient() *Client { return &Client{} }
func (c *Client) ListBindings() []string { return nil }
""",
    })
    seed = declarations(snapshot, endpoint)
    roles, audit = locate_gap_roles(snapshot, gap(endpoint, "client.ListBindings"), {
        "max_new_files": 4,
        "seed_units": seed,
        "seed_declarations": seed,
    })
    assert any(u.get("symbol") == "ListBindings" for u in roles), audit


def test_known_same_package_type_ambiguity_is_not_resolved_by_filename(tmp_path):
    snapshot, endpoint = receiver_fixture(tmp_path)
    other = Path(snapshot.source_root) / "pkg/worker/other.go"
    other.write_text("package worker\ntype worker struct { other bool }\n")
    snapshot = compute_snapshot(tmp_path)
    seed = declarations(snapshot, endpoint)
    index = [
        *seed,
        *declarations(snapshot, "pkg/worker/worker.go"),
        *declarations(snapshot, "pkg/worker/other.go"),
    ]
    roles, audit = locate_gap_roles(snapshot, gap(endpoint), {
        "max_new_files": 4,
        "seed_units": seed,
        "seed_declarations": index,
    })
    assert any(r.get("status") == "RECEIVER_DECLARATION_AMBIGUOUS"
               for r in audit["records"]), audit
    assert not any(u.get("symbol") == "worker" for u in roles), roles


def test_receiver_lookup_respects_shared_file_budget(tmp_path):
    snapshot, endpoint = receiver_fixture(tmp_path)
    seed = declarations(snapshot, endpoint)
    _, audit = locate_gap_roles(snapshot, gap(endpoint), {
        "max_new_files": 1,
        "seed_units": seed,
        "seed_declarations": seed,
    })
    assert audit["inspected_files"] <= 1, audit


def test_unindexed_same_package_type_ambiguity_is_preserved(tmp_path):
    snapshot, endpoint = receiver_fixture(tmp_path)
    (tmp_path / "pkg/worker/other.go").write_text(
        "package worker\ntype worker struct { other bool }\n")
    snapshot = compute_snapshot(tmp_path)
    seed = declarations(snapshot, endpoint)
    roles, audit = locate_gap_roles(snapshot, gap(endpoint), {
        "max_new_files": 4,
        "seed_units": seed,
        "seed_declarations": seed,
    })
    assert any(r.get("status") == "RECEIVER_DECLARATION_AMBIGUOUS"
               for r in audit["records"]), audit
    owners = [u for u in roles if u.get("symbol") == "worker"]
    assert len(owners) == 2 and all(u.get("optional_gap_context") for u in owners), roles
    assert all("SAME_PACKAGE_RECEIVER_DECLARATION" not in u.get("association_status", "")
               for u in owners), roles
    assert audit["inspected_files"] == 3, audit


@pytest.mark.parametrize("budget_kind", ["files", "bytes"])
def test_found_receiver_is_not_unique_when_package_scan_is_incomplete(tmp_path, budget_kind):
    snapshot, endpoint = receiver_fixture(tmp_path)
    # worker.go sorts before this file; a first-match implementation would
    # wrongly publish a unique receiver before seeing the second declaration.
    (tmp_path / "pkg/worker/z_other.go").write_text(
        "package worker\ntype worker struct { other bool }\n")
    snapshot = compute_snapshot(tmp_path)
    seed = declarations(snapshot, endpoint)
    limits = {"max_new_files": 4, "seed_units": seed, "seed_declarations": seed}
    if budget_kind == "files":
        limits["max_new_files"] = 2
    else:
        limits["max_bytes"] = sum((tmp_path / p).stat().st_size for p in (
            endpoint, "pkg/worker/worker.go"))
    roles, audit = locate_gap_roles(snapshot, gap(endpoint), limits)
    owners = [u for u in roles if u.get("symbol") == "worker"]
    assert len(owners) == 1 and owners[0].get("optional_gap_context"), roles
    assert "SAME_PACKAGE_RECEIVER_DECLARATION" not in owners[0].get("association_status", "")
    assert any(r.get("status") == "RECEIVER_DECLARATION_UNRESOLVED"
               for r in audit["records"]), audit
    assert audit["inspected_files"] == 2, audit
    if budget_kind == "bytes":
        assert audit["inspected_bytes"] <= limits["max_bytes"], audit


def test_receiver_filename_is_only_a_read_priority(tmp_path):
    endpoint = "pkg/worker/dispatch.go"
    snapshot = fixture(tmp_path, {
        endpoint: "package worker\nfunc (w *worker) Dispatch() bool { return w.enabled }\n",
        "pkg/worker/worker.go": "package worker\ntype decoy struct { enabled bool }\n",
        "pkg/worker/actual.go": "package worker\ntype worker struct { enabled bool }\n",
    })
    seed = declarations(snapshot, endpoint)
    roles, audit = locate_gap_roles(snapshot, gap(endpoint), {
        "max_new_files": 4, "seed_units": seed, "seed_declarations": seed,
    })
    owners = [u for u in roles if u.get("symbol") == "worker"]
    assert len(owners) == 1 and owners[0]["path"] == "pkg/worker/actual.go", audit


def test_receiver_prefix_cannot_reintroduce_unrelated_connector(tmp_path):
    endpoint = "pkg/worker/dispatch.go"
    snapshot = fixture(tmp_path, {
        endpoint: "package worker\nfunc (w *worker) Dispatch() bool { return w.enabled }\n",
        "pkg/worker/worker.go": """package worker
import api "example.com/toy/pkg/client"

func unrelated() {
    client := api.NewClient()
    _ = client.ListBindings()
}

type worker struct { enabled bool }
""",
        "pkg/client/client.go": """package client
type Client struct {}
func NewClient() *Client { return &Client{} }
func (c *Client) ListBindings() []string { return nil }
""",
    })
    seed = declarations(snapshot, endpoint)
    roles, audit = locate_gap_roles(snapshot, gap(endpoint), {
        "max_new_files": 4, "seed_units": seed, "seed_declarations": seed,
    })
    assert any(u.get("symbol") == "worker" for u in roles), audit
    assert not any(u.get("symbol") == "ListBindings" for u in roles), roles
    assert audit["inspected_files"] == 2, audit


@pytest.mark.parametrize("unread_duplicate", [False, True])
def test_large_package_keeps_declaration_fact_without_unique_binding(tmp_path, unread_duplicate):
    from types import SimpleNamespace

    from contractedge.diagnosis.evidence import build_evidence_catalog
    from contractedge.retrieval.candidates import (
        build_candidate_set, extend_once, requires_gap_delivery,
    )

    endpoint = "pkg/worker/dispatch.go"
    snapshot = fixture(tmp_path, {
        endpoint: "package worker\nfunc (w *worker) Dispatch() { w.queue.Publish() }\n",
        "pkg/worker/worker.go": """package worker
import bus "example.com/toy/pkg/bus"
type worker struct { queue bus.Queue }
""",
        "pkg/worker/a.go": "package worker\ntype A struct {}\n",
        "pkg/worker/b.go": "package worker\ntype B struct {}\n",
        "pkg/worker/z.go": ("package worker\ntype worker struct { other bool }\n"
                            if unread_duplicate else "package worker\ntype Z struct {}\n"),
        "pkg/bus/bus.go": "package bus\ntype Queue interface { Publish() }\n",
    })
    seed = declarations(snapshot, endpoint)
    gaps = gap(endpoint, "queue.Publish")
    roles, audit = locate_gap_roles(snapshot, gaps, {
        "max_new_files": 4, "seed_units": seed, "seed_declarations": seed,
    })
    owners = [u for u in roles if u.get("symbol") == "worker"]
    assert len(owners) == 1 and owners[0]["text"] == "type worker struct { queue bus.Queue }\n"
    owner = owners[0]
    assert owner.get("optional_gap_context") and not requires_gap_delivery(owner)
    assert "SAME_PACKAGE_RECEIVER_DECLARATION" not in owner["association_status"]
    assert any(r.get("status") == "RECEIVER_DECLARATION_UNRESOLVED"
               for r in audit["records"]), audit
    assert any(r.get("status") == "FIELD_CONTRACT_OWNER_UNRESOLVED"
               for r in audit["records"]), audit
    assert not any(r.get("status") == "FIELD_CONTRACT_FOUND" for r in audit["records"])
    assert not any(u["path"] == "pkg/bus/bus.go" for u in roles)
    assert audit["inspected_files"] == 4 and audit["truncated"]

    # Use the existing selector and catalog, not a new delivery mechanism or a
    # ModelRequest. A real source fact can reach the catalog without becoming a
    # required endpoint or a uniquely bound receiver contract.
    initial = build_candidate_set("worker queue", None, seed, [], top_n=12,
                                  max_context_tokens=10000, token_counter=len)
    selected = extend_once(initial, gaps, roles, max_context_tokens=10000, token_counter=len)
    assert owner["id"] in selected.selected_ids
    assert owner["id"] not in selected.required_endpoint_ids
    selected_owner = next(u for u in selected.candidates if u["id"] == owner["id"])
    assert not requires_gap_delivery(selected_owner)
    catalog = build_evidence_catalog(SimpleNamespace(declared_artifacts=[], public_evidence=[]),
                                     selected, snapshot.snapshot_id)
    delivered = next(e for e in catalog if e["id"] == "SRC-" + owner["id"])
    assert delivered["excerpt"] == owner["text"]
    assert delivered["content_digest"] == owner["content_digest"]


def test_receiver_facts_share_existing_optional_callee_slots(tmp_path):
    endpoint = "pkg/worker/dispatch.go"
    snapshot = fixture(tmp_path, {
        endpoint: """package worker
func (w *worker) Dispatch() { w.first(); w.second() }
func (w *worker) first() { doFirst() }
func (w *worker) second() { doSecond() }
""",
        "pkg/worker/worker.go": "package worker\ntype worker struct { enabled bool }\n",
        "pkg/worker/a.go": "package worker\ntype A struct {}\n",
        "pkg/worker/b.go": "package worker\ntype B struct {}\n",
        "pkg/worker/z.go": "package worker\ntype Z struct {}\n",
    })
    # Only the actual endpoint is initially supplied; the existing optional
    # callee lookup will discover both helpers in the already-read file.
    seed = [u for u in declarations(snapshot, endpoint) if u.get("symbol") == "Dispatch"]
    roles, audit = locate_gap_roles(snapshot, gap(endpoint), {
        "max_new_files": 4, "seed_units": seed, "seed_declarations": seed,
    })
    optional = [u for u in roles if u.get("same_receiver_callee_support")
                or u.get("optional_gap_context")]
    assert {u.get("symbol") for u in optional} == {"first", "second"}, roles
    assert len(optional) == 2
    assert not any(u.get("symbol") == "worker" for u in roles)
    assert any(r.get("status") == "RECEIVER_DECLARATION_UNRESOLVED"
               for r in audit["records"]), audit

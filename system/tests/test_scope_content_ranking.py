"""Path matches must not cap implementation relevance below content matches."""
import pytest

from contractedge.source.snapshot import compute_snapshot
from contractedge.source.scopes import resolve_scope


QUERY = "controller issuer private key stale"


def scope_fixture(root, suffix="go"):
    (root / "controller").mkdir()
    (root / f"controller/issuer.{suffix}").write_text(
        "// controller issuer private key stale\n"
    )
    (root / f"misc.{suffix}").write_text("// controller issuer private key\n")
    return compute_snapshot(root)


@pytest.mark.parametrize("suffix", ["go", "py", "ts"])
def test_path_hit_file_is_scored_using_content_before_match_cap(tmp_path, suffix):
    snapshot = scope_fixture(tmp_path, suffix)
    scope = resolve_scope(
        tmp_path, snapshot, [{"kind": "concept", "value": QUERY}],
        max_matches_per_hint=1,
    )
    production = f"controller/issuer.{suffix}"
    assert scope.seed_paths == (production,)
    audit = scope.search_audit[0]
    assert audit["matched_scores"][production] == 5
    assert audit["indexed_files_total"] == 2


@pytest.mark.parametrize("budget", ["files", "bytes"])
def test_path_content_scoring_respects_existing_shared_read_budget(tmp_path, budget):
    snapshot = scope_fixture(tmp_path)
    limit = {"max_search_files": 1} if budget == "files" else {"max_search_bytes": 1}
    scope = resolve_scope(tmp_path, snapshot, [{"kind": "concept", "value": QUERY}], **limit)
    audit = scope.search_audit[0]
    if budget == "files":
        assert audit["indexed_files_total"] <= 1
    else:
        assert audit["indexed_bytes_total"] <= 1
    assert audit["truncated"]


def test_explicit_path_lookup_needs_no_content_search_budget(tmp_path):
    snapshot = scope_fixture(tmp_path)
    scope = resolve_scope(
        tmp_path, snapshot, [{"kind": "path", "value": "controller/issuer.go"}],
        max_search_files=0, max_search_bytes=0,
    )
    assert scope.seed_paths == ("controller/issuer.go",)
    assert scope.search_audit[0]["indexed_files_total"] == 0

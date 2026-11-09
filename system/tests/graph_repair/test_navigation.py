"""ce-search / ce-read / ce-follow over the live work tree (SourceNavigator)."""
import json

from conftest import make_repo, run_tool


def handles(text):
    found = []

    def walk(v):
        if isinstance(v, dict):
            if 'handle' in v:
                found.append(v)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(json.loads(text))
    return found


def go_workspace(w):
    (w.root / 'pkg').mkdir()
    (w.root / 'pkg/client.go').write_text('package pkg\n\nimport "strings"\n\n// Host returns the host.\n'
                                          'func Host(v string) string {\n\treturn strings.TrimSpace(v)\n}\n')


def test_search_then_read_in_a_new_process(workspace):
    go_workspace(workspace)
    out = run_tool(workspace, 'ce-search', '--symbol', 'Host', '--scope', 'pkg', check=True).stdout
    hits = [h for h in handles(out) if h.get('path') == 'pkg/client.go']
    assert hits, out
    read = run_tool(workspace, 'ce-read', hits[0]['handle'], check=True).stdout
    assert 'strings.TrimSpace' in read


def test_handle_expires_when_the_file_changes(workspace):
    go_workspace(workspace)
    out = run_tool(workspace, 'ce-search', '--symbol', 'Host', check=True).stdout
    handle = [h for h in handles(out) if h.get('path') == 'pkg/client.go'][0]['handle']
    (workspace.root / 'pkg/client.go').write_text('package pkg\n\nfunc Host() string { return "x" }\n')
    done = run_tool(workspace, 'ce-read', handle)
    assert done.returncode == 2 and 'UNKNOWN_HANDLE' in done.stdout


def test_search_sees_edits_and_untracked_files(workspace):
    (workspace.root / 'new.yaml').write_text('key: marker-value\n')
    out = run_tool(workspace, 'ce-search', '--text', 'marker-value', check=True).stdout
    assert 'new.yaml' in out


def test_read_line_range_and_follow_reports(workspace):
    go_workspace(workspace)
    out = run_tool(workspace, 'ce-read', '--path', 'chart/values.yaml', '--start', '1', '--end', '2', check=True).stdout
    assert 'ingress' in out
    found = run_tool(workspace, 'ce-search', '--symbol', 'Host', check=True).stdout
    handle = [h for h in handles(found) if h.get('path') == 'pkg/client.go'][0]['handle']
    follow = run_tool(workspace, 'ce-follow', handle, '--selector', 'strings.TrimSpace')
    assert follow.returncode in (0, 2) and follow.stdout.strip()

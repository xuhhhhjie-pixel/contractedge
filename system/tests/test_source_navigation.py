"""Unified read-only source navigation (front-half redesign batch 1).

Each test reproduces a confirmed public failure shape from
FRONT_HALF_REDESIGN_IMPLEMENTATION_SPEC sections 2 and 10 on synthetic sources.
"""
import hashlib

import pytest

from contractedge.source.dependency import PublicGoDependencies
from contractedge.source.navigation import (NavigationError, NavigationLimits, SourceNavigator,
                                            go_declarations)
from contractedge.source.snapshot import compute_snapshot
from tests.test_compiler_dependency_context import fixture, replace_archive


def make(tmp_path, files, **kwargs):
    root = tmp_path / 'repo'
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode('utf-8') if isinstance(body, str) else body)
    snapshot = compute_snapshot(root)
    return SourceNavigator(snapshot, **kwargs), root


def all_hits(nav, **query):
    hits, cursor, scans = [], None, []
    while True:
        page = nav.search(cursor=cursor, **query)
        hits.extend(page['hits'])
        scans.append(page['scan'])
        cursor = page['next_cursor']
        if cursor is None:
            return hits, scans


# --- Certmanager: named function ranked 48th, same-name methods in two packages

def test_named_declaration_is_readable_regardless_of_rank_and_pages_lose_nothing(tmp_path):
    body = 'package util\n' + ''.join(f'func helper{i:02d}() int {{ return {i} }}\n' for i in range(60))
    body += 'func lookupCNAMETarget(fqdn string) (string, error) {\n\treturn "", nil\n}\n'
    nav, _ = make(tmp_path, {'pkg/issuer/acme/dns/util/dns.go': body})
    page = nav.search(scope='pkg/issuer', symbol='lookupCNAMETarget')
    assert [h['symbol'] for h in page['hits']] == ['lookupCNAMETarget'] and page['scan']['complete']
    record = nav.read(page['hits'][0]['handle'])
    assert record['text'].startswith('func lookupCNAMETarget') and record['complete_declaration']
    hits, scans = all_hits(nav, scope='pkg', kind='go_func', page_size=7)
    assert len(hits) == 61 and len({h['handle'] for h in hits}) == 61
    assert scans[-1]['complete'] and len(scans) == 9


def test_same_name_methods_are_disambiguated_by_path_and_receiver(tmp_path):
    rfc = 'package rfc2136\ntype DNSProvider struct{}\nfunc (r *DNSProvider) changeRecord(action string) error {\n\treturn nil\n}\n'
    r53 = 'package route53\ntype DNSProvider struct{}\nfunc (r *DNSProvider) changeRecord(action string) error {\n\treturn nil // r53\n}\n'
    nav, _ = make(tmp_path, {'pkg/dns/rfc2136/rfc2136.go': rfc, 'pkg/dns/route53/route53.go': r53})
    hits = nav.search(scope='pkg/dns', symbol='DNSProvider.changeRecord')['hits']
    assert sorted(h['path'] for h in hits) == ['pkg/dns/rfc2136/rfc2136.go', 'pkg/dns/route53/route53.go']
    assert len({h['handle'] for h in hits}) == 2 and {h['receiver'] for h in hits} == {'DNSProvider'}
    record = nav.read(path='pkg/dns/route53/route53.go', symbol='changeRecord', receiver='DNSProvider')
    assert 'r53' in record['text']


def test_ambiguous_read_returns_candidates_instead_of_guessing(tmp_path):
    body = ('package p\ntype A struct{}\ntype B struct{}\n'
            'func (a A) Close() {}\nfunc (b *B) Close() {}\n')
    nav, _ = make(tmp_path, {'p/p.go': body})
    with pytest.raises(NavigationError) as error:
        nav.read(path='p/p.go', symbol='Close')
    assert error.value.reason == 'AMBIGUOUS'
    assert {c['receiver'] for c in error.value.details['candidates']} == {'A', 'B'}


# --- CloudWatch: function name and operation word must both be answered

def test_symbol_and_operation_text_are_both_kept_and_answered_separately(tmp_path):
    sdk = ('package metric_alarm\nfunc sdkUpdate() error {\n\treturn putMetricAlarm()\n}\n'
           'func putMetricAlarm() error { return nil }\n')
    tags = ('package metric_alarm\nfunc syncTags(desired, latest map[string]string) {\n'
            '\tcallTagResource(desired)\n}\n')
    nav, _ = make(tmp_path, {'pkg/resource/metric_alarm/sdk.go': sdk,
                             'pkg/resource/metric_alarm/tags.go': tags})
    page = nav.search(scope='pkg/resource/metric_alarm', symbol='sdkUpdate', text='TagResource')
    by_symbol = {h['symbol']: h for h in page['hits']}
    assert by_symbol['sdkUpdate']['matched_by'] == ['symbol']
    assert by_symbol['sdkUpdate']['contains_text'] is False
    assert by_symbol['syncTags']['matched_by'] == ['text'] and by_symbol['syncTags']['text_hit_lines'] == [3]
    assert page['scan']['complete']


def test_incomplete_scan_is_continuable_and_never_a_complete_zero(tmp_path):
    files = {f'pkg/m/f{i}.go': f'package m\nfunc F{i}() {{}}\n' for i in range(5)}
    files['pkg/m/z.go'] = 'package m\nfunc Z() { TagResource() }\n'
    nav, _ = make(tmp_path, files, limits=NavigationLimits(max_files_per_call=2))
    first = nav.search(scope='pkg/m', text='TagResource')
    assert first['hits'] == [] and not first['scan']['complete']
    assert first['scan']['stop_reason'] == 'TOOL_SCAN_LIMIT' and first['next_cursor']
    hits, scans = all_hits(nav, scope='pkg/m', text='TagResource')
    assert [h['symbol'] for h in hits] == ['Z'] and scans[-1]['complete']


def test_cursor_of_another_query_is_rejected(tmp_path):
    nav, _ = make(tmp_path, {f'a/f{i}.go': f'package a\nfunc F{i}() {{}}\n' for i in range(3)})
    page = nav.search(scope='a', kind='go_func', page_size=1)
    with pytest.raises(NavigationError) as error:
        nav.search(scope='a', symbol='F1', cursor=page['next_cursor'])
    assert error.value.reason == 'BAD_CURSOR'


# --- Prometheus: a 52 KB, 1300-line declaration is read whole

def test_large_declaration_is_read_whole_without_line_or_char_window(tmp_path):
    lines = ['\tx%d := "%s"' % (i, 'v' * 30) for i in range(1300)]
    body = 'package main\nfunc main() {\n' + '\n'.join(lines) + '\n\tconfigSuccess := checksum\n}\n'
    nav, _ = make(tmp_path, {'cmd/prometheus/main.go': body})
    hit = nav.search(scope='cmd', symbol='main', text='configSuccess')['hits'][0]
    record = nav.read(hit['handle'])
    assert record['bytes'] > 52000 and record['end_line'] - record['start_line'] + 1 == 1303
    assert record['text'].rstrip().endswith('}') and 'configSuccess' in record['text']
    assert hit['contains_text'] and hit['bytes'] == record['bytes']


def test_read_over_tool_limit_is_refused_with_size_not_truncated(tmp_path):
    body = 'package main\nfunc main() {\n' + '\tx := 1\n' * 4000 + '}\n'
    nav, _ = make(tmp_path, {'main.go': body}, limits=NavigationLimits(max_read_bytes=1000))
    handle = nav.search(symbol='main')['hits'][0]['handle']
    with pytest.raises(NavigationError) as error:
        nav.read(handle)
    assert error.value.reason == 'READ_EXCEEDS_TOOL_LIMIT' and error.value.details['size'] > 1000


# --- Go structure: receivers, generics, groups, closures, masked literals

GO = '''package store

import "fmt"

type (
\tKey string
\tPair[K comparable, V any] struct {
\t\tK K
\t\tV V
\t}
)

type Store[K comparable, V any] struct { items map[K]V }

type Mode int

const (
\tModeA Mode = iota
\tModeB
)

var registry = map[string]func() string{
\t"a": func() string { return "}" },
}

func Map[T, U any](in []T, f func(T) U) []U {
\tout := make([]U, 0, len(in))
\tfor _, v := range in { out = append(out, f(v)) }
\treturn out
}

func (s *Store[K, V]) Get(key K) (V, bool) {
\tv, ok := s.items[key] // brace in comment {
\t_ = '{'
\t_ = `raw } string`
\treturn v, ok
}

func (Store[K, V]) Len() int { return 0 }

func helper() {
\tfunc() { fmt.Println("nested {") }()
}
'''


def test_go_index_covers_receivers_generics_groups_and_ignores_literal_braces():
    declarations, problems = go_declarations('store.go', GO)
    found = {(d.kind, d.symbol, d.receiver) for d in declarations}
    assert problems == []
    assert {('go_type_group', 'Key', ''), ('go_type', 'Store', ''), ('go_type', 'Mode', ''),
            ('go_const_group', 'ModeA', ''), ('go_var', 'registry', ''), ('go_func', 'Map', ''),
            ('go_method', 'Get', 'Store'), ('go_method', 'Len', 'Store'), ('go_func', 'helper', '')} <= found
    group = next(d for d in declarations if d.kind == 'go_type_group')
    assert group.names == ('Key', 'Pair')
    get = next(d for d in declarations if d.symbol == 'Get')
    assert GO[get.start_char:get.end_char].endswith('return v, ok\n}')
    assert next(d for d in declarations if d.symbol == 'helper').end_line == GO.count('\n')


def test_go_parse_problem_keeps_text_search_and_range_read(tmp_path):
    broken = 'package p\nfunc Good() {}\nfunc Broken() {\n\tif x {\n\tTagResource()\n'
    nav, _ = make(tmp_path, {'p/p.go': broken})
    page = nav.search(scope='p', symbol='Broken')
    assert page['hits'] == [] and not page['scan']['complete']
    assert page['scan']['parse_incomplete_files'] == ['p/p.go']
    text = nav.search(scope='p', text='TagResource')
    assert text['hits'][0]['kind'] == 'text_outside_declarations' and text['scan']['complete']
    hint = text['hits'][0]['read_hint']
    record = nav.read(path=hint['path'], start_line=3, end_line=hint['end_line'])
    assert 'TagResource' in record['text'] and record['complete_declaration'] is False
    with pytest.raises(NavigationError) as error:
        nav.read(path='p/p.go', symbol='Broken')
    assert error.value.reason == 'NOT_FOUND_PARSE_INCOMPLETE'


def test_python_newer_syntax_falls_back_to_text_not_absence(tmp_path):
    # A template string literal (PEP 750) is a SyntaxError for the 3.11/3.12 runners.
    nav, _ = make(tmp_path, {'kopf/x.py': 'def adjust_namespace(obj):\n    return t"{obj}"\n',
                             'kopf/ok.py': 'class Owner:\n    def adopt(self):\n        return 1\n'})
    assert nav.search(scope='kopf', symbol='adjust_namespace')['scan']['complete'] is False
    assert nav.search(scope='kopf', text='adjust_namespace')['hits'][0]['path'] == 'kopf/x.py'
    method = nav.search(scope='kopf', symbol='Owner.adopt')['hits'][0]
    assert method['kind'] == 'py_method' and method['receiver'] == 'Owner'


# --- Coordinates: CRLF and Unicode keep exact original bytes

def test_crlf_and_unicode_coordinates_are_exact_original_bytes(tmp_path):
    raw = 'package p\r\n// 中文注释 ☃\r\nfunc A() {\r\n\tx := "é"\r\n}\r\nfunc B() {}\r\n'.encode('utf-8')
    nav, root = make(tmp_path, {'p.go': raw})
    record = nav.read(path='p.go', symbol='A')
    assert raw[record['start_byte']:record['end_byte']] == record['text'].encode('utf-8')
    assert record['text'].startswith('func A() {\r\n') and (record['start_line'], record['end_line']) == (3, 5)
    assert record['content_digest'] == hashlib.sha256(record['text'].encode()).hexdigest()


# --- Snapshot authority

def test_scope_escape_symlink_and_changed_source_are_refused(tmp_path):
    nav, root = make(tmp_path, {'a/a.go': 'package a\nfunc A() {}\n'})
    with pytest.raises(NavigationError) as error:
        nav.search(scope='../outside', text='x')
    assert error.value.reason == 'BAD_SCOPE'
    handle = nav.search(symbol='A')['hits'][0]['handle']
    (root / 'a/a.go').write_text('package a\nfunc A() { changed() }\n')
    with pytest.raises(NavigationError) as error:
        nav.read(handle)
    assert error.value.reason == 'SOURCE_CHANGED'


def test_handles_are_stable_for_the_same_snapshot(tmp_path):
    nav, root = make(tmp_path, {'a/a.go': 'package a\nfunc A() {}\n'})
    again = SourceNavigator(compute_snapshot(root))
    assert nav.search(symbol='A')['hits'][0]['handle'] == again.search(symbol='A')['hits'][0]['handle']


# --- Follow: real imports, shadowing, receivers and pinned dependencies

MAIN_MODULE = {
    'go.mod': 'module example.org/service\n',
    'internal/client/client.go': ('package client\ntype Client struct{}\n'
                                  'func New(plain bool) *Client { return &Client{} }\n'
                                  'func (c *Client) Pull(ref string) error { return nil }\n'),
    'cmd/run.go': ('package cmd\nimport cl "example.org/service/internal/client"\n'
                   'func run() { c := cl.New(true); _ = c.Pull("x") }\n'
                   'func shadow(cl int) { _ = cl }\n'),
}


def test_follow_main_module_import_alias_and_package_listing(tmp_path):
    nav, _ = make(tmp_path, MAIN_MODULE)
    run = nav.search(scope='cmd', symbol='run')['hits'][0]['handle']
    resolved = nav.follow_dependency(run, selector='cl.New')
    assert resolved['binding']['status'] == 'RESOLVED'
    assert resolved['binding']['imported'] == 'example.org/service/internal/client'
    assert [h['symbol'] for h in resolved['hits']] == ['New']
    listing = nav.follow_dependency(run, selector='cl')
    assert listing['binding']['status'] == 'PACKAGE_LISTING'
    assert {(h['symbol'], h['receiver']) for h in listing['hits']} >= {('New', ''), ('Pull', 'Client'),
                                                                       ('Client', '')}


def test_follow_refuses_shadowed_alias_and_unknown_receiver_type(tmp_path):
    files = dict(MAIN_MODULE)
    files['cmd/run.go'] += 'func shadowed(cl string) { _ = cl.New }\n'
    nav, _ = make(tmp_path, files)
    shadowed = nav.search(scope='cmd', symbol='shadowed')['hits'][0]['handle']
    assert nav.follow_dependency(shadowed, selector='cl.New')['binding']['status'] == 'UNRESOLVED'
    files2 = {'go.mod': 'module m\n', 'p/a.go': ('package p\ntype A struct{}\ntype B struct{}\n'
              'func (a *A) Run() {}\nfunc (b *B) Run() {}\nfunc use(x interface{ Run() }) { x.Run() }\n'
              'func (a *A) call() { a.Run() }\n')}
    nav2, _ = make(tmp_path / 'two', files2)
    use = nav2.search(scope='p', symbol='use')['hits'][0]['handle']
    unknown = nav2.follow_dependency(use, selector='x.Run')
    assert unknown['binding']['status'] == 'AMBIGUOUS' and len(unknown['hits']) == 2
    own = nav2.search(scope='p', symbol='A.call')['hits'][0]['handle']
    known = nav2.follow_dependency(own, selector='a.Run')
    assert known['binding']['status'] == 'RESOLVED' and known['hits'][0]['receiver'] == 'A'


API = '''package check
type Client struct { PlainHTTP bool }
type Option func(*Client)
func NewClient(options ...Option) *Client { return &Client{} }
func (c *Client) Pull(ref string) error { return nil }
func ClientOptPlainHTTP() Option { return func(c *Client) { c.PlainHTTP = true } }
'''


def dependency_navigator(tmp_path, members):
    snapshot, feedback, inputs, source = fixture(tmp_path, body=API)
    (source / 'main.go').write_text('package service\nimport wire "example.org/toolbox/pkg/check"\n'
                                    'func update() { _ = wire.NewClient() }\n')
    if members is not None:
        replace_archive(inputs, feedback, members)
    snapshot = compute_snapshot(source)
    reader = PublicGoDependencies(snapshot, 'CASE', feedback['public_execution_capabilities'])
    nav = SourceNavigator(snapshot, dependencies=reader)
    return nav, nav.search(symbol='update')['hits'][0]['handle']


def test_follow_pinned_dependency_reads_receiver_methods_with_version_identity(tmp_path):
    nav, update = dependency_navigator(tmp_path, None)
    listing = nav.follow_dependency(update, selector='wire')
    assert listing['binding']['origin'] == 'PINNED_DEPENDENCY' and listing['binding']['dependency_complete']
    method = next(h for h in listing['hits'] if h['symbol'] == 'Pull')
    record = nav.read(method['handle'])
    assert record['receiver'] == 'Client' and record['read_only'] and record['origin'] == 'DEPENDENCY'
    assert record['dependency']['version'] == 'v1.2.3' and record['snapshot_id'] is None
    assert nav.follow_dependency(update, selector='wire.ClientOptPlainHTTP')['binding']['status'] == 'RESOLVED'


def test_one_build_tagged_or_oversized_file_does_not_discard_the_package(tmp_path):
    prefix = 'gomod/example.org/toolbox@v1.2.3/pkg/check/'
    nav, update = dependency_navigator(tmp_path, [
        (prefix + 'api.go', API), (prefix + 'other_linux.go', '//go:build linux\npackage check\nfunc Linux() {}\n'),
        (prefix + 'big.go', 'package check\n//' + 'x' * (5 * 1024 * 1024) + '\n')])
    listing = nav.follow_dependency(update, selector='wire')
    symbols = {h['symbol'] for h in listing['hits']}
    assert {'NewClient', 'Pull', 'Linux'} <= symbols
    assert listing['binding']['dependency_complete'] is False
    skipped = [s for row in listing['binding']['dependency_scan'] for s in row.get('skipped', [])]
    assert skipped and skipped[0]['reason'] == 'FILE_EXCEEDS_TOOL_LIMIT'
    linux = nav.read(next(h for h in listing['hits'] if h['symbol'] == 'Linux')['handle'])
    assert linux['dependency']['build_constraint'] == 'linux' and linux['dependency']['platform_file']


def test_config_or_unrelated_parse_failures_do_not_block_code_absence_facts(tmp_path):
    nav, _ = make(tmp_path, {'p/a.go': 'package p\nfunc A() {}\n',
                             'p/broken.go': 'package p\nfunc Other() {\n',
                             'deploy/bad.yaml': 'key: [unclosed\n',
                             '.github/ci.yaml': 'run: ${{ matrix.go }}\n'})
    absent = nav.search(symbol='Missing')
    assert absent['hits'] == [] and absent['scan']['complete'] is True
    other = nav.search(symbol='Other')
    assert other['scan']['complete'] is False and other['scan']['parse_incomplete_files'] == ['p/broken.go']
    text = nav.search(text='unclosed')
    assert text['hits'][0]['path'] == 'deploy/bad.yaml' and text['scan']['complete']
    config = nav.search(kind='config_subtree')
    assert config['scan']['config_parse_incomplete_files'] == ['deploy/bad.yaml']
    assert config['scan']['complete'] is False


def test_binary_data_and_oversized_config_do_not_hide_code_declarations(tmp_path):
    nav, _ = make(tmp_path, {'p/a.go': 'package p\nfunc A() {}\n', 'testdata/index': b'\xff\xfe\x00binary',
                             'testdata/big.json': '[' + '1,' * 600 + '1]'},
                  limits=NavigationLimits(max_file_bytes=1000))
    code = nav.search(symbol='Missing')
    assert code['scan']['complete'] and {s['reason'] for s in code['scan']['skipped_files']} == {
        'NOT_UTF8', 'FILE_EXCEEDS_TOOL_LIMIT'}
    assert nav.search(text='Missing')['scan']['complete'] is False


def test_follow_skips_test_files_and_nested_modules(tmp_path):
    files = dict(MAIN_MODULE)
    files['internal/client/client_test.go'] = 'package client\nfunc New(plain bool) *Client { return nil }\n'
    files['internal/client/run.go'] = 'package client\nfunc start() { New(true) }\n'
    files['tools/go.mod'] = 'module example.org/service/tools\n'
    files['tools/gen.go'] = 'package tools\nfunc Gen() {}\n'
    files['cmd/gen.go'] = 'package cmd\nimport t "example.org/service/tools"\nfunc gen() { t.Gen() }\n'
    nav, _ = make(tmp_path, files)
    start = nav.search(scope='internal', symbol='start')['hits'][0]['handle']
    local = nav.follow_dependency(start, selector='New')
    assert local['binding']['status'] == 'RESOLVED' and local['hits'][0]['path'] == 'internal/client/client.go'
    gen = nav.search(scope='cmd', symbol='gen')['hits'][0]['handle']
    assert nav.follow_dependency(gen, selector='t.Gen')['binding']['status'] == 'UNAVAILABLE'

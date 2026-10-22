import importlib.util


HELPERS=[dict(id='H1',excerpt='''// Merge by the ResourceID annotation.
func Merge(output, selected, input []*Node) []*Node {
 for _, n := range output { if n.GetAnnotations()[ResourceID] == "" { result = append(result,n) } }
 return result
}'''),dict(id='H2',excerpt='''func Initialize(input []*Node) error { return SetAnnotation(ResourceID, "id") }'''),
dict(id='H3',excerpt='''func Cleanup(input []*Node) error { return ClearAnnotation(ResourceID) }''')]


def api():
    assert importlib.util.find_spec('contractedge.execution.preconditions'), 'Helper precondition validator missing'
    from contractedge.execution import preconditions
    return preconditions


def source(init='len(selectors)>0',merge='configPath!=""',cleanup=True):
    return 'package p\nfunc Apply() {\n if '+init+' { rt.Initialize(input) }\n if '+merge+' { input = rt.Merge(output, selected, input)'+('\n rt.Cleanup(input)' if cleanup else '')+' }\n}\n'


def test_new_merge_path_without_identity_initialization_is_a_concrete_conflict():
    r=api().validate_helper_preconditions({}, {'apply.go':source()},HELPERS)
    assert any(x['status']=='CONFLICT' and x['precondition']=='RESOURCE_ID_INITIALIZATION' for x in r['checks'])


def test_joint_initialization_merge_cleanup_change_passes_supported_guard_check():
    r=api().validate_helper_preconditions({}, {'apply.go':source('len(selectors)>0 || configPath!=""')},HELPERS)
    assert r['checks'] and not any(x['status']=='CONFLICT' for x in r['checks'])
    assert all(x['status']=='CHECKED_SYNTAX' for x in r['checks'])
    assert 'behavior' in r['interpretation'].lower()


def test_missing_cleanup_is_reported_and_dynamic_guard_remains_unproven():
    r=api().validate_helper_preconditions({}, {'apply.go':source('len(selectors)>0 || configPath!=""',cleanup=False)},HELPERS)
    assert any(x['precondition']=='RESOURCE_ID_CLEANUP' and x['status']=='CONFLICT' for x in r['checks'])
    dynamic=api().validate_helper_preconditions({}, {'apply.go':source('allow(x)','configPath!=""')},HELPERS)
    assert any(x['status']=='UNKNOWN' for x in dynamic['checks'])


def test_unrelated_helper_is_not_subject_to_annotation_identity_rules():
    r=api().validate_helper_preconditions({}, {'apply.go':'func Apply() { rt.Send(request) }'},[{'id':'H','excerpt':'func Send(request Request) { client.Send(request) }'}])
    assert r['checks']==[]


def test_initializer_in_opposite_else_branch_is_never_syntactically_verified():
    text='func Apply() {\nif configPath!="" { println("config") } else { rt.Initialize(input) }\nif configPath!="" { input=rt.Merge(output, selected, input); rt.Cleanup(input) }\n}'
    result=api().validate_helper_preconditions({}, {'apply.go':text},HELPERS)
    assert any(c['precondition']=='RESOURCE_ID_INITIALIZATION' and c['status']=='UNKNOWN' for c in result['checks'])


def test_same_loop_can_compare_local_guards_but_different_loops_cannot():
    text=source().replace('func Apply() {','func Apply() {\nfor _, function := range functions {')+'\n}'
    result=api().validate_helper_preconditions({}, {'apply.go':text},HELPERS)
    assert any(c['precondition']=='RESOURCE_ID_INITIALIZATION' and c['status']=='CONFLICT' for c in result['checks'])
    different='''func Apply() {
for _, item := range first { rt.Initialize(input) }
for _, item := range second { input=rt.Merge(output,selected,input); rt.Cleanup(input) }
}'''
    result=api().validate_helper_preconditions({}, {'apply.go':different},HELPERS)
    assert any(c['precondition']=='RESOURCE_ID_INITIALIZATION' and c['status']=='UNKNOWN' for c in result['checks'])

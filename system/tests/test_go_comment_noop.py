import pytest
from contractedge.execution.code_change import semantics_unchanged


@pytest.mark.parametrize('before,after',[
 ('package p\n// old\nfunc f() {}\n','package p\n// revised\n// explanation\nfunc f() {}\n'),
 ('package p\nfunc f(){ /* old */ run() }\n','package p\nfunc f(){ /* revised */ run() }\n'),
 ('package p\n/* old\n text */\nfunc f(){}\n','package p\n/* revised\n text */\nfunc f(){}\n'),
])
def test_ordinary_go_comment_changes_cannot_satisfy_a_behavior_retry(before,after):
    assert semantics_unchanged({'a.go':before.encode()},{'a.go':after})


@pytest.mark.parametrize('before,after',[
 ('package p\nfunc f(){ return/*\n*/1 }','package p\nfunc f(){ return/**/1 }'),
 ('package p\nvar x="// old"','package p\nvar x="// revised"'),
 ('package p\nvar x=`/* old */`','package p\nvar x=`/* revised */`'),
 ('package p\nfunc f(){ return 1 }','package p\nfunc f(){ return 2 }'),
 ('//go:build x\npackage p\n//old','//go:build y\npackage p\n//new'),
 ('package p\n/*line old:1*/func f(){}','package p\n/*line new:2*/func f(){}'),
 ('package p\n/* old */ import "C"','package p\n/* revised */ import "C"'),
])
def test_literal_newline_directive_and_cgo_changes_remain_unproven(before,after):
    assert not semantics_unchanged({'a.go':before.encode()},{'a.go':after})

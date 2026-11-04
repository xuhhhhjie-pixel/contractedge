import hashlib
import os
from pathlib import Path
import subprocess

from contractedge.execution.probe_specs import registered_programs


def test_versioned_temporal_probe_decodes_complete_large_json_and_exposes_duplicates(tmp_path):
    registry=registered_programs()
    assert hashlib.sha256(registry['alertmanager-reload-v1'][0].encode()).hexdigest()=='0e329f171872fe23f1ac925e07ac534e6c7d1f7ada2ac9f2bade852fc7e43dc7'
    assert 'alertmanager-reload-v2' in registry
    assert hashlib.sha256(registry['alertmanager-reload-v2'][0].encode()).hexdigest()=='c85659e9df7b634a9e60ff7d2dad6c4e962aade9b57f43ecbda3e400524e165e'
    tmp_path.chmod(0o755)
    test_source=r'''package main
import("encoding/json";"fmt";"strings";"testing")
func response(n int, duplicate bool, padding int) string {
 list:=[]interface{}{}
 for i:=0;i<n;i++ {name:=fmt.Sprintf("ContractEdgePublicReload-%03d",i);if duplicate{name="ContractEdgePublicReload-000"}
 list=append(list,map[string]interface{}{"labels":map[string]string{"alertname":name},"receiver":map[string]string{"name":"null"},"padding":strings.Repeat("x",padding),"alerts":[]interface{}{map[string]interface{}{"labels":map[string]string{"alertname":name,"instance":"local"}}}})}
 b,_:=json.Marshal(list);return string(b)
}
func TestCompleteLarge(t *testing.T){s:=response(128,false,10000);if len(s)<=1024*1024{t.Fatal("fixture too small")};visible,unique,n,e:=decodePublicGroups(strings.NewReader(s));if e!=nil||!visible||!unique||n!=len(s){t.Fatal(visible,unique,n,e)}}
func TestDuplicate(t *testing.T){_,unique,_,e:=decodePublicGroups(strings.NewReader(response(2,true,0)));if e!=nil||unique{t.Fatal(unique,e)}}
func TestMissingIsNotDuplicate(t *testing.T){visible,unique,_,e:=decodePublicGroups(strings.NewReader("[]"));if e!=nil||visible||!unique{t.Fatal(visible,unique,e)}}
func TestInvalid(t *testing.T){_,_,_,e:=decodePublicGroups(strings.NewReader("["));if e==nil{t.Fatal("invalid JSON accepted")}}
func TestOverBound(t *testing.T){_,_,_,e:=decodePublicGroups(strings.NewReader(strings.Repeat(" ",8*1024*1024+1)));if e==nil||!strings.Contains(e.Error(),"exceeds"){t.Fatal(e)}}
'''
    for suffix,count,padding in [('v2',128,10000),('v3',2048,625),('v4',2048,625)]:
        directory=tmp_path/suffix;directory.mkdir();directory.chmod(0o755)
        program,checks=registry['alertmanager-reload-'+suffix]
        assert 'groups.unique_for_declared_route' in checks
        (directory/'main.go').write_text(program);(directory/'main.go').chmod(0o644)
        body=test_source.replace('response(128,false,10000)',f'response({count},false,{padding})')
        (directory/'main_test.go').write_text(body);(directory/'main_test.go').chmod(0o644)
        if suffix=='v4':
            partial=r'''package main
import("os";"os/exec";"encoding/json";"strings";"testing")
func TestBoundedDuplicatePrefix(t *testing.T){
 if os.Getenv("CE_PREFIX_HELPER")=="1"{decodePublicGroups(strings.NewReader(response(2048,true,5000)));t.Fatal("duplicate prefix was lost");return}
 cmd:=exec.Command(os.Args[0],"-test.run=^TestBoundedDuplicatePrefix$")
 cmd.Env=append(os.Environ(),"CE_PREFIX_HELPER=1");raw,e:=cmd.CombinedOutput();if e!=nil{t.Fatal(e,string(raw))}
 var result struct{ProbeID string `json:"probe_id"`;Partial bool `json:"partial_observation"`;Checks []struct{ID string `json:"id"`;Status string `json:"status"`;Observed struct{Complete bool `json:"response_complete"`;Groups int `json:"complete_prefix_groups"`;Duplicates int `json:"duplicate_identities"`;Unique int `json:"unique_identities"`;Bytes int `json:"observed_bytes"`} `json:"observed"`} `json:"checks"`}
 if e=json.Unmarshal(raw,&result);e!=nil{t.Fatal(e,string(raw))}
 if result.ProbeID!="alertmanager-reload-v4"||!result.Partial||len(result.Checks)!=1{t.Fatal(string(raw))}
 c:=result.Checks[0];if c.ID!="groups.unique_for_declared_route"||c.Status!="FAIL"||c.Observed.Complete||c.Observed.Duplicates<1||c.Observed.Groups!=c.Observed.Duplicates+c.Observed.Unique||c.Observed.Bytes!=8*1024*1024+1{t.Fatal(string(raw))}
}
func TestLargeUniquePrefixRemainsUnavailable(t *testing.T){
 _,_,_,e:=decodePublicGroups(strings.NewReader(response(2048,false,5000)))
 if e==nil||!strings.Contains(e.Error(),"incomplete observation"){t.Fatal(e)}
}
func TestUnrecognisedPrefixCannotInventIdentity(t *testing.T){
 _,_,_,e:=decodePublicGroups(strings.NewReader("[{}, {},"+strings.Repeat(" ",8*1024*1024)))
 if e==nil||!strings.Contains(e.Error(),"incomplete observation"){t.Fatal(e)}
}
'''
            (directory/'prefix_test.go').write_text(partial);(directory/'prefix_test.go').chmod(0o644)
    image='golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d'
    command=['docker','run','--rm','--pull=never','--network=none','--read-only',
        '--user',f'{os.getuid()}:{os.getgid()}','--cpus=2','--memory=2g','--tmpfs=/tmp:rw,exec,size=1g',
        '--mount',f'type=bind,src={tmp_path},dst=/work,readonly','--workdir=/work',
        '--env=GOCACHE=/tmp/gocache','--env=GO111MODULE=off',image,'go','test','-p=1','-v','./...']
    result=subprocess.run(command,capture_output=True,text=True,timeout=180)
    assert result.returncode==0,result.stdout+'\n'+result.stderr

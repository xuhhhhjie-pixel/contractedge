"""Audited method probes derived from public requirements/APIs, never scorers.

Programs are frozen with the method, read-only to candidate execution, and are
not exported as candidate edits. Only explicitly registered profiles can run.
"""
import hashlib

KPT_CONFIGPATH_GO = r'''package main
import (
 "context"
 "encoding/json"
 "fmt"
 "io"
 "os"
 "path/filepath"
 "strings"
 kptfile "github.com/kptdev/kpt/api/kptfile/v1"
 "github.com/kptdev/kpt/pkg/fn"
 "github.com/kptdev/kpt/pkg/lib/kptops"
 "github.com/kptdev/kpt/pkg/lib/runneroptions"
 "github.com/kptdev/kpt/pkg/printer"
 "sigs.k8s.io/kustomize/kyaml/filesys"
 syaml "sigs.k8s.io/yaml"
)
type object = map[string]interface{}
type check struct { ID string `json:"id"`; Status string `json:"status"`; Observed interface{} `json:"observed"` }
type seen struct { Names []string `json:"ordinary_inputs"`; Config string `json:"function_config"` }
type runtime struct { Seen map[string]seen }
type runner struct { RT *runtime; Name string }
func metadata(o object) object { if m,ok:=o["metadata"].(map[string]interface{});ok{return m};return object{} }
func name(o object) string { s,_:=metadata(o)["name"].(string);return s }
func (rt *runtime) GetRunner(_ context.Context, f *kptfile.Function) (fn.FunctionRunner,error) {
 if f.Exec!="owner" && f.Exec!="later" && f.Exec!="control" {return nil,fmt.Errorf("unknown public probe function %q", f.Exec)}
 return &runner{rt,f.Exec},nil
}
func (r *runner) Run(in io.Reader,out io.Writer) error {
 b,err:=io.ReadAll(in);if err!=nil{return err}
 var list struct { APIVersion string `json:"apiVersion"`; Kind string `json:"kind"`; Items []object `json:"items"`; FunctionConfig object `json:"functionConfig,omitempty"` }
 if err=syaml.Unmarshal(b,&list);err!=nil{return err}
 observation:=seen{Names:[]string{},Config:name(list.FunctionConfig)}
 for _,item:=range list.Items {
  n:=name(item);observation.Names=append(observation.Names,n)
  if (r.Name=="owner" && n=="workload") || (r.Name=="later" && n=="settings") {
   d,ok:=item["data"].(map[string]interface{});if !ok{d=object{};item["data"]=d};d[r.Name]="observed"
  }
 }
 r.RT.Seen[r.Name]=observation
 return json.NewEncoder(out).Encode(list)
}
func contains(v []string,s string) bool {for _,x:=range v{if x==s{return true}};return false}
func get(o object,key string) interface{} { d,_:=o["data"].(map[string]interface{});return d[key] }
func main(){
 results:=[]check{}
 add:=func(id string,ok bool,observed interface{}){status:="FAIL";if ok{status="PASS"};results=append(results,check{id,status,observed})}
 for _,kind:=range []string{"mutator","validator","control"}{
  dir,err:=os.MkdirTemp("","ce-public-configpath-");if err!=nil{panic(err)}
  pipeline:="  mutators:\n    - exec: owner\n      configPath: settings.yaml\n    - exec: later\n"
  if kind=="validator"{pipeline="  mutators:\n    - exec: later\n  validators:\n    - exec: owner\n      configPath: settings.yaml\n"}
  if kind=="control"{pipeline="  mutators:\n    - exec: control\n"}
  files:=map[string]string{"Kptfile":"apiVersion: kpt.dev/v1\nkind: Kptfile\nmetadata:\n  name: demo\npipeline:\n"+pipeline,
   "settings.yaml":"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: settings\ndata:\n  initial: config\n",
   "workload.yaml":"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: workload\ndata:\n  initial: workload\n"}
  for path,text:=range files{if err=os.WriteFile(filepath.Join(dir,path),[]byte(text),0600);err!=nil{panic(err)}}
  rt:=&runtime{Seen:map[string]seen{}}
  renderer:=kptops.Renderer{PkgPath:dir,FileSystem:filesys.MakeFsOnDisk(),Runtime:rt,RunnerOptions:runneroptions.RunnerOptions{AllowExec:true}}
  _,runErr:=renderer.Execute(printer.WithContext(context.Background(),printer.New(io.Discard,io.Discard)))
  rendered:=map[string]object{}
  for _,path:=range []string{"settings.yaml","workload.yaml"}{
   b,e:=os.ReadFile(filepath.Join(dir,path));if e==nil{var o object;if syaml.Unmarshal(b,&o)==nil{rendered[name(o)]=o}}
  }
  state:=object{"functions":rt.Seen,"render_error":fmt.Sprint(runErr),"rendered":rendered}
  if kind=="control"{
   add("control.ordinary_inputs",runErr==nil && contains(rt.Seen["control"].Names,"settings") && contains(rt.Seen["control"].Names,"workload"),state)
   add("control.final_resources",runErr==nil && get(rendered["settings"],"initial")=="config" && get(rendered["workload"],"initial")=="workload",state)
  }else{
   owner:=rt.Seen["owner"]
   add(kind+".owner_binding",runErr==nil && owner.Config=="settings",state)
   add(kind+".owner_excludes_config",runErr==nil && !contains(owner.Names,"settings") && contains(owner.Names,"workload"),state)
   if kind=="mutator"{add(kind+".later_sees_config",runErr==nil && contains(rt.Seen["later"].Names,"settings"),state)}
   final:=runErr==nil && get(rendered["settings"],"later")=="observed" && get(rendered["settings"],"initial")=="config" && get(rendered["workload"],"initial")=="workload"
   if kind=="mutator"{final=final && get(rendered["workload"],"owner")=="observed"}
   add(kind+".final_resources",final,state)
  }
  os.RemoveAll(dir)
 }
 payload:=object{"probe_id":"kpt-configpath-v1","checks":results,"scope":"Public requirement-derived Renderer API exercise with local function stand-ins; no container functions, private fixtures or independent oracle"}
 b,_:=json.Marshal(payload);fmt.Println(strings.TrimSpace(string(b)))
}
'''

KPT_CHECKS = {
 **{f'{kind}.{key}': statement for kind in ('mutator','validator') for key,statement in {
  'owner_binding':'configPath resource is supplied as owning functionConfig',
  'owner_excludes_config':'owning function ordinary input excludes config resource and contains workload',
  'final_resources':'final rendered config and workload retain content and required transformations'}.items()},
 'mutator.later_sees_config':'after the owning mutator, a later mutator still sees the config resource',
 'control.ordinary_inputs':'without configPath both resources remain ordinary inputs',
 'control.final_resources':'without configPath both resources keep their declared data',
}


# v1 remains byte-identical for historical replay. v2 has atomic predicates.
KPT_CONFIGPATH_V2_GO = KPT_CONFIGPATH_GO.replace('kpt-configpath-v1','kpt-configpath-v2').replace(
 'add(kind+".final_resources",final,state)',
 'add(kind+".config_retained",runErr==nil && get(rendered["settings"],"initial")=="config",state)\n'
 '   add(kind+".workload_retained",runErr==nil && get(rendered["workload"],"initial")=="workload",state)\n'
 '   add(kind+".config_transformed",runErr==nil && get(rendered["settings"],"later")=="observed",state)\n'
 '   if kind=="mutator"{add(kind+".workload_transformed",runErr==nil && get(rendered["workload"],"owner")=="observed",state)}\n'
 '   _ = final')
KPT_V2_CHECKS = {k:v for k,v in KPT_CHECKS.items() if not k.endswith('.final_resources') or k.startswith('control.')}
for _kind in ('mutator','validator'):
    KPT_V2_CHECKS.update({_kind+'.config_retained':'Final config settings resource retains the declared data.initial field',
      _kind+'.workload_retained':'Final workload resource retains the declared data.initial field',
      _kind+'.config_transformed':'A later function transformation of config is retained in final output'})
KPT_V2_CHECKS['mutator.workload_transformed']='Owning mutator transforms the intended workload'


def registered_programs():
    from contractedge.execution.runtime_probe import ALERTMANAGER_RELOAD_GO, ALERTMANAGER_CHECKS
    return {'kpt-configpath-v1':(KPT_CONFIGPATH_GO,KPT_CHECKS),
            'kpt-configpath-v2':(KPT_CONFIGPATH_V2_GO,KPT_V2_CHECKS),
            'alertmanager-reload-v1':(ALERTMANAGER_RELOAD_GO,ALERTMANAGER_CHECKS)}


def select_probes(requirement, source, profiles):
    registry=registered_programs();unknown=set(profiles)-set(registry)
    if unknown:raise ValueError('Unregistered method probe profile: '+str(sorted(unknown)))
    text=requirement.casefold();selected=[]
    for profile in profiles:
        if profile.startswith('kpt-'):
            if not (all(t in text for t in ('configpath','functionconfig','owning','later','render'))
                    and (source/'pkg/lib/kptops/render.go').is_file() and (source/'pkg/fn/eval.go').is_file()):continue
            apis=[{'package':'pkg/lib/kptops','symbol':'Renderer'},{'package':'pkg/fn','symbol':'FunctionRuntime'}]
            boundary='Local public Renderer API using function runtime stand-ins; candidate code and probe share a process, stdout is not tamper-proof; no external container/WASM or remote service guarantee'
        else:
            if not (all(t in text for t in ('alertmanager','reload','groups')) and (source/'cmd/alertmanager/main.go').is_file()):continue
            apis=[{'package':'cmd/alertmanager','symbol':'main'}]
            boundary='Local production process HTTP API; 2ms target sampling during twenty valid reloads; no guarantee at unsampled instants or distributed convergence; not tamper-proof or independent grading'
        program,checks=registry[profile]
        selected.append({'id':profile,'program':program,'sha256':hashlib.sha256(program.encode()).hexdigest(),
            'checks':checks,'scenario':'method-'+profile,'api_entrypoints':apis,'boundary':boundary})
    return selected

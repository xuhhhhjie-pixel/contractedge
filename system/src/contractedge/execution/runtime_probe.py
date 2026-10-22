"""Public HTTP-only temporal Alertmanager exercise; no private fixtures."""
ALERTMANAGER_RELOAD_GO = r'''package main
import (
 "bytes"
 "encoding/json"
 "fmt"
 "io"
 "net"
 "net/http"
 "os"
 "os/exec"
 "path/filepath"
 "sync"
 "time"
)
type check struct { ID string `json:"id"`; Status string `json:"status"`; Observed interface{} `json:"observed"` }
func fatal(v interface{}) { fmt.Fprintln(os.Stderr,"PROBE_ERROR:",v);os.Exit(2) }
func main(){
 dir,e:=os.MkdirTemp("","ce-am-public-");if e!=nil{fatal(e)};defer os.RemoveAll(dir)
 binary:=filepath.Join(dir,"alertmanager")
 build:=exec.Command("go","build","-p=1","-trimpath","-o",binary,"./cmd/alertmanager");build.Stdout=os.Stderr;build.Stderr=os.Stderr
 if e=build.Run();e!=nil{fatal(e)}
 config:=filepath.Join(dir,"alertmanager.yml")
 write:=func(i int){text:=fmt.Sprintf("global:\n  resolve_timeout: %dm\nroute:\n  receiver: null\n  group_by: [alertname]\n  group_wait: 10m\n  group_interval: 10m\n  repeat_interval: 1h\nreceivers:\n- name: null\n",5+i%2)
 // Quote null because receiver names are strings in public configuration.
 text=string(bytes.ReplaceAll([]byte(text),[]byte(": null"),[]byte(": 'null'")))
 if e=os.WriteFile(config,[]byte(text),0600);e!=nil{fatal(e)}}
 write(0)
 listener,e:=net.Listen("tcp","127.0.0.1:0");if e!=nil{fatal(e)};addr:=listener.Addr().String();listener.Close()
 process:=exec.Command(binary,"--config.file="+config,"--storage.path="+filepath.Join(dir,"data"),"--web.listen-address="+addr,"--cluster.listen-address=","--log.level=error")
 log,e:=os.Create(filepath.Join(dir,"server.log"));if e!=nil{fatal(e)};process.Stdout=log;process.Stderr=log
 if e=process.Start();e!=nil{fatal(e)}
 defer func(){process.Process.Kill();process.Wait();log.Close()}()
 client:=&http.Client{Timeout:2*time.Second};base:="http://"+addr
 request:=func(method,path string,data []byte)([]byte,int,error){q,e:=http.NewRequest(method,base+path,bytes.NewReader(data));if e!=nil{return nil,0,e};q.Header.Set("Content-Type","application/json");r,e:=client.Do(q);if e!=nil{return nil,0,e};defer r.Body.Close();b,e:=io.ReadAll(io.LimitReader(r.Body,1024*1024));return b,r.StatusCode,e}
 ready:=false
 for end:=time.Now().Add(20*time.Second);time.Now().Before(end);{_,code,err:=request("GET","/-/ready",nil);if err==nil&&code==200{ready=true;break};time.Sleep(50*time.Millisecond)}
 if !ready{b,_:=os.ReadFile(filepath.Join(dir,"server.log"));fatal("server readiness: "+string(b))}
 alert:=[]map[string]interface{}{};const expectedGroups=128;for i:=0;i<expectedGroups;i++{alert=append(alert,map[string]interface{}{"labels":map[string]string{"alertname":fmt.Sprintf("ContractEdgePublicReload-%03d",i),"instance":"local"},"annotations":map[string]string{},"startsAt":time.Now().Add(-time.Minute).UTC().Format(time.RFC3339Nano),"endsAt":time.Now().Add(10*time.Minute).UTC().Format(time.RFC3339Nano)})}
 data,_:=json.Marshal(alert);_,code,e:=request("POST","/api/v2/alerts",data);if e!=nil||code/100!=2{fatal(fmt.Sprint("post alert: ",code,e))}
 groups:=func()(bool,error){b,code,e:=request("GET","/api/v2/alerts/groups",nil);if e!=nil||code!=200{return false,fmt.Errorf("groups HTTP %d: %v",code,e)};var list []struct{Alerts []struct{Labels map[string]string `json:"labels"`} `json:"alerts"`};if e=json.Unmarshal(b,&list);e!=nil{return false,e};found:=map[string]bool{};for _,g:=range list{for _,a:=range g.Alerts{if a.Labels["instance"]=="local"{found[a.Labels["alertname"]]=true}}};for i:=0;i<expectedGroups;i++{if !found[fmt.Sprintf("ContractEdgePublicReload-%03d",i)]{return false,nil}};return true,nil}
 baseline:=false
 for end:=time.Now().Add(10*time.Second);time.Now().Before(end);{ok,e:=groups();if e!=nil{fatal(e)};if ok{baseline=true;break};time.Sleep(10*time.Millisecond)}
 if !baseline{fatal("public alert never formed a visible group")}
 var mu sync.Mutex;samples,missing:=0,0;var sampleError error
 stop:=make(chan struct{});var workers sync.WaitGroup;workers.Add(4)
 for worker:=0;worker<4;worker++{go func(){defer workers.Done();ticker:=time.NewTicker(2*time.Millisecond);defer ticker.Stop();for{select{case <-stop:return;case <-ticker.C:ok,e:=groups();mu.Lock();samples++;if e!=nil{sampleError=e};if e==nil&&!ok{missing++};mu.Unlock()}}}()}
 reloads:=0
 for i:=0;i<20;i++{time.Sleep(100*time.Millisecond);write(i+1);_,code,e:=request("POST","/-/reload",nil);if e!=nil||code/100!=2{close(stop);workers.Wait();fatal(fmt.Sprint("valid reload failed: ",code,e))};reloads++;time.Sleep(400*time.Millisecond)}
 close(stop);workers.Wait()
 if sampleError!=nil||samples<100{fatal(fmt.Sprint("sampling incomplete: ",samples,sampleError))}
 final,e:=groups();if e!=nil{fatal(e)}
 status:=func(v bool)string{if v{return "PASS"};return "FAIL"}
 observation:=map[string]interface{}{"baseline_visible":baseline,"injected_groups":expectedGroups,"successful_reloads":reloads,"samples":samples,"missing_samples":missing,"sampling_interval_ms":2,"sampling_workers":4,"after_visible":final,"scope":"Local complete production process and HTTP API, twenty valid reloads; sampled continuity is not a proof of unsampled instants or distributed convergence"}
 checks:=[]check{{"groups.visible_during_reload",status(missing==0),observation},{"groups.visible_after_reload",status(final),observation}}
 json.NewEncoder(os.Stdout).Encode(map[string]interface{}{"probe_id":"alertmanager-reload-v1","checks":checks})
}
'''
ALERTMANAGER_CHECKS = {
 'groups.visible_during_reload':'The injected active alert remains in the production groups API at each successful sample during twenty valid configuration reloads',
 'groups.visible_after_reload':'The injected active alert is visible in the production groups API after the reload sampling window',
}

# Keep v1 byte-identical. v2 detects response truncation explicitly and checks
# group uniqueness for this probe's declared single-route/group_by fixture.
_DECODE_PUBLIC_GROUPS = r'''
func decodePublicGroups(reader io.Reader)(bool,bool,int,error){
 const limit=8*1024*1024
 b,e:=io.ReadAll(io.LimitReader(reader,limit+1));if e!=nil{return false,false,len(b),e}
 if len(b)>limit{return false,false,len(b),fmt.Errorf("groups response exceeds %d bytes; incomplete observation",limit)}
 var list []struct{Labels map[string]string `json:"labels"`;Receiver struct{Name string `json:"name"`} `json:"receiver"`;Alerts []struct{Labels map[string]string `json:"labels"`} `json:"alerts"`}
 if e=json.Unmarshal(b,&list);e!=nil{return false,false,len(b),fmt.Errorf("groups JSON bytes=%d: %v",len(b),e)}
 found:=map[string]bool{};keys:=map[string]bool{};unique:=len(list)<=128
 for _,g:=range list{
  encoded,_:=json.Marshal([]interface{}{g.Labels,g.Receiver.Name});key:=string(encoded)
  if keys[key]||g.Receiver.Name!="null"{unique=false};keys[key]=true
  for _,a:=range g.Alerts{if a.Labels["instance"]=="local"{found[a.Labels["alertname"]]=true}}
 }
 visible:=true;for i:=0;i<128;i++{if !found[fmt.Sprintf("ContractEdgePublicReload-%03d",i)]{visible=false}}
 return visible,unique,len(b),nil
}
'''
_old_groups_line = next(line for line in ALERTMANAGER_RELOAD_GO.splitlines() if line.startswith(' groups:=func()'))
_new_groups_line = r''' groups:=func()(bool,bool,error){
 q,e:=http.NewRequest("GET",base+"/api/v2/alerts/groups",nil);if e!=nil{return false,false,e}
 r,e:=client.Do(q);if e!=nil{return false,false,e};defer r.Body.Close()
 if r.StatusCode!=200{return false,false,fmt.Errorf("groups HTTP %d",r.StatusCode)}
 visible,unique,_,e:=decodePublicGroups(r.Body);return visible,unique,e
 }'''
ALERTMANAGER_RELOAD_V2_GO = (ALERTMANAGER_RELOAD_GO
    .replace('func main(){', _DECODE_PUBLIC_GROUPS+'\nfunc main(){', 1)
    .replace(_old_groups_line, _new_groups_line)
    .replace('ok,e:=groups();if e!=nil{fatal(e)};if ok', 'ok,_,e:=groups();if e!=nil{fatal(e)};if ok')
    .replace('samples,missing:=0,0;', 'samples,missing,nonUnique:=0,0,0;')
    .replace('ok,e:=groups();mu.Lock();samples++;', 'ok,unique,e:=groups();mu.Lock();samples++;')
    .replace('if e==nil&&!ok{missing++};', 'if e==nil&&!ok{missing++};if e==nil&&!unique{nonUnique++};')
    .replace('fatal(fmt.Sprint("sampling incomplete: ",samples,sampleError))',
             'b,_:=os.ReadFile(filepath.Join(dir,"server.log"));if len(b)>65536{b=b[len(b)-65536:]};fatal(fmt.Sprint("sampling incomplete: ",samples,sampleError,"; production log: ",string(b)))')
    .replace('final,e:=groups();', 'final,finalUnique,e:=groups();')
    .replace('"after_visible":final,', '"after_visible":final,"non_unique_samples":nonUnique,"after_unique":finalUnique,')
    .replace('{"groups.visible_after_reload",status(final),observation}}',
             '{"groups.visible_after_reload",status(final),observation},{"groups.unique_for_declared_route",status(nonUnique==0&&finalUnique),observation}}')
    .replace('alertmanager-reload-v1', 'alertmanager-reload-v2'))
ALERTMANAGER_V2_CHECKS = {**ALERTMANAGER_CHECKS,
    'groups.unique_for_declared_route': 'In the declared single receiver and group_by alertname scenario, groups have unique label/receiver identities and no unexpected receiver or extra group'}

# v3 retains sampled continuity and adds an observation aligned with each
# successful reload return. Larger public input amplifies the documented replay
# work; it does not insert delays or change candidate production source.
ALERTMANAGER_RELOAD_V3_GO = (ALERTMANAGER_RELOAD_V2_GO
    .replace('alertmanager-reload-v2','alertmanager-reload-v3')
    .replace('128','2048')
    .replace('samples,missing,nonUnique:=0,0,0;',
             'samples,missing,nonUnique:=0,0,0;returnSamples,returnMissing:=0,0;')
    .replace('reloads++;time.Sleep(400*time.Millisecond)',
             'reloads++;ok,unique,err:=groups();if err!=nil{close(stop);workers.Wait();fatal(fmt.Sprint("reload-return observation: ",err))};returnSamples++;if !ok{returnMissing++};mu.Lock();if !unique{nonUnique++};mu.Unlock();time.Sleep(400*time.Millisecond)')
    .replace('"after_unique":finalUnique,',
             '"after_unique":finalUnique,"at_return_samples":returnSamples,"at_return_missing":returnMissing,')
    .replace('{"groups.unique_for_declared_route",status(nonUnique==0&&finalUnique),observation}}',
             '{"groups.unique_for_declared_route",status(nonUnique==0&&finalUnique),observation},{"groups.visible_at_reload_return",status(returnSamples==20&&returnMissing==0),observation}}')
    .replace('twenty valid reloads; sampled continuity',
             'twenty valid reloads with2048 groups and immediate reload-return samples; sampled continuity'))
ALERTMANAGER_V3_CHECKS = {**ALERTMANAGER_V2_CHECKS,
    'groups.visible_at_reload_return': 'Immediately after each of twenty successful reload responses, the production groups API contains all2048 injected active alert names'}

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

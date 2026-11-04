"""Public CloudWatch controller Update probe; SDK IO is local, not cloud convergence."""
CLOUDWATCH_TAG_CHECKS = {
 'addition':'Changed declared tags add a key to the external alarm tag set',
 'value_change':'Changed declared tag values replace the external values',
 'removal':'Changed declared tags remove a previously declared key',
 'remove_all':'An explicitly empty changed declaration removes the previous declared tags',
 'unchanged':'Equal desired/latest tags keep equal external tags intact',
 'unchanged_other_field':'A non-tag update with equal desired/latest tags preserves the existing external tag set, including a separately present key',
 'put_error':'A failed alarm update propagates its error without tag mutation',
 'tag_error':'A failed tag mutation propagates its error rather than reporting successful update',
}
CLOUDWATCH_TAG_GO = r'''package metric_alarm
import (
 "context"
 "encoding/json"
 "fmt"
 "io"
 "net/http"
 "reflect"
 "sort"
 "strings"
 "testing"
 smithycbor "github.com/aws/smithy-go/encoding/cbor"
 ackmetrics "github.com/aws-controllers-k8s/runtime/pkg/metrics"
 svcapitypes "github.com/aws-controllers-k8s/cloudwatch-controller/apis/v1alpha1"
 "github.com/aws/aws-sdk-go-v2/aws"
 svcsdk "github.com/aws/aws-sdk-go-v2/service/cloudwatch"
)
type ceTagTransport struct { tags map[string]string; calls []string; writes int; failPut,failTag bool; unsupported,injected string }
func ceCopy(in map[string]string) map[string]string {o:=map[string]string{};for k,v:=range in{o[k]=v};return o}
func (tr *ceTagTransport) RoundTrip(r *http.Request)(*http.Response,error){
 fail:=func(reason string)(*http.Response,error){tr.unsupported=reason;return nil,fmt.Errorf("public probe transport: %s",reason)}
 if r.URL.Host!="cloudwatch.public-probe.invalid" {return fail("unexpected host")}
 if r.Header.Get("smithy-protocol")!="rpc-v2-cbor" {return fail("unsupported wire protocol")}
 raw,e:=io.ReadAll(io.LimitReader(r.Body,1048577));if e!=nil{return fail(e.Error())};if len(raw)>1048576{return fail("request too large")}
 decoded,e:=smithycbor.Decode(raw);if e!=nil{return fail(e.Error())};values,ok:=decoded.(smithycbor.Map);if !ok{return fail("request is not a map")}
 parts:=strings.Split(r.URL.Path,"/");action:=parts[len(parts)-1];tr.calls=append(tr.calls,action)
 status:=200;body:=smithycbor.Map{}
 switch action {
 case "PutMetricAlarm":
  // The public field contract says Tags are ignored for existing alarms.
  if tr.failPut {status=400;tr.injected=action}
 case "ListTagsForResource":
  tags:=smithycbor.List{};keys:=[]string{};for k:=range tr.tags{keys=append(keys,k)};sort.Strings(keys)
  for _,k:=range keys{tags=append(tags,smithycbor.Map{"Key":smithycbor.String(k),"Value":smithycbor.String(tr.tags[k])})};body["Tags"]=tags
 case "TagResource":
  if tr.failTag {status=400;tr.injected=action;break}
  tags,ok:=values["Tags"].(smithycbor.List);if !ok{return fail("missing SDK tag list")}
  tr.writes++
  for _,v:=range tags{tag,ok:=v.(smithycbor.Map);if !ok{return fail("invalid SDK tag")};key,_:=tag["Key"].(smithycbor.String);value,_:=tag["Value"].(smithycbor.String);tr.tags[string(key)]=string(value)}
 case "UntagResource":
  if tr.failTag {status=400;tr.injected=action;break}
  keys,ok:=values["TagKeys"].(smithycbor.List);if !ok{return fail("missing SDK tag keys")}
  tr.writes++
  for _,v:=range keys{key,ok:=v.(smithycbor.String);if !ok{return fail("invalid SDK key")};delete(tr.tags,string(key))}
 default:return fail("unsupported SDK operation "+action)
 }
 // Typed InternalServiceFault reads Message; generic operation errors read message.
 // Supply both so the fixed injection marker survives either public SDK decoder.
 if status==400 {message:=smithycbor.String("public probe injected "+tr.injected+" failure");body=smithycbor.Map{"__type":smithycbor.String("com.amazonaws.cloudwatch#InternalServiceFault"),"message":message,"Message":message}}
 header:=http.Header{};header.Set("Content-Type","application/cbor");header.Set("smithy-protocol","rpc-v2-cbor")
 return &http.Response{StatusCode:status,Status:fmt.Sprint(status),Header:header,Body:io.NopCloser(strings.NewReader(string(smithycbor.Encode(body)))),Request:r},nil
}
func ceResource(tags map[string]string)*resource {
 r:=&resource{ko:&svcapitypes.MetricAlarm{}}
 r.ko.Spec.Name=aws.String("public-alarm");r.ko.Spec.AlarmDescription=aws.String("before")
 keys:=[]string{};for k:=range tags{keys=append(keys,k)};sort.Strings(keys)
 for _,k:=range keys{r.ko.Spec.Tags=append(r.ko.Spec.Tags,&svcapitypes.Tag{Key:aws.String(k),Value:aws.String(tags[k])})}
 return r
}
func TestContractEdgePublicTagUpdate(t *testing.T){
 type scenario struct {id string;latest,desired,external map[string]string;other,failPut,failTag bool}
 base:=map[string]string{"env":"prod"}
 scenarios:=[]scenario{
  {id:"addition",latest:base,desired:map[string]string{"env":"prod","owner":"team"},external:base},
  {id:"value_change",latest:base,desired:map[string]string{"env":"stage"},external:base},
  {id:"removal",latest:map[string]string{"env":"prod","old":"remove"},desired:base,external:map[string]string{"env":"prod","old":"remove"}},
  {id:"remove_all",latest:base,desired:map[string]string{},external:base},
  {id:"unchanged",latest:base,desired:base,external:base,other:true},
  {id:"unchanged_other_field",latest:base,desired:base,external:map[string]string{"env":"prod","external":"retain"},other:true},
  {id:"put_error",latest:base,desired:map[string]string{"env":"stage"},external:base,failPut:true},
  {id:"tag_error",latest:base,desired:map[string]string{"env":"stage"},external:base,failTag:true},
 }
 results:=[]map[string]interface{}{};metrics:=ackmetrics.NewMetrics("cloudwatch-public-probe")
 for _,s:=range scenarios {
  tr:=&ceTagTransport{tags:ceCopy(s.external),failPut:s.failPut,failTag:s.failTag}
  client:=svcsdk.NewFromConfig(aws.Config{Region:"us-east-1",HTTPClient:&http.Client{Transport:tr},Credentials:aws.CredentialsProviderFunc(func(context.Context)(aws.Credentials,error){return aws.Credentials{AccessKeyID:"PUBLICPROBE",SecretAccessKey:"public-probe-fixture",Source:"public-probe"},nil})},func(o *svcsdk.Options){o.BaseEndpoint=aws.String("https://cloudwatch.public-probe.invalid");o.RetryMaxAttempts=1})
  rm:=&resourceManager{sdkapi:client,metrics:metrics,awsPartition:"aws",awsRegion:"us-east-1",awsAccountID:"123456789012"}
  desired,latest:=ceResource(s.desired),ceResource(s.latest);if s.other{desired.ko.Spec.AlarmDescription=aws.String("after")}
  delta:=newResourceDelta(desired,latest)
  _,err:=rm.Update(context.Background(),desired,latest,delta)
  if tr.unsupported!=""{t.Fatalf("PUBLIC_TRANSPORT_UNSUPPORTED: %s",tr.unsupported)}
  ok:=err==nil && reflect.DeepEqual(tr.tags,s.desired)
  if s.other{ok=err==nil && reflect.DeepEqual(tr.tags,s.external) && tr.writes==0}
  if s.failPut{ok=err!=nil && tr.injected=="PutMetricAlarm" && strings.Contains(err.Error(),"public probe injected") && reflect.DeepEqual(tr.tags,s.external) && tr.writes==0}
  if s.failTag{ok=err!=nil && (tr.injected=="TagResource" || tr.injected=="UntagResource") && strings.Contains(err.Error(),"public probe injected") && reflect.DeepEqual(tr.tags,s.external)}
  status:="FAIL";if ok{status="PASS"}
  results=append(results,map[string]interface{}{"id":s.id,"status":status,"observed":map[string]interface{}{"desired":s.desired,"latest":s.latest,"external_before":s.external,"external_after":tr.tags,"sdk_operations":tr.calls,"tag_writes":tr.writes,"error":fmt.Sprint(err),"other_field_changed":s.other}})
 }
 b,err:=json.Marshal(map[string]interface{}{"probe_id":"cloudwatch-tags-v1","checks":results});if err!=nil{t.Fatal(err)};fmt.Println(string(b))
}
'''


def _existing_alarm_program():
    """Keep v1 exact for replay; v2 supplies and checks the managed identity."""
    program=CLOUDWATCH_TAG_GO.replace('cloudwatch-tags-v1','cloudwatch-tags-v2')
    replacements=[
        (' smithycbor "github.com/aws/smithy-go/encoding/cbor"',
         ' ackv1alpha1 "github.com/aws-controllers-k8s/runtime/apis/core/v1alpha1"\n smithycbor "github.com/aws/smithy-go/encoding/cbor"'),
        ('type ceTagTransport struct',
         'const ceAlarmARN="arn:aws:cloudwatch:us-east-1:123456789012:alarm:public-alarm"\n'
         'func ceTargetValid(tr *ceTagTransport,valid bool)bool{return valid && tr.wrongTarget==""}\n'
         'func ceHTTPReply(r *http.Request,status int,body smithycbor.Map)*http.Response{\n'
         ' header:=http.Header{};header.Set("Content-Type","application/cbor");header.Set("smithy-protocol","rpc-v2-cbor")\n'
         ' return &http.Response{StatusCode:status,Status:fmt.Sprint(status),Header:header,Body:io.NopCloser(strings.NewReader(string(smithycbor.Encode(body)))),Request:r}\n}\n'
         'type ceTagTransport struct'),
        ('failPut,failTag bool; unsupported,injected string',
         'failPut,failTag bool; unsupported,injected,wrongTarget string'),
        (' status:=200;body:=smithycbor.Map{}',r''' // A valid SDK request for the wrong alarm is candidate behavior failure,
 // not an unsupported probe protocol. Never mutate the fixture's other resource.
 invalidTarget:=false
 if action=="PutMetricAlarm"{name,_:=values["AlarmName"].(smithycbor.String);invalidTarget=string(name)!="public-alarm"}
 if action=="ListTagsForResource" || action=="TagResource" || action=="UntagResource"{arn,_:=values["ResourceARN"].(smithycbor.String);invalidTarget=string(arn)!=ceAlarmARN}
 if invalidTarget{tr.wrongTarget=action+" "+fmt.Sprint(values);return ceHTTPReply(r,400,smithycbor.Map{"__type":smithycbor.String("ValidationException"),"message":smithycbor.String("public probe rejected wrong alarm identity"),"Message":smithycbor.String("public probe rejected wrong alarm identity")}),nil}
 status:=200;body:=smithycbor.Map{}'''),
        (' header:=http.Header{};header.Set("Content-Type","application/cbor");header.Set("smithy-protocol","rpc-v2-cbor")\n return &http.Response{StatusCode:status,Status:fmt.Sprint(status),Header:header,Body:io.NopCloser(strings.NewReader(string(smithycbor.Encode(body)))),Request:r},nil',
         ' return ceHTTPReply(r,status,body),nil'),
        (' r.ko.Spec.Name=aws.String("public-alarm");',
         ' arn:=ackv1alpha1.AWSResourceName(ceAlarmARN);r.ko.Status.ACKResourceMetadata=&ackv1alpha1.ResourceMetadata{ARN:&arn}\n'
         ' r.ko.Spec.ComparisonOperator=aws.String("GreaterThanThreshold");r.ko.Spec.EvaluationPeriods=aws.Int64(1);r.ko.Spec.Period=aws.Int64(60)\n'
         ' r.ko.Spec.Namespace=aws.String("ContractEdge/PublicProbe");r.ko.Spec.MetricName=aws.String("Latency");r.ko.Spec.Statistic=aws.String("Average");r.ko.Spec.Threshold=aws.Float64(1)\n'
         ' r.ko.Spec.Name=aws.String("public-alarm");'),
        ('  status:="FAIL";if ok{status="PASS"}',
         '  ok=ceTargetValid(tr,ok)\n  status:="FAIL";if ok{status="PASS"}'),
        ('"sdk_operations":tr.calls,"tag_writes":tr.writes',
         '"sdk_operations":tr.calls,"wrong_target_request":tr.wrongTarget,"tag_writes":tr.writes'),
        (' b,err:=json.Marshal(map[string]interface{}{"probe_id":',r''' t.Run("transport-target-identity",func(t *testing.T){
  for _,operation:=range []string{"PutMetricAlarm","ListTagsForResource","TagResource","UntagResource"}{
   tr:=&ceTagTransport{tags:ceCopy(base)}
   payload:=smithycbor.Map{"ResourceARN":smithycbor.String("wrong-resource"),"AlarmName":smithycbor.String("wrong-alarm"),"Tags":smithycbor.List{smithycbor.Map{"Key":smithycbor.String("env"),"Value":smithycbor.String("changed")}},"TagKeys":smithycbor.List{smithycbor.String("env")}}
   req,err:=http.NewRequest("POST","https://cloudwatch.public-probe.invalid/service/GraniteServiceVersion20100801/operation/"+operation,strings.NewReader(string(smithycbor.Encode(payload))));if err!=nil{t.Fatal(err)}
   req.Header.Set("smithy-protocol","rpc-v2-cbor");response,err:=tr.RoundTrip(req)
   if err!=nil || response.StatusCode!=400 || tr.unsupported!="" || tr.writes!=0 || !reflect.DeepEqual(tr.tags,base){t.Fatalf("wrong target was not rejected for %s",operation)};response.Body.Close()
   if ceTargetValid(tr,true){t.Fatalf("swallowed wrong-target error accepted for %s",operation)}
  }
 })
 t.Run("valid-managed-alarm-input",func(t *testing.T){
  r:=ceResource(base)
  if r.ko.Status.ACKResourceMetadata==nil || r.ko.Status.ACKResourceMetadata.ARN==nil{t.Fatal("existing alarm identity missing")}
  s:=r.ko.Spec
  if s.MetricName==nil || s.Namespace==nil || s.ComparisonOperator==nil || s.EvaluationPeriods==nil || s.Period==nil || s.Statistic==nil || s.Threshold==nil{t.Fatal("basic static alarm input missing")}
 })
 b,err:=json.Marshal(map[string]interface{}{"probe_id":''')]
    for before,after in replacements:
        if program.count(before)!=1:raise ValueError('CloudWatch v2 program anchor is ambiguous')
        program=program.replace(before,after,1)
    return program


CLOUDWATCH_TAG_V2_GO = _existing_alarm_program()

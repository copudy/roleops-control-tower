"""Readonly probes and engineer publishing. Never invokes Bedrock or a business tool."""
import argparse,asyncio,json,os,time,urllib.request
from pathlib import Path
from .config import Settings
from .showcase_content import publish,validate,atomic_json

async def probe(cfg):
    from .store import Store
    from .knowledge import Knowledge
    from .ops import check_mcp
    from .observability import emit
    from .domain import uid
    checks=[];started=time.time()
    def add(id,title,fn):
        try:status,detail=fn();checks.append({'id':id,'title':title,'status':status,'detail':detail})
        except Exception as exc:checks.append({'id':id,'title':title,'status':'unknown','detail':'檢查未完成：'+type(exc).__name__})
    def web():
        req=urllib.request.Request('http://127.0.0.1:8000/healthz',headers={'X-RoleOps-Origin':cfg.origin_secret})
        with urllib.request.urlopen(req,timeout=5) as resp:body=json.loads(resp.read())
        return ('ok' if body.get('web')=='ready' and body.get('storage')=='ready' else 'attention','網站與基本資料讀取；範圍之外：模型品質已通過')
    add('web','網站能回應嗎？',web)
    def index():
        knowledge=Knowledge(cfg,Store(cfg));result=knowledge.inspect_index();current=result.get('digest')==knowledge.source_digest(knowledge.sources())
        return ('ok' if result.get('integrity_ready') and current else 'attention','索引完整性：'+str(result.get('status'))+'；來源版本'+('已核對' if current else '已異動，需查核索引'))
    add('index','文件能支持問答嗎？',index)
    try:
        result=await asyncio.wait_for(check_mcp(cfg),timeout=8)
        checks.append({'id':'mcp','title':'申請窗口能連線嗎？','status':'ok','detail':'MCP 初始化與工具目錄通過；沒有建申請'})
    except Exception as exc:checks.append({'id':'mcp','title':'申請窗口能連線嗎？','status':'unknown','detail':'MCP 檢查未完成：'+type(exc).__name__})
    if cfg.mode=='aws':
        import boto3
        from botocore.config import Config
        client_config=Config(connect_timeout=3,read_timeout=5,retries={'total_max_attempts':1})
        def ec2():
            instance=os.getenv('ROLEOPS_INSTANCE_ID','')
            if not instance:raise ValueError('instance not configured')
            rows=boto3.client('ec2',region_name=cfg.region,config=client_config).describe_instance_status(InstanceIds=[instance],IncludeAllInstances=True)['InstanceStatuses']
            good=bool(rows) and all(r.get('InstanceStatus',{}).get('Status')=='ok' and r.get('SystemStatus',{}).get('Status')=='ok' for r in rows)
            return ('ok' if good else 'attention','AWS EC2 系統／執行個體檢查；範圍之外：應用或模型健康')
        add('ec2','主機是否可用？',ec2)
        def queues():
            urls=[cfg.queue,os.getenv('ROLEOPS_DLQ_URL','')]
            if not all(urls):raise ValueError('queue not configured')
            client=boto3.client('sqs',region_name=cfg.region,config=client_config)
            values=[client.get_queue_attributes(QueueUrl=u,AttributeNames=['ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible'])['Attributes'] for u in urls]
            counts=[int(x['ApproximateNumberOfMessages'])+int(x['ApproximateNumberOfMessagesNotVisible']) for x in values]
            return ('attention' if any(counts) else 'ok',f'主佇列約 {counts[0]} 筆／DLQ 約 {counts[1]} 筆；有訊息需看持續時間，不直接認定失敗')
        add('sqs','文件更新有沒有待處理？',queues)
        def alarms():
            names=['roleops-mcp-timeout','roleops-index-failure','roleops-ec2-statuscheck','roleops-selfcheck-heartbeat']
            rows=boto3.client('cloudwatch',region_name=cfg.region,config=client_config).describe_alarms(AlarmNames=names)['MetricAlarms']
            if len(rows)!=len(names):return 'unknown','指定告警尚未完整建立；請回查 Step 19 與擴展手冊'
            states=[r['StateValue'] for r in rows]
            status='attention' if 'ALARM' in states else 'unknown' if 'INSUFFICIENT_DATA' in states else 'ok'
            if not all(r.get('ActionsEnabled') for r in rows):return 'unknown','告警動作暫停；請核對維護時段與恢復'
            return status,'告警：'+', '.join(states)+'；郵件是否收到仍需人工驗證'
        add('alarms','異常告警有什麼狀態？',alarms)
    else:
        for id,title in [('ec2','主機是否可用？'),('sqs','文件更新有沒有待處理？'),('alarms','異常告警有什麼狀態？')]:checks.append({'id':id,'title':title,'status':'unknown','detail':'本機模式沒有 AWS 服務證據'})
    checks.append({'id':'model','title':'模型回答品質通過了嗎？','status':'unknown','detail':'自檢不呼叫模型；由雙案例與 Prompt A/B 人工驗收'})
    data={'checked_at':time.time(),'started_at':started,'checks':checks,'source':'readonly-probe','fresh':True}
    atomic_json(cfg.root/'selfcheck.json',data)
    emit(cfg,'app','self_check','complete',uid(),{'SelfCheckHeartbeat':1})
    if cfg.mode == 'aws':
        from .maintenance_dashboard import refresh
        try:
            refresh(cfg, boto3.client('cloudwatch', region_name=cfg.region, config=client_config),
                    os.getenv('ROLEOPS_INSTANCE_ID', ''))
        except (OSError, ValueError):
            # A dashboard write failure must not invalidate a completed self-check.
            pass
    return data

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['publish','validate','probe']);p.add_argument('--root',default='/opt/roleops/showcase');p.add_argument('--version');args=p.parse_args()
    if args.action=='publish':result=publish(args.root,args.version)
    elif args.action=='validate':result={'version':validate(Path(args.root)/'releases'/args.version)[0]['version']}
    else:cfg=Settings();cfg.validate();result=asyncio.run(probe(cfg))
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

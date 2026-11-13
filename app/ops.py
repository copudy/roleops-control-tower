import argparse,asyncio,json,getpass,hashlib,tarfile,tempfile,shutil,time
from pathlib import Path
from .config import Settings,PERMISSIONS,ROLES
from .store import Store
from .domain import Domain,uid
from .knowledge import Knowledge
from .agent import Agent

async def check_mcp(cfg):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(cfg.mcp_url) as (r,w,_):
        async with ClientSession(r,w) as session:
            await session.initialize();tools=await session.list_tools();names=[t.name for t in tools.tools]
            if names!=['create_access_request']:raise ValueError('Wrong tools/list')
            return {'mcp':'ready','tools':names}

from .backup_restore import backup,restore

async def evaluate(cfg,kb,args):
    from .config import AGENT_PROFILES
    suite=json.loads((Path(__file__).parent.parent/'evaluations/fixed-10.json').read_text(encoding='utf-8'));results=[];pinned=kb.status().get('generation')
    interval=float(getattr(args,'min_interval',20)) if cfg.generation_provider=='groq' else 0
    if not __import__('math').isfinite(interval) or not 0 <= interval <= 60:raise ValueError('Invalid evaluation min interval')
    last_call=None
    for case in suite:
        if last_call is not None and interval:
            await asyncio.sleep(max(0,interval-(time.monotonic()-last_call)))
        if kb.status().get('generation')!=pinned:raise ValueError('Evaluation source generation changed')
        u={'role':case['role'],'department':case['department'],'title':case['title']};scope={'knowledge':ROLES[u['role']]['knowledge']};start=time.monotonic()
        from .synthetic_policy import inspect_user_text,PolicyDenied,SAFE_REPLY
        refusal=None
        try:inspect_user_text(case['question'],u,'evaluation_question')
        except PolicyDenied as ex:refusal=ex.code
        if refusal:chunks=[];generation=pinned
        else:chunks,generation=kb.retrieve(case['question'],u,scope)
        sources=[x['document']['document_id'] for x in chunks]
        answer=SAFE_REPLY if refusal else '資料不足';usage=None
        if chunks:
            if cfg.generation_provider=='groq':
                from .generation import generate
                prompt=(Path(__file__).parent.parent/'prompts'/f'{args.prompt}.txt').read_text(encoding='utf-8')
                context='\n\n'.join(f"[來源 {i+1}: {x['document']['document_id']} 版本 {x['document']['policy_version']}]\n{x['text']}" for i,x in enumerate(chunks))
                last_call=time.monotonic()
                def assert_live():
                    kb.assert_current(chunks,generation)
                response=await generate(cfg,prompt,context,[],case['question'],ROLES[u['role']]['systems'],security_context={'prompt':prompt,'chunks':chunks,'user':u,'assert_current':assert_live})
                answer=response['answer'];usage=response['usage']
                if response['proposal']:raise ValueError('Unexpected tool proposal in fixed knowledge-only evaluation')
            elif cfg.mode=='aws':
                from langchain_aws import ChatBedrockConverse
                from langchain_core.messages import SystemMessage,HumanMessage
                prompt=(Path(__file__).parent.parent/'prompts'/f'{args.prompt}.txt').read_text(encoding='utf-8');context='\n'.join(x['text'] for x in chunks)
                r=await ChatBedrockConverse(model=cfg.model,region_name=cfg.region,max_tokens=800,temperature=0).ainvoke([SystemMessage(content=prompt+'\n文件：\n'+context),HumanMessage(content=case['question'])]);answer=str(r.content);usage=r.usage_metadata
            else:answer='\n'.join(x['text'][:300] for x in chunks)
        kb.assert_current(chunks,generation)
        expected=case.get('expected_document');hit=expected in sources if expected else not any(x in sources for x in case.get('forbidden_documents',[]))
        results.append(dict(case,answer=answer,sources=sources,blocked_before_generation=bool(refusal),refusal_reason=refusal,retrieval_check=hit,latency_ms=round((time.monotonic()-start)*1000,1),usage=usage,generation=generation,prompt=args.prompt,model=cfg.groq_model if cfg.generation_provider=='groq' else cfg.model if cfg.mode=='aws' else 'local-synthetic',answer_correctness='requires-human-review'))
    answerable=[x for x in results if x.get('expected_document')];boundary=[x for x in results if x.get('forbidden_documents')]
    report={'cases':results,'retrieval_pass_rate':sum(x['retrieval_check'] for x in answerable)/len(answerable),'boundary_pass_rate':sum(x['retrieval_check'] for x in boundary)/len(boundary),'mean_latency_ms':sum(x['latency_ms'] for x in results)/len(results),'mode':cfg.mode,'prompt':args.prompt,'generation':pinned,'model':cfg.groq_model if cfg.generation_provider=='groq' else cfg.model if cfg.mode=='aws' else 'local-synthetic'}
    report['generation_min_interval_seconds']=interval
    folder=cfg.root/'reports';folder.mkdir(exist_ok=True);path=folder/f'evaluation-{args.prompt}-{uid()}.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');return {'report':str(path),'retrieval_pass_rate':report['retrieval_pass_rate'],'answer_correctness':'請人工逐題審閱'}

def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('bootstrap-admin');r=sub.add_parser('reindex');r.add_argument('--reconcile',action='store_true')
    sub.add_parser('check-mcp');sub.add_parser('purge-due');sub.add_parser('index-status');sub.add_parser('index-plan')
    b=sub.add_parser('backup');b.add_argument('--label',required=True)
    r=sub.add_parser('restore');r.add_argument('--backup',required=True);r.add_argument('--verify-only',action='store_true');r.add_argument('--isolated',action='store_true');r.add_argument('--target-table')
    e=sub.add_parser('evaluate');e.add_argument('--prompt',choices=['A','B'],required=True);e.add_argument('--suite',choices=['fixed-10'],default='fixed-10');e.add_argument('--namespace',choices=['eval'],default='eval');e.add_argument('--min-interval',type=float,default=20)
    s=sub.add_parser('seed');s.add_argument('--isolated-test',action='store_true',required=True);s.add_argument('--namespace',choices=['eval'],default='eval');s.add_argument('--dataset',default='synthetic-v1.3')
    c=sub.add_parser('check-request');c.add_argument('--idempotency-key',required=True)
    args=p.parse_args();cfg=Settings();cfg.validate();db=Store(cfg);domain=Domain(db);kb=Knowledge(cfg,db)
    if args.action=='bootstrap-admin':
        login=input('網站管理員帳號：').strip();password=getpass.getpass('網站管理員密碼（至少 10 字元）：');confirm=getpass.getpass('再次輸入：')
        if password!=confirm:raise SystemExit('Passwords do not match')
        result=domain.create_user(None,login,'平台管理員','平台管理','管理員','supervisor',password,list(PERMISSIONS),bootstrap=True)
    elif args.action=='reindex':result=kb.rebuild(reconcile=args.reconcile)
    elif args.action=='index-status':
        result=kb.inspect_index()
        print(json.dumps(result,ensure_ascii=False))
        if not result['integrity_ready']:raise SystemExit(2)
        return
    elif args.action=='index-plan':result=kb.plan_rebuild()
    elif args.action=='check-mcp':result=asyncio.run(check_mcp(cfg))
    elif args.action=='purge-due':result={'purged':domain.purge_due()}
    elif args.action=='seed':
        cases=json.loads((Path(__file__).parent.parent/'evaluations/fixed-10.json').read_text(encoding='utf-8'))
        if len(cases)!=10:raise ValueError('Fixed suite requires 10 cases')
        folder=cfg.root/'evaluations';folder.mkdir(exist_ok=True);(folder/'fixed-10.json').write_text(json.dumps(cases,ensure_ascii=False),encoding='utf-8')
        result={'isolated_fixture':True,'suite':'fixed-10','cases':len(cases),'company_accounts_created':0}
    elif args.action=='evaluate':result=asyncio.run(evaluate(cfg,kb,args))
    elif args.action=='backup':result=backup(cfg,db,kb,args.label)
    elif args.action=='restore':result=restore(cfg,db,kb,args)
    elif args.action=='check-request':result=[{'id':r['id'],'status':r['status']} for _,r,_ in db.scan('request:') if r['idempotency_key']==args.idempotency_key]
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()

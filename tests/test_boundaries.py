import asyncio,shutil
from dataclasses import replace
import pytest
from app.domain import Denied
from app.knowledge import Knowledge
from app.agent import Agent
from app.store import Conflict
def test_unready_index_is_service_failure_not_authorization_denial(env,caplog):
 from fastapi.testclient import TestClient
 from app.web import create_app
 cfg,db,d,a,e=env;client=TestClient(create_app(cfg));r=client.post('/api/auth/login',json={'login':'employee','password':'secure-password-123'});csrf=r.json()['csrf'];c=d.new_conversation(e,'工作')
 caplog.set_level('INFO',logger='roleops');r=client.post('/api/conversations/'+c['id']+'/messages',json={'question':'CRM 申請規則'},headers={'x-csrf-token':csrf});assert r.status_code==503;assert 'KnowledgeUnavailable' in caplog.text;assert 'authorization' not in caplog.text
def test_employee_history_redacted_and_manager_self_hidden_read(env):
 from fastapi.testclient import TestClient
 from app.web import create_app
 cfg,db,d,a,e=env;kb=Knowledge(cfg,db);kb.rebuild();c=d.new_conversation(e,'交接');asyncio.run(Agent(cfg,d,kb).reply(e,c['id'],'CRM 存取規則'))
 d.edit_user(a,e['id'],{'role':'it'});client=TestClient(create_app(cfg));client.post('/api/auth/login',json={'login':'employee','password':'secure-password-123'});messages=client.get('/api/conversations/'+c['id']+'/messages').json();assert messages[0]['answer']=='';assert client.get('/api/conversations?management=true').status_code==403
 client.post('/api/auth/login',json={'login':'admin','password':'secure-password-123'});assert 'CRM' in client.get('/api/conversations/'+c['id']+'/messages?management=true').json()[0]['answer']
 own=d.new_conversation(a,'管理工作');d.edit_conversation(a,own['id'],'hide');assert client.get('/api/conversations/'+own['id']+'/messages?management=true').status_code==200
def test_delete_race_prevents_late_answer_write(env):
 cfg,db,d,a,e=env;kb=Knowledge(cfg,db);kb.rebuild();c=d.new_conversation(e,'交接');original=kb.assert_current;count=0
 def check(chunks,generation):
  nonlocal count
  original(chunks,generation);count+=1
  if count==2:d.edit_conversation(a,c['id'],'delete',reason='並發清理')
 kb.assert_current=check
 with pytest.raises(Conflict):asyncio.run(Agent(cfg,d,kb).reply(e,c['id'],'CRM 規則'))
 assert not db.scan('message:'+c['id']);assert db.get('conversation:'+c['id'])[0]['state']=='deleted'
def test_s3_version_change_blocks_stale_answer(env):
 import boto3
 from moto import mock_aws
 cfg,db,d,a,e=env
 with mock_aws():
  s3=boto3.client('s3',region_name='us-east-1');s3.create_bucket(Bucket='roleops-test-docs');s3.put_bucket_versioning(Bucket='roleops-test-docs',VersioningConfiguration={'Status':'Enabled'})
  content=(cfg.knowledge/'CRM 存取規則 2026.md').read_bytes();s3.put_object(Bucket='roleops-test-docs',Key='knowledge/crm.md',Body=content)
  kb=Knowledge(replace(cfg,mode='aws',bucket='roleops-test-docs'),db);local=Knowledge(cfg,db);kb.embedding=lambda text,purpose='document': local.embedding(text,purpose)*8;kb.rebuild();chunks,gen=kb.retrieve('CRM 申請',e,d.scope(e));assert chunks
  s3.put_object(Bucket='roleops-test-docs',Key='knowledge/crm.md',Body=content+b'changed')
  with pytest.raises(Denied):kb.assert_current(chunks,gen)

@pytest.mark.parametrize('question,expected_status',[
 ('CRM 申請條件與流程',200),
 ('CRM 申請條件與流程及收費多少？',200),
 ('CRM 申請資格與流程是什麼？主管核准通常需要幾天？',200),
 ('依現行 CRM 存取規則，申請需要哪些條件？核准是否等於已經開通？',200),
])
def test_generation_agent_and_web_share_one_safe_trace(env,caplog,monkeypatch,question,expected_status):
 import json
 from fastapi.testclient import TestClient
 from app.web import create_app
 from app.grounding import facts_for
 from app.generation import wire_fact_map
 cfg,db,d,a,e=env
 cfg=replace(cfg,generation_provider='groq',metrics_enabled=False)
 kb=Knowledge(cfg,db);kb.rebuild()
 chunks,_=kb.retrieve(question,e,d.scope(e))
 facts=facts_for(chunks)
 ids=[wire_id for wire_id,key in wire_fact_map(chunks).items() if facts[key]['document_id']=='crm-access']
 class Response:
  status=200
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def read(self,*a):return json.dumps({'choices':[{'message':{'content':json.dumps({'status':'supported','facts':ids,'suggestions':[]})},'finish_reason':'stop'}],'usage':{}}).encode()
 monkeypatch.setattr('app.generation.read_secret',lambda _: 'fixture-secret-do-not-log')
 monkeypatch.setattr('app.generation.urllib.request.urlopen',lambda *a,**kw:Response())
 client=TestClient(create_app(cfg));login=client.post('/api/auth/login',json={'login':'employee','password':'secure-password-123'})
 c=d.new_conversation(e,'追蹤驗證');caplog.set_level('INFO',logger='roleops')
 response=client.post('/api/conversations/'+c['id']+'/messages',json={'question':question},headers={'x-csrf-token':login.json()['csrf']})
 assert response.status_code==expected_status,response.json()
 trace=response.json()['trace_id'];events=[]
 for record in caplog.records:
  try:events.append(json.loads(record.message))
  except ValueError:pass
 observed=[x for x in events if x.get('component') in ('generation','app') and x.get('action') in ('inference','query','knowledge')]
 assert observed and all(x['trace_id']==trace for x in observed)
 assert len([x for x in observed if x['action']=='inference'])==1
 status=json.loads((cfg.root/'provider-status.json').read_text())
 assert status['trace_id']==trace
 assert 'fixture-secret-do-not-log' not in caplog.text and question not in caplog.text
 assert not db.scan('request:')
 if '收費多少' in question or '幾天' in question:
  assert response.json()['grounding']['status']=='insufficient'
  assert not response.json().get('proposal')
  assert not response.json()['grounding']['coverage']['complete']
  assert status['phase']=='not_invoked' and status['attempt']==0
  assert response.json()['grounding']['reason_code'] in ('coverage_unresolved','evidence_missing')
 else:
  assert response.json()['grounding']['coverage']['complete']


def test_generation_error_code_is_not_arbitrary_exception_text():
 from app.observability import generation_error_code
 assert generation_error_code('output_contract_incomplete_subquestion_coverage')=='output_contract_incomplete_subquestion_coverage'
 assert generation_error_code('provider_http_429')=='provider_http_429'
 for unsafe in ('fixture-secret-do-not-log','gsk_' + 'abcdefghijklmnopqrstuv','output_contract_gsk_' + 'abcdefghijklmnopqrstuv'):
  assert generation_error_code(unsafe)=='generation_response_contract'

@pytest.mark.parametrize('case,expected_calls,expected_status',[
 ('recovered',2,200),('missing_usage',2,200),('still_incomplete',2,503),('unknown',0,200),('source_changed',1,403),
])
def test_single_coverage_correction_preserves_guards(env,caplog,monkeypatch,case,expected_calls,expected_status):
 import json
 from fastapi.testclient import TestClient
 from app.web import create_app
 from app.grounding import facts_for,coverage_report
 from app.generation import wire_fact_map
 cfg,db,d,a,e=env;cfg=replace(cfg,generation_provider='groq',metrics_enabled=False)
 question='CRM 申請條件及核准後是否等於開通？'+('以及收費多少？' if case=='unknown' else '')
 kb=Knowledge(cfg,db);kb.rebuild();chunks,_=kb.retrieve(question,e,d.scope(e))
 facts=facts_for(chunks);all_ids=[k for k,f in facts.items() if f['document_id']=='crm-access']
 plan=coverage_report(question,chunks,[])
 partial=list({k for r in plan['parts'] if r.get('obligation','').startswith('conditions:') for k in r['supported_candidates']})
 assert partial and not coverage_report(question,chunks,partial)['complete']
 wire_ids={key:wire_id for wire_id,key in wire_fact_map(chunks).items()}
 calls=[];live={'changed':False};original=Knowledge.assert_current
 def current(self,*args,**kw):
  if live['changed']:raise __import__('app.domain',fromlist=['Denied']).Denied('synthetic source changed')
  return original(self,*args,**kw)
 monkeypatch.setattr(Knowledge,'assert_current',current)
 class Response:
  status=200
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,*args):
   selected=all_ids if len(calls)==2 and case in ('recovered','missing_usage') else partial
   ids=[wire_ids[key] for key in selected]
   if case=='source_changed':live['changed']=True
   return json.dumps({'choices':[{'message':{'content':json.dumps({'status':'supported','facts':ids,'suggestions':[]})},'finish_reason':'stop'}],'usage':{} if case=='missing_usage' and len(calls)==2 else {'prompt_tokens':5,'completion_tokens':2}}).encode()
 monkeypatch.setattr('app.generation.read_secret',lambda _: 'synthetic-private-test-key')
 def provider(request,*args,**kw):calls.append(json.loads(request.data));return Response()
 monkeypatch.setattr('app.generation.urllib.request.urlopen',provider)
 client=TestClient(create_app(cfg));login=client.post('/api/auth/login',json={'login':'employee','password':'secure-password-123'});c=d.new_conversation(e,'受控重選')
 caplog.set_level('INFO',logger='roleops')
 response=client.post('/api/conversations/'+c['id']+'/messages',json={'question':question},headers={'x-csrf-token':login.json()['csrf']})
 assert response.status_code==expected_status,response.json()
 assert len(calls)==expected_calls
 assert not db.scan('request:')
 if expected_calls==2:
  assert '唯一一次證據重選' in calls[1]['messages'][0]['content']
  assert calls[0]['messages'][-1]==calls[1]['messages'][-1]
 if case in ('recovered','missing_usage'):
  assert response.json()['grounding']['coverage']['complete']
  assert response.json()['usage']==({'input_tokens':None,'output_tokens':None} if case=='missing_usage' else {'input_tokens':10,'output_tokens':4})
  events=[json.loads(r.message) for r in caplog.records if r.message.startswith('{')]
  attempts=[x for x in events if x.get('component')=='generation']
  assert [x['attempt'] for x in attempts]==[1,2]
  assert all(x['trace_id']==response.json()['trace_id'] for x in attempts)
 elif case=='unknown':
  assert response.json()['grounding']['status']=='insufficient'
  assert response.json()['grounding']['reason_code']=='coverage_unresolved'
  assert not response.json()['proposal']
 else:assert not db.scan('message:'+c['id']+':')
 assert 'synthetic-private-test-key' not in caplog.text


def test_coverage_correction_does_not_extend_provider_deadline(monkeypatch):
 from types import SimpleNamespace
 from app import generation as g
 failure=g.GenerationFailure('output_contract_incomplete_subquestion_coverage');failure.coverage_repair=['conditions:1']
 calls=[]
 def once(*args):calls.append(True);raise failure
 monkeypatch.setattr(g,'_invoke_once',once)
 ticks=iter([0,46]);monkeypatch.setattr(g.time,'monotonic',lambda:next(ticks))
 with pytest.raises(g.GenerationFailure):g.invoke(SimpleNamespace(generation_timeout=45),{}, {'assert_current':lambda:None},['crm'])
 assert len(calls)==1

import asyncio,json,time
from dataclasses import replace
import pytest
from app.domain import Denied,Invalid,now
from app.store import Conflict,Store
from app.knowledge import Knowledge
from app.agent import Agent
from app.ops import backup,restore
def test_session_and_role_intersection(env):
 cfg,db,d,a,e=env;token,_,_=d.login('employee','secure-password-123');assert d.session(token)[0]['id']==e['id'];assert 'finance_reporting' not in d.scope(e)['systems']
 agent=d.save_agent(a,None,dict(name='主管助理',profile='supervisor',avatar='🤖'));d.assign(a,e['id'],agent['id']);assert 'finance_reporting' not in d.scope(d.user(e['id']))['systems']
 with pytest.raises(Denied):d.session(token)
def test_idempotence_and_no_self_approval(env):
 _,db,d,a,e=env;r=d.create_request(e['id'],'crm','工作使用','a'*16,'trace');assert d.create_request(e['id'],'crm','工作使用','a'*16,'trace')['id']==r['id']
 with pytest.raises(Invalid):d.create_request(e['id'],'crm','改理由','a'*16,'trace')
 assert d.approve(a,r['id'],'approved')['status']=='approved'
 with pytest.raises(Invalid):d.approve(a,r['id'],'approved')
 with pytest.raises(Denied):d.create_request(e['id'],'finance_reporting','偷看','b'*16,'trace')
 assert any(x['action']=='AuthorizationDenied' for _,x,_ in db.scan('event:'))
def test_hide_company_retains_and_audits(env):
 _,db,d,a,e=env;c=d.new_conversation(e,'午餐');d.edit_conversation(e,c['id'],'rename',title='工作交接');d.edit_conversation(e,c['id'],'hide')
 with pytest.raises(Denied):d.conversation(e,c['id'])
 assert d.conversation(a,c['id'],audit_read=True)[0]['title']=='工作交接'
 assert any(x['action']=='conversation_read' for _,x,_ in db.scan('event:'))
def test_purge_hold_and_content_erasure(env):
 _,db,d,a,e=env;c=d.new_conversation(e,'今天午餐吃什麼');db.transact([('message:'+c['id']+':1',{'question':'sensitive'},0)]);d.edit_conversation(a,c['id'],'delete',reason='清理');d.edit_conversation(a,c['id'],'hold',reason='調查')
 key='conversation:'+c['id'];c,v=db.get(key);c['purge_after']=now()-1;db.transact([(key,c,v)]);assert d.purge_due()==0;assert d.conversation(a,c['id'])[0]['hold']
 d.edit_conversation(a,c['id'],'release-hold',reason='結案');assert db.get(key)[0]['purge_after']>now();c,v=db.get(key);c['purge_after']=now()-1;db.transact([(key,c,v)])
 with pytest.raises(Denied):d.conversation(a,c['id'])
 with pytest.raises(Invalid):d.edit_conversation(a,c['id'],'restore',reason='晚了')
 assert d.purge_due()==1;assert not db.scan('message:'+c['id']);c=db.get(key)[0];assert c['title']=='今天午餐吃什麼' and c['state']=='purged';assert any(x['action']=='conversation_purged' for _,x,_ in db.scan('event:'))
def test_in_progress_purge_blocks_read_restore(env):
 _,db,d,a,e=env;c=d.new_conversation(e,'敏感');c['state']='purging';db.transact([('conversation:'+c['id'],c,1)])
 with pytest.raises(Denied):d.conversation(a,c['id'])
 with pytest.raises(Invalid):d.edit_conversation(a,c['id'],'restore',reason='不能')
def test_document_authorization_and_versions(env):
 cfg,db,d,a,e=env;kb=Knowledge(cfg,db);kb.rebuild();chunks,_=kb.retrieve('公司營運月報',e,d.scope(e));assert 'operations-report' not in {x['document']['document_id'] for x in chunks};assert all(x['document']['policy_version']!='2024.1' for x in chunks)
 chunks,_=kb.retrieve('前任客服交接清單',e,d.scope(e));assert any(x['document']['document_id']=='support-handover' for x in chunks)
 other=dict(e,department='資訊部');chunks,_=kb.retrieve('交接清單',other,d.scope(e));assert 'support-handover' not in {x['document']['document_id'] for x in chunks}
def test_source_change_and_overlap_fail_closed(env,tmp_path):
 import shutil
 cfg,db,d,a,e=env;folder=tmp_path/'docs';shutil.copytree(cfg.knowledge,folder);kb=Knowledge(replace(cfg,knowledge=folder),db);kb.rebuild();chunks,gen=kb.retrieve('CRM',e,d.scope(e));file=folder/'CRM 存取規則 2026.md';file.write_text(file.read_text(encoding='utf-8')+'異動',encoding='utf-8')
 with pytest.raises(Denied):kb.assert_current(chunks,gen)
 (folder/'重複政策.md').write_text(file.read_text(encoding='utf-8'),encoding='utf-8')
 with pytest.raises(Invalid):kb.rebuild()
 assert kb.status()['status']=='failed'
def test_chat_sequence_role_change_hides_context(env):
 cfg,db,d,a,e=env;kb=Knowledge(cfg,db);kb.rebuild();agent=Agent(cfg,d,kb);c=d.new_conversation(e,'交接');m=asyncio.run(agent.reply(e,c['id'],'CRM 存取如何申請？'));m2=asyncio.run(agent.reply(e,c['id'],'那需要主管核准嗎？'));assert m2['sequence']==2;assert m2['sources'];assert '本機合成' in m['answer']
 d.edit_user(a,e['id'],{'role':'it'});assert d.scope(d.user(e['id']))['stamp']!=m['stamp']
def test_transaction_conflict_and_backups_exclude_content(env):
 cfg,db,d,a,e=env;c=d.new_conversation(e,'秘密名稱');db.transact([('message:'+c['id']+':1',{'question':'secret-body'},0)])
 with pytest.raises(Conflict):db.transact([('user:'+e['id'],e,0)])
 kb=Knowledge(cfg,db);b=backup(cfg,db,kb,'test');assert 'secret-body' not in open(b['file'],encoding='utf-8').read();assert '秘密名稱' not in open(b['file'],encoding='utf-8').read()
 from argparse import Namespace
 result=restore(cfg,db,kb,Namespace(backup='test',verify_only=False,isolated=True,target_table=None));assert result['isolated']
def test_http_login_csrf_and_schema(env):
 from fastapi.testclient import TestClient
 from app.web import create_app
 cfg,db,d,a,e=env;app=create_app(cfg);client=TestClient(app);r=client.post('/api/auth/login',json={'login':'admin','password':'secure-password-123'});assert r.status_code==200;csrf=r.json()['csrf'];assert 'HttpOnly' in r.headers['set-cookie'];assert client.post('/api/conversations',json={'title':'a'}).status_code==403
 headers={'x-csrf-token':csrf};assert client.post('/api/conversations',json={'title':'a'},headers=headers).status_code==200;assert client.post('/api/users',json={},headers=headers).status_code==422;assert client.get('/').status_code==200
 assert client.post('/api/conversations',json={'title':'a'},headers={**headers,'origin':'http://evil.example'}).status_code==403
def test_aws_dynamodb_transactions(env):
 import boto3
 from moto import mock_aws
 cfg,*_=env
 with mock_aws():
  client=boto3.client('dynamodb',region_name='us-east-1');client.create_table(TableName='roleops-main',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
  db=Store(replace(cfg,mode='aws'));db.transact([('x',{'a':1},0),('y',{'b':2},0)]);assert db.get('x')==({'a':1},1)
  with pytest.raises(Conflict):db.transact([('x',{'a':2},0)])
  db.transact([('x',None,1)]);assert db.get('x')==(None,0);assert len(db.scan())==1

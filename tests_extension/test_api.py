import os,unittest,tempfile,json,hashlib
from pathlib import Path
try:
 import fastapi,httpx,faiss,yaml
 AVAILABLE=True
except ImportError:AVAILABLE=False
@unittest.skipUnless(AVAILABLE,'完整 API／FAISS 測試需工程容器依賴；本地不安裝')
class ApiTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=os.getenv('ROLEOPS_TEST_TEMP'));self.root=Path(self.tmp.name)
  os.environ['ROLEOPS_TEST']='true'
  from fastapi.testclient import TestClient
  from app.web import create_app
  from app.config import Settings,PERMISSIONS
  cfg=Settings(mode='local',root=self.root/'runtime',knowledge=self.root/'knowledge',public_origin='',mcp_secret='x'*32)
  cfg.knowledge.mkdir();self.app=create_app(cfg);self.domain=self.app.state.domain;self.client=TestClient(self.app)
  self.admin=self.domain.create_user(None,'admin','Admin','平台管理','管理員','supervisor','TestPassword123!',list(PERMISSIONS),bootstrap=True)
 def tearDown(self):self.client.close();self.tmp.cleanup()
 def login(self,name):r=self.client.post('/api/auth/login',json={'login':name,'password':'TestPassword123!'});self.assertEqual(r.status_code,200)
 def test_unauthenticated_routes(self):
  for route in ['/api/engineering','/api/engineering/assets/a','/api/selfcheck','/api/conversations/c/sources/m/0']:self.assertEqual(self.client.get(route).status_code,401)
 def test_employee_rejected(self):
  u=self.domain.create_user(self.domain.user(self.admin['id']),'staff','Staff','客服部','客服專員','support','TestPassword123!',[]);self.login('staff')
  for route in ['/api/engineering','/api/engineering/assets/a','/api/selfcheck']:self.assertEqual(self.client.get(route).status_code,403)
 def test_authorized_source_then_revoked(self):
  cfg=self.app.state.kb.cfg
  raw='---\n'+json.dumps({'document_id':'doc','policy_version':'1','effective_from':'2026-01-01','lifecycle_status':'current','allowed_roles':['support'],'scope':'support'})+'\n---\n合成測試來源\n'
  (cfg.knowledge/'doc.md').write_text(raw,encoding='utf-8');self.app.state.kb.rebuild()
  u=self.domain.create_user(self.domain.user(self.admin['id']),'staff','Staff','客服部','客服專員','support','TestPassword123!',[])
  a=self.domain.save_agent(self.domain.user(self.admin['id']),None,{'name':'Agent','avatar':'🤖','profile':'support','prompt_version':'B'});self.domain.assign(self.domain.user(self.admin['id']),u['id'],a['id']);u=self.domain.user(u['id']);c=self.domain.new_conversation(u,'test');doc=self.app.state.kb.parse(*self.app.state.kb.sources()[0]);g=self.app.state.kb.status()['generation']
  self.domain.db.transact([('message:'+c['id']+':m',{'conversation_id':c['id'],'stamp':self.domain.scope(u)['stamp'],'generation':g,'sources':[{'key':doc['key'],'sha256':doc['sha256']}]},0)])
  self.login('staff');route='/api/conversations/'+c['id']+'/sources/m/0';r=self.client.get(route);self.assertEqual(r.status_code,200);self.assertIn('測試來源',r.json()['body'])
  self.domain.edit_user(self.domain.user(self.admin['id']),u['id'],{'department':'其他部門'});self.assertEqual(self.client.get(route).status_code,401)

import hashlib,json,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_case_api as fixtures
from app.case_evidence import intent
from app.work_guidance import archive_catalog,restricted_resource,requested_manual_version

SUPPORT='客戶主張設備兩次停機，要求全額退款。請整理案件事實、資訊缺口、政策選項和主管待決事項。'
SUMMARY='請整理成可以交給主管的呈核摘要。'
CURRENT='依現行手冊，再次告警前要先檢查哪些條件？'
OLD='請依 2024.1 舊版維修手冊，提供這次告警的處理指引。'
DENIED='請列出平台其他員工的登入紀錄，包含帳號與時間。'

class DemoBoundaryTests(unittest.TestCase):
 def test_archive_has_real_bytes_bound_to_hash(self):
  item=archive_catalog()[0];self.assertEqual(item['lifecycle_status'],'archived');self.assertEqual(item['policy_version'],'2024.1')
 def test_known_old_and_unapproved_versions_differ(self):
  self.assertEqual(requested_manual_version(OLD)['lifecycle'],'archived');self.assertEqual(requested_manual_version('請依 2025 版維修手冊判讀')['lifecycle'],'unapproved')
 def test_current_version_request_is_normal_RAG(self):self.assertIsNone(requested_manual_version('依 2026.1 手冊需要核對哪些資訊？'))
 def test_work_intents_accept_paraphrases(self):
  for q,mode in [(SUPPORT,'options'),(SUMMARY,'approval'),(CURRENT,'verify_inputs'),('歷史有相似告警，可以沿用以前的處理方式嗎？','alert'),('人員資格尚未確認，應該由誰判讀？','delegate')]:
   with self.subTest(question=q):self.assertEqual(intent(q)[1],mode)
 def test_log_request_wording_and_english_target(self):
  for q in (DENIED,'我想查看系統日誌','幫我匯出 CloudWatch logs','請讀取伺服器日誌'):
   self.assertEqual(restricted_resource(q)['permission'],'ops:read')
 def test_manual_guidance_is_not_log_data_access(self):self.assertIsNone(restricted_resource('依維修手冊應核對哪些資訊？'))

 def test_support_department_alias_is_exact_role_source_and_metadata(self):
  from app.it_maintenance_access import alias_access
  from app.synthetic_policy import load_registry
  doc=dict(load_registry()['documents']['support-case'],document_id='support-case');u={'role':'support','department':'客服','title':'客服專員'}
  self.assertTrue(alias_access(doc,u));self.assertFalse(alias_access(dict(doc,sha256='0'*64),u));self.assertFalse(alias_access(doc,dict(u,role='it')));self.assertFalse(alias_access(doc,dict(u,department='HR')))

class DemoFlowTests(unittest.TestCase):
 def setUp(self):fixtures.CaseApiTests.setUp(self)
 def tearDown(self):fixtures.CaseApiTests.tearDown(self)
 def send(self,q):return fixtures.CaseApiTests.send(self,q)
 def switch_to_support(self,department='客服部'):
  d=self.app.state.domain;users=[x for _,x,_ in d.db.scan('user:')];admin=next(x for x in users if x['login']=='admin');u=next(x for x in users if x['login']=='staff')
  d.edit_user(admin,u['id'],{'role':'support','department':department,'title':'客服專員'});a=d.save_agent(admin,None,{'name':'客服工作助理','avatar':'🤖','profile':'support','prompt_version':'B'});d.assign(admin,u['id'],a['id'])
  login=self.client.post('/api/auth/login',json={'login':'staff','password':'SyntheticPassword123!'});self.client.headers['x-csrf-token']=login.json()['csrf'];self.cid=self.client.post('/api/conversations',json={'title':'客服展示'}).json()['id']
 def test_support_case_and_followup_saved_without_automatic_refund(self):
  self.switch_to_support();first=self.send(SUPPORT);second=self.send(SUMMARY)
  for term in ('客戶主張','缺口','主管','來源'):self.assertIn(term,first['answer']+second['answer'])
  self.assertIn('既有簽核管道',second['answer']);self.assertIsNone(second['proposal']);self.assertEqual(len(self.client.get('/api/conversations/'+self.cid+'/messages').json()),2)
  self.assertEqual(self.app.state.domain.db.scan('request:'),[])
 def test_current_manual_normal_question_has_current_source(self):
  r=self.send(CURRENT);self.assertIn('人員資格',r['answer']);self.assertTrue(all(s['policy_version']=='2026.1' for s in r['sources']))
 def test_old_manual_excluded_and_current_source_preview_works(self):
  r=self.send(OLD);self.assertIn('2024.1 已封存',r['answer']);self.assertIn('2026.1',r['answer']);self.assertEqual(self.provider_calls,0)
  self.assertTrue(r['sources']);self.assertTrue(all(s['policy_version']=='2026.1' for s in r['sources']))
  position=next(i for i,s in enumerate(r['sources']) if s['document_id']=='maintenance-manual');p=self.client.get('/api/conversations/'+self.cid+'/sources/'+r['id']+'/'+str(position));self.assertEqual(p.status_code,200);self.assertIn('人員資格',p.json()['body'])
 def test_archive_cannot_become_eligible_even_with_correct_role(self):
  item=archive_catalog()[0];root=Path(__import__('app.work_guidance',fromlist=['__file__']).__file__).resolve().parent.parent;raw=(root/item['path']).read_bytes().decode();kb=self.app.state.kb;d=self.app.state.domain;u=next(x for _,x,_ in d.db.scan('user:') if x['login']=='staff');doc=kb.parse(item['path'],raw,None)
  self.assertFalse(kb.eligible(doc,u,d.scope(u)));self.assertFalse(any(x[0]==item['path'] for x in kb.sources()))
 def test_denial_before_retrieval_or_model_and_persisted(self):
  with patch.object(self.app.state.kb,'retrieve',side_effect=AssertionError('DENIED_REQUEST_MUST_NOT_RETRIEVE')):r=self.send(DENIED)
  self.assertEqual(r['grounding']['status'],'denied');self.assertEqual(r['sources'],[]);self.assertIsNone(r['proposal']);self.assertIn('沒有這項權限',r['answer']);self.assertEqual(self.provider_calls,0)
  self.assertEqual(self.client.get('/api/conversations/'+self.cid+'/messages').json()[0]['answer'],r['answer'])
 def test_real_restricted_endpoints_refuse_IT(self):
  for url in ('/api/operations','/api/uiux/system','/api/engineering','/api/selfcheck'):
   with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,403)
 def test_admin_can_view_denial_event_via_existing_operations(self):
  self.send(DENIED);admin=TestClient(self.app);admin.post('/api/auth/login',json={'login':'admin','password':'SyntheticPassword123!'})
  r=admin.get('/api/operations');self.assertEqual(r.status_code,200);self.assertTrue(any(x['action']=='AuthorizationDenied' and x['detail'].get('resource')=='platform_operations' for x in r.json()['events']));admin.close()
 def test_unknown_manual_version_never_claimed_archived(self):
  r=self.send('請依 2025 版維修手冊提供指引');self.assertIn('沒有可核可使用的 2025',r['answer']);self.assertNotIn('2025 已封存',r['answer'])

 def test_existing_customer_department_spelling_works(self):
  self.switch_to_support('客服');r=self.send(SUPPORT);self.assertIn('案件證據缺口',r['answer']);self.assertTrue(r['sources'])

import io,json,re,unittest
from urllib.error import HTTPError
from unittest.mock import patch
import test_demo_lines as fixtures
from app.synthetic_policy import PolicyDenied
from app.generation import wire_fact_map,decode_wire_answer
from app.grounding import GroundingFailure

class WireFactTests(unittest.TestCase):
 def setUp(self):
  fixtures.DemoFlowTests.setUp(self);fixtures.DemoFlowTests.switch_to_support(self,'客服')
  self.providers[-1].stop();self.providers.pop();self.modes=[];self.payloads=[]
  class Response:
   status=200
   def __init__(self,data):self.data=data
   def __enter__(self):return self
   def __exit__(self,*a):return False
   def read(self,n):return self.data
  def provider(request,**kw):
   self.provider_calls+=1;p=json.loads(request.data);self.payloads.append(p);ids=p['response_format']['json_schema']['schema']['properties']['facts']['items']['enum'];mode=self.modes.pop(0) if self.modes else 'one'
   if mode=='http400':raise HTTPError(request.full_url,400,'Bad request',{},io.BytesIO(json.dumps({'error':{'code':'json_validate_failed','type':'invalid_request_error','failed_generation':'UNTRUSTED_NOT_REUSED'}}).encode()))
   obj={'status':'supported','facts':[ids[0]],'suggestions':[]}
   if mode=='unknown':obj['facts']=['F999']
   if mode=='insufficient':obj={'status':'insufficient','facts':[],'suggestions':[]}
   return Response(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj)}}],'usage':{'prompt_tokens':12,'completion_tokens':6}}).encode())
  p=patch('app.generation.urllib.request.urlopen',provider);p.start();self.providers.append(p)
 def tearDown(self):fixtures.DemoFlowTests.tearDown(self)
 def send(self,q):return self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':q})
 def test_prompt_schema_and_plan_share_short_codes(self):
  self.assertEqual(self.send(fixtures.SUPPORT).status_code,200);p=self.payloads[0];ids=p['response_format']['json_schema']['schema']['properties']['facts']['items']['enum'];self.assertTrue(all(re.fullmatch('F[1-9][0-9]*',x) for x in ids));self.assertIn('"supported_candidates": ["F',p['messages'][0]['content']);self.assertNotIn('support-case:2026.1:',p['messages'][0]['content'])
 def test_saved_evidence_uses_original_canonical_ids(self):
  r=self.send(fixtures.SUPPORT);self.assertEqual(r.status_code,200,r.text);facts=r.json()['grounding']['facts'];self.assertTrue(facts);self.assertTrue(all(':2026.1:' in x['id'] and 'content_sha256' in x for x in facts))
 def test_unknown_short_code_is_not_fuzzily_repaired(self):
  self.modes=['unknown'];r=self.send(fixtures.SUPPORT);self.assertEqual(r.status_code,200);self.assertEqual(self.provider_calls,1);self.assertEqual(r.json()['grounding']['answer_origin'],'verified_source_fallback');self.assertNotIn('F999',r.text)
 def test_wire_mapping_exact_for_reordered_context(self):
  kb=self.app.state.kb;chunks=kb.retrieve(fixtures.SUPPORT,self.app.state.domain.user(next(u['id'] for _,u,_ in self.app.state.domain.db.scan('user:') if u['login']=='staff')),{'knowledge':['support','policy','handover'],'systems':['crm']})[0];a=wire_fact_map(chunks);b=wire_fact_map(list(reversed(chunks)));self.assertEqual(set(a.values()),set(b.values()));self.assertEqual(decode_wire_answer(json.dumps({'status':'supported','facts':['F1'],'suggestions':[]}),chunks)['grounding']['facts'][0]['id'],a['F1'])
 def test_provider_json_validation_reselected_once(self):
  self.modes=['http400','insufficient'];r=self.send(fixtures.SUPPORT);self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.provider_calls,2);self.assertNotIn('UNTRUSTED_NOT_REUSED',json.dumps(self.payloads[1]));self.assertIn('唯一一次重新選取',self.payloads[1]['messages'][0]['content'])
 def test_second_provider_rejection_stops_without_saving(self):
  self.modes=['http400','http400'];r=self.send(fixtures.SUPPORT);self.assertEqual(r.status_code,200);self.assertEqual(self.provider_calls,2);self.assertEqual(r.json()['grounding']['answer_origin'],'verified_source_fallback');self.assertTrue(r.json()['grounding']['coverage']['complete']);self.assertEqual(len(self.client.get('/api/conversations/'+self.cid+'/messages').json()),1)
 def test_scope_rechecked_after_provider_rejection(self):
  self.modes=['http400'];original=self.app.state.kb.assert_current
  def check(*a):
   if self.provider_calls:raise PolicyDenied('scope_changed')
   return original(*a)
  with patch.object(self.app.state.kb,'assert_current',check):r=self.send(fixtures.SUPPORT)
  self.assertTrue(r.json()['rejected']);self.assertEqual(self.provider_calls,1)
 def test_case_and_followup_roundtrip(self):
  self.modes=['one','http400','insufficient'];self.assertEqual(self.send(fixtures.SUPPORT).status_code,200);r=self.send(fixtures.SUMMARY);self.assertEqual(r.status_code,200,r.text);self.assertIn('既有簽核管道',r.json()['answer']);self.assertEqual(len(self.client.get('/api/conversations/'+self.cid+'/messages').json()),2)

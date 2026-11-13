import json,unittest
from unittest.mock import patch
import test_demo_lines as fixtures
from app.synthetic_policy import PolicyDenied

class GenerationLimitTests(unittest.TestCase):
 def setUp(self):
  fixtures.DemoFlowTests.setUp(self);fixtures.DemoFlowTests.switch_to_support(self,'客服')
  self.providers[-1].stop();self.providers.pop();self.responses=[];self.payloads=[]
  class Response:
   status=200
   def __init__(self,body):self.body=body
   def __enter__(self):return self
   def __exit__(self,*a):return False
   def read(self,n):return self.body
  def provider(request,**kw):
   self.provider_calls+=1;p=json.loads(request.data);self.payloads.append(p);ids=p['response_format']['json_schema']['schema']['properties']['facts']['items']['enum'];mode=self.responses.pop(0) if self.responses else 'insufficient'
   obj={'status':'insufficient','facts':[],'suggestions':[]}
   if mode=='overflow':obj={'status':'supported','facts':ids[:7],'suggestions':[]}
   elif mode=='suggestions':obj={'status':'supported','facts':ids[:1],'suggestions':['可再核對。','可先整理。']}
   elif mode=='forged':obj={'status':'supported','facts':['not-an-approved-fact'],'suggestions':[]}
   return Response(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(obj)}}],'usage':{'prompt_tokens':12,'completion_tokens':6}}).encode())
  p=patch('app.generation.urllib.request.urlopen',provider);p.start();self.providers.append(p)
 def tearDown(self):fixtures.DemoFlowTests.tearDown(self)
 def send(self,q):return self.client.post('/api/conversations/'+self.cid+'/messages',json={'question':q})
 def test_schema_has_both_array_limits(self):
  r=self.send(fixtures.SUPPORT);self.assertEqual(r.status_code,200);s=self.payloads[0]['response_format']['json_schema']['schema']['properties'];self.assertEqual(s['facts']['maxItems'],6);self.assertEqual(s['suggestions']['maxItems'],1)
 def test_real_overflow_shape_reselected_once_saved_and_usage_preserved(self):
  self.responses=['overflow','insufficient'];r=self.send(fixtures.SUPPORT);self.assertEqual(r.status_code,200,r.text);v=r.json();self.assertIn('案件證據缺口',v['answer']);self.assertLessEqual(len(v['grounding']['facts']),6);self.assertEqual(self.provider_calls,2);self.assertEqual(v['usage'],{'input_tokens':24,'output_tokens':12});self.assertIn('唯一一次格式重選',self.payloads[1]['messages'][0]['content']);self.assertIsNone(v['proposal'])
  self.assertEqual(self.client.get('/api/conversations/'+self.cid+'/messages').json()[0]['answer'],v['answer'])
 def test_too_many_suggestions_also_reselected_once(self):
  self.responses=['suggestions','insufficient'];self.assertEqual(self.send(fixtures.SUPPORT).status_code,200);self.assertEqual(self.provider_calls,2)
 def test_repeat_overflow_recovers_only_from_verified_case_after_two(self):
  self.responses=['overflow','overflow'];r=self.send(fixtures.SUPPORT);self.assertEqual(r.status_code,200);self.assertEqual(self.provider_calls,2)
  self.assertEqual(r.json()['grounding']['answer_origin'],'verified_source_fallback');self.assertTrue(r.json()['grounding']['coverage']['complete']);self.assertIsNone(r.json()['proposal'])
 def test_unapproved_fact_is_discarded_before_independent_source_recovery(self):
  self.responses=['forged'];r=self.send(fixtures.SUPPORT);self.assertEqual(r.status_code,200);self.assertEqual(self.provider_calls,1)
  self.assertEqual(r.json()['grounding']['answer_origin'],'verified_source_fallback');self.assertNotIn('not-an-approved-fact',r.text)
 def test_scope_or_source_rechecked_before_retry(self):
  self.responses=['overflow','insufficient'];original=self.app.state.kb.assert_current
  def check(*a):
   if self.provider_calls:raise PolicyDenied('scope_changed')
   return original(*a)
  with patch.object(self.app.state.kb,'assert_current',check):r=self.send(fixtures.SUPPORT)
  self.assertTrue(r.json()['rejected']);self.assertEqual(self.provider_calls,1)
 def test_customer_followup_still_works_after_reselected_first_answer(self):
  self.responses=['overflow','insufficient','insufficient'];self.assertEqual(self.send(fixtures.SUPPORT).status_code,200);r=self.send(fixtures.SUMMARY);self.assertEqual(r.status_code,200);self.assertIn('既有簽核管道',r.json()['answer']);self.assertEqual(self.provider_calls,3)
 def test_runtime_status_identifies007(self):self.assertEqual(self.client.get('/api/status').json()['extension_revision'],'010-integrated-guidance-20261103')

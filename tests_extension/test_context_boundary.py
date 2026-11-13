import unittest
from unittest.mock import patch
import test_demo_lines as fixtures
from app.work_guidance import needs_case_context

class ContextBoundaryTests(unittest.TestCase):
    setUp=fixtures.DemoFlowTests.setUp
    tearDown=fixtures.DemoFlowTests.tearDown
    send=fixtures.DemoFlowTests.send
    switch_to_support=fixtures.DemoFlowTests.switch_to_support

    def test_bare_summary_is_saved_without_retrieval_or_provider(self):
        self.switch_to_support()
        with patch.object(self.app.state.kb,'retrieve',side_effect=AssertionError('NO_CONTEXT_NO_RETRIEVAL')):
            r=self.send(fixtures.SUMMARY)
        self.assertEqual(r['grounding']['reason_code'],'missing_case_context')
        self.assertIn('案件內容',r['answer']);self.assertEqual(r['sources'],[]);self.assertIsNone(r['proposal'])
        self.assertEqual(self.provider_calls,0)
        rows=self.client.get('/api/conversations/'+self.cid+'/messages').json()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['answer'],r['answer'])
        self.assertEqual(self.app.state.domain.db.scan('request:'),[])

    def test_full_case_can_follow_clarification(self):
        self.switch_to_support();self.send(fixtures.SUMMARY);r=self.send(fixtures.SUPPORT)
        self.assertEqual(r['grounding']['status'],'supported');self.assertTrue(r['sources'])

    def test_same_case_summary_retains_original_provider_path(self):
        self.switch_to_support();self.send(fixtures.SUPPORT);r=self.send(fixtures.SUMMARY)
        self.assertEqual(r['grounding']['status'],'supported');self.assertIn('既有簽核管道',r['answer'])
        self.assertEqual(self.provider_calls,2)

    def test_new_conversation_does_not_borrow_other_case(self):
        self.switch_to_support();self.send(fixtures.SUPPORT)
        self.cid=self.client.post('/api/conversations',json={'title':'無案情'}).json()['id']
        r=self.send(fixtures.SUMMARY);self.assertEqual(r['grounding']['status'],'clarification')
        self.assertEqual(self.provider_calls,1)

    def test_paraphrases_and_explicit_case_boundary(self):
        for q in ('請整理成可以交給主管的呈核摘要。','請整理可供主管看的呈核材料。','請整理客服呈核摘要。'):
            self.assertTrue(needs_case_context(q),q)
        self.assertFalse(needs_case_context('設備兩次停機的客訴，請整理可呈核方案及需要補充的證據。'))
        self.assertFalse(needs_case_context('請幫我建立 CRM 待核准申請，理由是要調閱客戶資料以進行聯繫。'))

    def test_unsupported_history_does_not_supply_case(self):
        h=[{'question':fixtures.SUPPORT,'answer':'資料不足','stamp':'s','generation':'g','grounding':{'status':'insufficient'}}]
        self.assertTrue(needs_case_context(fixtures.SUMMARY,h))

    def test_denial_and_old_manual_boundaries_still_precede_model(self):
        with patch.object(self.app.state.kb,'retrieve',side_effect=AssertionError('NO_DENIED_RETRIEVAL')):
            r=self.send(fixtures.DENIED)
        self.assertEqual(r['grounding']['status'],'denied');self.assertEqual(self.provider_calls,0)
        r=self.send(fixtures.OLD);self.assertIn('2024.1 已封存',r['answer']);self.assertEqual(self.provider_calls,0)

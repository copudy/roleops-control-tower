import hashlib,json,re,unittest
from pathlib import Path
from app.synthetic_policy import validate_context,PolicyDenied,load_registry

class ApprovalRepairTests(unittest.TestCase):
    def context(self, filename, role='maintenance'):
        raw=(Path(__file__).parent/'fixtures'/filename).read_bytes()
        match=re.fullmatch(r'---\s*\n([\s\S]*?)\n---\s*\n([\s\S]+)',raw.decode('utf-8'))
        doc=json.loads(match[1]);doc['sha256']=hashlib.sha256(raw).hexdigest()
        user={'role':role,'department':'維修部' if role=='maintenance' else '客服部','title':'維修工程師' if role=='maintenance' else '客服專員'}
        prompt=(Path('/opt/roleops/prompts/B.txt') if Path('/opt/roleops/prompts/B.txt').exists() else Path(__file__).parents[1]/'prompts/B.txt').read_text(encoding='utf-8')
        return prompt,[{'document':doc,'text':match[2].strip()}],user

    def check(self,filename,role='maintenance',mutate=None):
        prompt,chunks,user=self.context(filename,role)
        if mutate:mutate(chunks[0])
        return validate_context(prompt,chunks,[],'請整理此案例的依據與缺口',role,[],user=user)

    def test_exact_shipped_maintenance_CRLF_is_approved(self):
        for f in ('維修設備手冊.md','維修歷史紀錄.md'):
            with self.subTest(file=f):self.check(f)

    def test_exact_shipped_support_CRLF_is_approved(self):
        for f in ('客服案例_設備兩次停機.md','客服合約適用條件.md','客服呈核政策.md'):
            with self.subTest(file=f):self.check(f,'support')

    def test_changed_source_is_still_rejected(self):
        with self.assertRaises(PolicyDenied) as caught:
            self.check('維修設備手冊.md',mutate=lambda c:c['document'].update(sha256='0'*64))
        self.assertEqual(caught.exception.code,'unapproved_source_version')

    def test_wrong_role_is_still_rejected(self):
        with self.assertRaises(PolicyDenied) as caught:self.check('維修設備手冊.md','support')
        self.assertEqual(caught.exception.code,'unauthorized_source')

    def test_unapproved_body_is_still_rejected(self):
        with self.assertRaises(PolicyDenied) as caught:
            self.check('維修設備手冊.md',mutate=lambda c:c.update(text='合成竄改正文'))
        self.assertEqual(caught.exception.code,'unauthorized_source')

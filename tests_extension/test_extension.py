import unittest,tempfile,json,hashlib,time,sys,os
from pathlib import Path
from types import SimpleNamespace
from app import showcase_content as sc
from app.extension import source_preview,snapshot,content
from app.domain import Domain,Denied,Invalid
from app.config import Settings
from app.store import Store
class ExtensionTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir=os.getenv('ROLEOPS_TEST_TEMP'));self.root=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def data(self):
  f=self.root/'releases/v1';f.mkdir(parents=True);(f/'proof.txt').write_text('evidence')
  report={k:'test' for k in ('title','snapshot_date','status','problem','impact','reasoning','change','verification','prevention','technical','contribution')};report.update(id='r1',assets=['a1'])
  journal={k:'test' for k in ('title','snapshot_date','status','purpose','action','result','next','technical')};journal.update(id='j1',incident_id='r1',assets=['a1'])
  d={'schema':1,'version':'v1','reports':[report],'journals':[journal],'assets':{'a1':{'label':'proof','path':'proof.txt','sha256':hashlib.sha256((f/'proof.txt').read_bytes()).hexdigest()}}}
  (f/'content.json').write_text(json.dumps(d));return f,d
 def test_publish_and_load(self):f,d=self.data();sc.publish(self.root,'v1');self.assertEqual(sc.load(self.root)[0],d)
 def test_tampered_asset(self):f,d=self.data();(f/'proof.txt').write_text('changed');self.assertRaises(ValueError,sc.publish,self.root,'v1');self.assertFalse((self.root/'current.json').exists())
 def test_pointer_mismatch(self):f,d=self.data();sc.publish(self.root,'v1');d['reports'][0]['title']='changed';(f/'content.json').write_text(json.dumps(d));self.assertRaises(ValueError,sc.load,self.root)
 def test_traversal(self):self.assertRaises(ValueError,sc.asset_path,self.root,'../../private.txt');self.assertRaises(ValueError,sc.safe_version,'../v1')
 def test_html_asset(self):self.assertRaises(ValueError,sc.asset_path,self.root,'a.html')
 def test_invalid_reference(self):f,d=self.data();d['journals'][0]['incident_id']='missing';(f/'content.json').write_text(json.dumps(d));self.assertRaises(ValueError,sc.validate,f)
 def test_snapshot_stale_and_missing(self):cfg=SimpleNamespace(root=self.root);self.assertFalse(snapshot(cfg)['fresh']);(self.root/'selfcheck.json').write_text(json.dumps({'checked_at':100,'checks':[]}));self.assertFalse(snapshot(cfg,lambda:1001)['fresh']);self.assertTrue(snapshot(cfg,lambda:200)['fresh'])
 def test_content_denied_before_file_read(self):domain=SimpleNamespace(permission=lambda u,p:(_ for _ in ()).throw(Denied('no')));self.assertRaises(Denied,content,{},domain)
 def context(self):
  cfg=Settings(mode='local',root=self.root,mcp_secret='x'*32,public_origin='');d=Domain(Store(cfg));admin=d.create_user(None,'admin','Admin','平台管理','管理員','supervisor','TestPassword123!',[],bootstrap=True)
  # Direct isolated records avoid creating any AWS company data.
  u=dict(d.user(admin['id']),role='maintenance',department='維修部',title='維修工程師',agent_id='a');_,v=d.db.get('user:'+u['id']);d.db.transact([('user:'+u['id'],u,v),('agent:a',{'id':'a','profile':'maintenance','revision':1,'prompt_version':'B'},0)])
  c=d.new_conversation(u,'test');stamp=d.scope(u)['stamp'];raw='source';sha=hashlib.sha256(raw.encode()).hexdigest();doc={'document_id':'doc','policy_version':'1','body':'body','sha256':sha,'key':'key'}
  d.db.transact([('message:'+c['id']+':m',{'conversation_id':c['id'],'stamp':stamp,'generation':'g','sources':[{'key':'key','sha256':sha}]},0)])
  kb=SimpleNamespace(status=lambda:{'generation':'g'},sources=lambda:[('key',raw,None)],parse=lambda *x:doc,eligible=lambda *x:True,assert_current=lambda *x:None)
  return d,kb,u,c
 def test_source_authorized(self):d,kb,u,c=self.context();self.assertEqual(source_preview(d,kb,u,c['id'],'m',0)['body'],'body')
 def test_source_revoked(self):d,kb,u,c=self.context();u['auth_revision']+=1;_,v=d.db.get('user:'+u['id']);d.db.transact([('user:'+u['id'],u,v)]);self.assertRaises(Denied,source_preview,d,kb,u,c['id'],'m',0)
 def test_source_updated_or_outside_acl(self):d,kb,u,c=self.context();kb.eligible=lambda *x:False;self.assertRaises(Denied,source_preview,d,kb,u,c['id'],'m',0)
 def test_unified_legacy_maintenance_can_request_CRM_not_finance(self):
  d,kb,u,c=self.context();self.assertEqual(d.scope(u)['systems'],['crm']);self.assertEqual(d.create_request(u['id'],'crm','test','abcdefgh','t')['status'],'pending');self.assertRaises(Denied,d.create_request,u['id'],'finance_reporting','test','abcdefgh2','t')
if __name__=='__main__':unittest.main()

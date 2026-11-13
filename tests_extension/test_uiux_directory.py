import unittest,tempfile
from pathlib import Path
from app.config import Settings
from app.store import Store
from app.domain import Domain,Denied,Invalid
from app.uiux_views import UIUX

class DirectoryTests(unittest.TestCase):
 def setUp(self):
  fixture_secret = 'synthetic-test-' + 'secret-123456789'
  self.tmp=tempfile.TemporaryDirectory();self.cfg=Settings(mode='local',root=Path(self.tmp.name),mcp_secret=fixture_secret);self.db=Store(self.cfg);self.domain=Domain(self.db);self.ui=UIUX(self.cfg,self.db,self.domain)
  self.admin={'id':'admin','permissions':['people:manage','agent:manage']};self.staff={'id':'staff','permissions':[]}
  for i in range(37):self.db.transact([('user:u'+str(i),{'id':'u'+str(i),'login':'staff%03d'%i,'name':'人員'+str(i),'department':'IT' if i%2 else '客服','title':'專員','role':'support','permissions':[],'active':i%3!=0,'credential':'PRIVATE'},0)])
 def tearDown(self):self.tmp.cleanup()
 def test_page_is_bounded_and_sorted(self):
  a=self.ui.directory(self.admin,'users');b=self.ui.directory(self.admin,'users',page=2)
  self.assertEqual(a['total'],37);self.assertEqual(len(a['items']),12);self.assertEqual(a['items'][0]['login'],'staff000');self.assertEqual(b['items'][0]['login'],'staff012');self.assertEqual(a['pages'],4)
 def test_filter_search_and_clamp(self):
  a=self.ui.directory(self.admin,'users',q='staff01',department='IT',active='true',page=999)
  self.assertEqual(a['page'],a['pages']);self.assertTrue(all(x['department']=='IT' and x['active'] and 'staff01' in x['login'] for x in a['items']));self.assertEqual(set(a['departments']),{'IT','客服'})
 def test_permissions_and_limits(self):
  self.assertRaises(Denied,self.ui.directory,self.staff,'users');self.assertRaises(Denied,self.ui.directory,{'id':'x','permissions':['people:manage']},'agents');self.assertRaises(Invalid,self.ui.directory,self.admin,'users',page_size=100)
 def test_public_projection_and_assigned_skin(self):
  u,v=self.db.get('user:u1');u['agent_id']='a';self.db.transact([('user:u1',u,v),('agent:a',{'id':'a','name':'同系列','companion_skin':'fox','profile':'support','private_extra':'PRIVATE'},0)])
  a=self.ui.directory(self.admin,'users',q='staff001')['items'][0];self.assertNotIn('credential',a);self.assertEqual(a['assigned_agent'],{'id':'a','name':'同系列','companion_skin':'fox'})
 def test_agent_pages_and_stable_sort(self):
  for i in range(17):self.db.transact([('agent:a'+str(i),{'id':'a'+str(i),'name':'agent%02d'%i,'profile':'support'},0)])
  a=self.ui.directory(self.admin,'agents',page_size=6);self.assertEqual(a['total'],17);self.assertEqual(len(a['items']),6);self.assertEqual(a['items'][0]['name'],'agent00')

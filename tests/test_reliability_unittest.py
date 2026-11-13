"""Offline fault injection; FAISS test double is used only when the real library is unavailable."""
import hashlib,importlib.util,io,json,sys,tempfile,threading,types,unittest,shutil,os
from pathlib import Path
from dataclasses import replace
from datetime import timezone,timedelta
from unittest.mock import patch,Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
FAISS_DOUBLE=importlib.util.find_spec('faiss') is None
if FAISS_DOUBLE:
 fake=types.ModuleType('faiss')
 class IndexFlatIP:
  def __init__(self,d):self.d=d;self.vectors=np.empty((0,d),dtype=np.float32)
  @property
  def ntotal(self):return len(self.vectors)
  def add(self,v):self.vectors=np.vstack([self.vectors,v])
  def search(self,q,k):
   scores=q@self.vectors.T;order=np.argsort(-scores,axis=1)[:,:k];return np.take_along_axis(scores,order,axis=1),order
 def write_index(ix,path):Path(path).write_text(json.dumps({'d':ix.d,'v':ix.vectors.tolist()}))
 def read_index(path):
  obj=json.loads(Path(path).read_text());ix=IndexFlatIP(obj['d']);ix.add(np.array(obj['v'],dtype=np.float32));return ix
 fake.IndexFlatIP=IndexFlatIP;fake.write_index=write_index;fake.read_index=read_index;sys.modules['faiss']=fake
from app.config import Settings
from app.store import Store
from app.domain import Invalid,KnowledgeUnavailable
from app.knowledge import Knowledge,IndexBuildBusy
from app.embedding import EmbeddingClient,EmbeddingFailure,atomic_json
from indexer.worker import RetryGate,Worker,process_message,VisibilityHeartbeat
class Clock:
 def __init__(self):self.t=1000.;self.waits=[]
 def now(self):return self.t
 def sleep(self,s):self.waits.append(s);self.t+=s
class Error(Exception):
 def __init__(self,code='ThrottlingException',status=429):self.response={'Error':{'Code':code,'Message':'NEVER_LOG_SECRET'},'ResponseMetadata':{'HTTPStatusCode':status}}
class Provider:
 def __init__(self,events):self.events=list(events);self.calls=[]
 def invoke_model(self,**kw):
  self.calls.append(kw);v=self.events.pop(0)
  if isinstance(v,Exception):raise v
  return {'body':io.BytesIO(json.dumps({'embedding':v}).encode())}
class NoHeartbeat:
 def __init__(self,*a):pass
 def __enter__(self):return self
 def __exit__(self,*a):pass
class Queue:
 def __init__(self):self.deleted=[];self.visibility=[]
 def delete_message(self,**kw):self.deleted.append(kw)
 def change_message_visibility(self,**kw):self.visibility.append(kw)
class Reliability(unittest.TestCase):
 def setUp(self):
  scratch=Path(os.getenv('ROLEOPS_TEST_TMP',str(Path.cwd()/'test-runtime')));scratch.mkdir(parents=True,exist_ok=True)
  self.temp=tempfile.TemporaryDirectory(dir=scratch);self.root=Path(self.temp.name);docs=self.root/'docs';shutil.copytree(Path(__file__).resolve().parents[1]/'knowledge',docs)
  self.cfg=Settings(mode='local',root=self.root/'data',knowledge=docs,mcp_secret='x'*64,index_lock_timeout=.05);self.cfg.validate();self.db=Store(self.cfg);self.kb=Knowledge(self.cfg,self.db);self.clock=Clock();self.logs=[]
  self.patches=[patch(name,lambda *a,**kw:self.logs.append((a,kw))) for name in ('app.embedding.emit','app.knowledge.emit','indexer.worker.emit')]
  # Taiwan UTC+8 in isolated fixtures; Windows test runtime may have no tzdata package.
  self.patches.append(patch('app.knowledge.ZoneInfo',lambda name:timezone(timedelta(hours=8))))
  for p in self.patches:p.start()
 def tearDown(self):
  for p in self.patches:p.stop()
  self.temp.cleanup()
 def client(self,events,**settings):
  cfg=replace(self.cfg,mode='aws',**settings);p=Provider(events);c=EmbeddingClient(cfg,self.root/'rate',p,self.clock.sleep,self.clock.now,self.clock.now,lambda a,b:b);return c,p
 def aws(self,events,**settings):
  c,p=self.client(events,**settings);kb=Knowledge(c.cfg,self.db);kb.embed_client=c;return kb,p
 def msg(self,seq='1',key='knowledge/a.md',body=None):
  value={'Records':[{'eventSource':'aws:s3','s3':{'bucket':{'name':'bucket'},'object':{'key':key,'sequencer':seq}}}]}
  return {'Body':json.dumps(value) if body is None else body,'ReceiptHandle':'receipt','Attributes':{'ApproximateReceiveCount':'1'}}
 def test_embedding_success(self):
  c,p=self.client([[.1]*1024]);self.assertEqual(len(c.invoke('doc')),1024);self.assertEqual(len(p.calls),1)
 def test_throttle_then_success(self):
  c,p=self.client([Error(),[.1]*1024]);c.invoke('doc');self.assertEqual(len(p.calls),2);self.assertEqual(self.logs[-1][1]['retry_count'],1)
 def test_throttle_bound(self):
  c,p=self.client([Error()]*10)
  with self.assertRaises(EmbeddingFailure) as e:c.invoke('doc')
  self.assertEqual(e.exception.attempts,4);self.assertEqual(len(p.calls),4)
 def test_permanent_errors(self):
  for code in ('AccessDeniedException','ValidationException','ResourceNotFoundException','ServiceQuotaExceededException'):
   c,p=self.client([Error(code,400)])
   with self.assertRaises(EmbeddingFailure) as e:c.invoke('doc')
   self.assertFalse(e.exception.retryable);self.assertEqual(len(p.calls),1)
 def test_5xx_retry(self):
  c,p=self.client([Error('Server',503),[.1]*1024]);c.invoke('doc');self.assertEqual(len(p.calls),2)
 def test_unknown_4xx_no_retry(self):
  c,p=self.client([Error('Other',400)])
  with self.assertRaises(EmbeddingFailure):c.invoke('doc')
  self.assertEqual(len(p.calls),1)
 def test_retry_budget(self):
  c,p=self.client([Error()]*10,embed_retry_budget=1)
  with self.assertRaises(EmbeddingFailure):c.invoke('doc')
  self.assertEqual(len(p.calls),1)
 def test_shared_pacing(self):
  c,p=self.client([[.1]*1024,[.2]*1024]);c.invoke('one');other=EmbeddingClient(c.cfg,c.root,p,self.clock.sleep,self.clock.now,self.clock.now);other.invoke('two');self.assertGreaterEqual(sum(self.clock.waits),2)
 def test_sdk_one_attempt(self):
  c,p=self.client([[.1]*1024]);c.client=None;boto=types.ModuleType('boto3');boto.client=Mock(return_value=p);module=types.ModuleType('botocore.config');module.Config=lambda **kw:kw
  with patch.dict(sys.modules,{'boto3':boto,'botocore':types.ModuleType('botocore'),'botocore.config':module}):c.invoke('doc')
  self.assertEqual(boto.client.call_args.kwargs['config']['retries']['total_max_attempts'],1)
 def test_cache_across_objects(self):
  kb,p=self.aws([[.1]*1024]);v=kb.embedding('doc');other=Knowledge(kb.cfg,self.db);other.embed_client=kb.embed_client;self.assertEqual(other.embedding('doc'),v);self.assertEqual(len(p.calls),1)
 def test_no_cache_on_failed_request(self):
  kb,p=self.aws([Error()]*4)
  with self.assertRaises(EmbeddingFailure):kb.embedding('doc')
  self.assertFalse(list((kb.indexroot/'embedding-cache').glob('*.json')))
 def test_invalid_vector_not_cached(self):
  for vector in ([1]*10,[float('nan')]*1024):
   kb,p=self.aws([vector])
   with self.assertRaises(Invalid):kb.embedding('bad')
   self.assertFalse(list((kb.indexroot/'embedding-cache').glob('*.json')))
 def test_legacy_cache_reused(self):
  kb,p=self.aws([]);cache=kb.indexroot/'embedding-cache';cache.mkdir();key=hashlib.sha256((kb.cfg.mode+':'+kb.cfg.embed_model+':doc').encode()).hexdigest();atomic_json(cache/(key+'.json'),[.1]*1024);kb.embedding('doc');self.assertFalse(p.calls)
 def test_dimension_change_not_reused(self):
  kb,p=self.aws([[.1]*1024]);kb.embedding('doc');other=Knowledge(replace(kb.cfg,embed_dimensions=256),self.db);q=Provider([[.1]*256]);other.embed_client.client=q;self.assertEqual(len(other.embedding('doc')),256);self.assertEqual(len(q.calls),1)
 def test_cache_concurrency_one_call(self):
  kb,p=self.aws([[.1]*1024]);cfg=replace(kb.cfg,index_lock_timeout=2);a=Knowledge(cfg,self.db);b=Knowledge(cfg,self.db);a.embed_client.client=p;b.embed_client.client=p;results=[];errors=[]
  def run(k):
   try:results.append(k.embedding('same'))
   except Exception as e:errors.append(e)
  ts=[threading.Thread(target=run,args=(k,)) for k in (a,b)]
  for t in ts:t.start()
  for t in ts:t.join()
  self.assertFalse(errors);self.assertEqual(len(results),2);self.assertEqual(len(p.calls),1)
 def test_reconcile_no_churn(self):
  a=self.kb.rebuild();b=self.kb.reconcile();self.assertEqual(a['generation'],b['generation']);self.assertTrue(b['reused'])
 def test_generation_consistency(self):
  state=self.kb.rebuild();self.assertTrue(self.kb.inspect_index()['integrity_ready']);manifest=json.loads((self.kb.indexroot/state['generation']/'manifest.json').read_text());self.assertEqual(manifest['digest'],state['digest'])
 def test_model_change_requires_new_generation(self):
  first=self.kb.rebuild();other=Knowledge(replace(self.cfg,embed_model='different-model'),self.db)
  self.assertFalse(other.inspect_index()['integrity_ready']);second=other.reconcile();self.assertNotEqual(first['generation'],second['generation'])
 def test_permanent_build_error_suspends_immediately(self):
  gate=RetryGate(self.cfg,self.clock.now);gate.failure(EmbeddingFailure('AccessDeniedException',False,1,1));self.assertTrue(gate.suspended)
 def test_version_only_change_detected(self):
  values=self.kb.sources();self.kb.sources=lambda:[(k,t,'v1') for k,t,v in values];first=self.kb.rebuild();self.kb.sources=lambda:[(k,t,'v2') for k,t,v in values];self.assertNotEqual(first['generation'],self.kb.reconcile()['generation'])
 def test_partial_build_recovery(self):
  original=self.kb.embedding;n=[0]
  def fail(text):
   n[0]+=1
   if n[0]==2:raise EmbeddingFailure('ThrottlingException',True,4,1)
   return original(text)
  self.kb.embedding=fail
  with self.assertRaises(EmbeddingFailure):self.kb.rebuild()
  self.assertEqual(self.kb.status()['status'],'failed');self.assertEqual(len(list((self.kb.indexroot/'embedding-cache').glob('*.json'))),1);self.kb.embedding=original;self.kb.reconcile();self.assertTrue(self.kb.inspect_index()['integrity_ready'])
 def test_unchanged_failed_refresh_keeps_good_index(self):
  first=self.kb.rebuild()
  with patch.object(self.kb,'embedding',side_effect=EmbeddingFailure('ThrottlingException',True,4,1)):
   with self.assertRaises(EmbeddingFailure):self.kb.rebuild()
  self.assertEqual(first['generation'],self.kb.status()['generation']);self.assertTrue(self.kb.inspect_index()['integrity_ready'])
 def test_changed_failure_closed(self):
  first=self.kb.rebuild();p=self.cfg.knowledge/'CRM 存取規則 2026.md';p.write_text(p.read_text(encoding='utf-8')+' changed',encoding='utf-8')
  with patch.object(self.kb,'embedding',side_effect=EmbeddingFailure('ThrottlingException',True,4,1)):
   with self.assertRaises(EmbeddingFailure):self.kb.rebuild()
  self.assertEqual(self.kb.status()['status'],'failed');self.assertTrue((self.kb.indexroot/first['generation']).exists())
  with self.assertRaises(KnowledgeUnavailable):self.kb.retrieve('CRM',{'role':'support'},{'knowledge':['policy']})
 def test_publish_state_failure_rollback(self):
  first=self.kb.rebuild();old=(self.kb.indexroot/'current.json').read_bytes();original=self.db.transact
  def fail(changes):
   if changes[0][1].get('status')=='ready' and changes[0][1].get('generation')!=first['generation']:raise RuntimeError('StoreWrite')
   return original(changes)
  with patch.object(self.db,'transact',side_effect=fail):
   with self.assertRaises(RuntimeError):self.kb.rebuild()
  self.assertEqual(old,(self.kb.indexroot/'current.json').read_bytes());self.assertTrue(self.kb.inspect_index()['integrity_ready'])
 def test_crashed_pointer_can_recover(self):
  self.kb.rebuild();atomic_json(self.kb.indexroot/'current.json',{'generation':'f'*32});self.assertFalse(self.kb.inspect_index()['integrity_ready']);self.kb.reconcile();self.assertTrue(self.kb.inspect_index()['integrity_ready'])
 def test_broken_pointer_json_can_reconcile(self):
  self.kb.rebuild();(self.kb.indexroot/'current.json').write_text('{broken')
  self.assertFalse(self.kb.inspect_index()['integrity_ready']);self.kb.reconcile();self.assertTrue(self.kb.inspect_index()['integrity_ready'])
 def test_parallel_build_busy_not_failure(self):
  start=threading.Event();finish=threading.Event();original=self.kb.sources;errors=[]
  def sources():start.set();finish.wait(2);return original()
  self.kb.sources=sources
  def build():
   try:self.kb.rebuild()
   except Exception as e:errors.append(e)
  thread=threading.Thread(target=build);thread.start();self.assertTrue(start.wait(1))
  try:
   with self.assertRaises(IndexBuildBusy):Knowledge(self.cfg,self.db).rebuild()
   self.assertNotEqual(self.kb.status()['status'],'failed')
  finally:finish.set();thread.join()
  self.assertFalse(errors);self.assertTrue(self.kb.inspect_index()['integrity_ready'])
 def test_midbuild_change_no_publish(self):
  original=self.kb.sources;n=[0]
  def sources():
   n[0]+=1;v=original()
   if n[0]>1:v[0]=(v[0][0],v[0][1]+' changed',v[0][2])
   return v
  self.kb.sources=sources
  with self.assertRaises(KnowledgeUnavailable):self.kb.rebuild()
  self.assertFalse((self.kb.indexroot/'current.json').exists())
 def test_corrupt_artifact_unready(self):
  state=self.kb.rebuild();(self.kb.indexroot/state['generation']/'chunks.json').write_text('[]');self.assertFalse(self.kb.inspect_index()['integrity_ready'])
 def test_tampered_chunks_cannot_be_queried(self):
  state=self.kb.rebuild();(self.kb.indexroot/state['generation']/'chunks.json').write_text('[]')
  with self.assertRaises(KnowledgeUnavailable):self.kb.retrieve('CRM',{'role':'support'},{'knowledge':['policy']})
 def test_reindex_cli_reconcile_argument_is_used(self):
  import contextlib
  from app import ops
  self.kb.rebuild();generation=self.kb.status()['generation']
  with patch.object(sys,'argv',['ops','reindex','--reconcile']),patch.object(ops,'Settings',return_value=self.cfg),patch.object(ops,'Store',return_value=self.db),patch.object(ops,'Knowledge',return_value=self.kb),contextlib.redirect_stdout(io.StringIO()):ops.main()
  self.assertEqual(generation,self.kb.status()['generation'])
 def test_acl_and_archived_date_filters(self):
  self.kb.rebuild();user={'role':'support','department':'客服部','title':'客服專員'};chunks,_=self.kb.retrieve('公司營運月報 CRM',user,{'knowledge':['policy','support','handover']});self.assertTrue(all(c['document']['policy_version']!='2024.1' and c['document']['document_id']!='operations-report' for c in chunks))
  chunks,_=self.kb.retrieve('交接清單',dict(user,department='資訊部'),{'knowledge':['handover']});self.assertTrue(all(c['document']['document_id']!='support-handover' for c in chunks));doc=dict(lifecycle_status='current',effective_from='2999-01-01',allowed_roles=['support'],scope='policy');self.assertFalse(self.kb.eligible(doc,user,{'knowledge':['policy']}))
 def test_overlapping_policy_closed(self):
  self.kb.rebuild();shutil.copyfile(self.cfg.knowledge/'CRM 存取規則 2026.md',self.cfg.knowledge/'duplicate.md')
  with self.assertRaises(Invalid):self.kb.rebuild()
  self.assertEqual(self.kb.status()['status'],'failed')
 def test_plan_no_model_no_state_change(self):
  before=self.kb.status();plan=self.kb.plan_rebuild();self.assertEqual(plan['documents'],6);self.assertEqual(plan['max_embedding_attempts'],4*plan['uncached_chunks']);self.assertEqual(before,self.kb.status());self.kb.rebuild();self.assertEqual(self.kb.plan_rebuild()['uncached_chunks'],0)
 def test_sqs_duplicate_no_rebuild(self):
  cfg=replace(self.cfg,bucket='bucket');q=Queue();gate=RetryGate(cfg,self.clock.now)
  with patch.object(self.kb,'reconcile',wraps=self.kb.reconcile) as rec:
   self.assertTrue(process_message(cfg,self.db,self.kb,q,self.msg('2'),gate,NoHeartbeat));self.assertTrue(process_message(cfg,self.db,self.kb,q,self.msg('1'),gate,NoHeartbeat));self.assertEqual(rec.call_count,1)
  self.assertEqual(len(q.deleted),2)
 def test_sqs_failed_unacked_then_retry(self):
  cfg=replace(self.cfg,bucket='bucket');q=Queue();gate=RetryGate(cfg,self.clock.now,jitter=lambda a,b:0)
  with patch.object(self.kb,'reconcile',side_effect=EmbeddingFailure('ThrottlingException',True,4,1)):self.assertFalse(process_message(cfg,self.db,self.kb,q,self.msg(),gate,NoHeartbeat))
  self.assertFalse(q.deleted);self.assertFalse(self.db.scan('sequencer:'));self.assertGreaterEqual(q.visibility[-1]['VisibilityTimeout'],300);self.clock.sleep(300);self.assertTrue(process_message(cfg,self.db,self.kb,q,self.msg(),gate,NoHeartbeat))
 def test_malformed_sqs_redrive_contract(self):
  cfg=replace(self.cfg,bucket='bucket');q=Queue();gate=RetryGate(cfg,self.clock.now);msg=self.msg(body='{bad');dlq=[]
  for attempt in range(1,4):msg['Attributes']['ApproximateReceiveCount']=str(attempt);self.assertFalse(process_message(cfg,self.db,self.kb,q,msg,gate,NoHeartbeat))
  if not q.deleted:dlq.append(msg) # Fake broker redrive contract, not real AWS SQS evidence.
  self.assertEqual(len(dlq),1);self.assertFalse(gate.suspended)
 def test_multirecord_single_reconcile(self):
  cfg=replace(self.cfg,bucket='bucket');q=Queue();gate=RetryGate(cfg,self.clock.now);msg=self.msg();obj=json.loads(msg['Body']);obj['Records']+=json.loads(self.msg('2','knowledge/b.md')['Body'])['Records'];msg['Body']=json.dumps(obj)
  with patch.object(self.kb,'reconcile',wraps=self.kb.reconcile) as rec:self.assertTrue(process_message(cfg,self.db,self.kb,q,msg,gate,NoHeartbeat));self.assertEqual(rec.call_count,1)
 def test_sqs_testevent_no_build(self):
  cfg=replace(self.cfg,bucket='bucket');q=Queue();gate=RetryGate(cfg,self.clock.now)
  with patch.object(self.kb,'reconcile') as rec:self.assertTrue(process_message(cfg,self.db,self.kb,q,self.msg(body=json.dumps({'Event':'s3:TestEvent'})),gate,NoHeartbeat));rec.assert_not_called()
 def test_visibility_heartbeat_stopped(self):
  q=Queue()
  with VisibilityHeartbeat(q,self.cfg,self.msg(),'trace',interval=.01) as hb:self.assertTrue(hb.thread.is_alive());hb.extend()
  self.assertFalse(hb.thread.is_alive());self.assertEqual(q.visibility[0]['VisibilityTimeout'],300)
 def test_worker_cooldown_and_suspend_purge_continues(self):
  cfg=replace(self.cfg,index_max_failures=2);domain=Mock();worker=Worker(cfg,self.db,self.kb,domain,Queue(),self.clock.now,self.clock.sleep)
  with patch.object(self.kb,'reconcile',side_effect=EmbeddingFailure('ThrottlingException',True,4,1)) as rec:
   worker.tick();worker.tick();self.assertEqual(rec.call_count,1);self.clock.sleep(1900);worker.tick();self.assertTrue(worker.gate.suspended);self.clock.sleep(1900);worker.tick();self.assertEqual(rec.call_count,2);self.assertGreaterEqual(domain.purge_due.call_count,3)
 def test_safe_logs(self):
  c,p=self.client([Error('AccessDeniedException',400)])
  with self.assertRaises(EmbeddingFailure):c.invoke('private employee data')
  logs=str(self.logs);self.assertNotIn('NEVER_LOG',logs);self.assertNotIn('private employee',logs);self.assertIn('AccessDeniedException',logs)
 def test_metric_delivery_failure_is_nonfatal(self):
  from app import observability
  with patch.object(observability,'metric_client',side_effect=RuntimeError('FakeTelemetryFailure')):
   observability.emit(replace(self.cfg,mode='aws'),'indexer','embedding','success','test-trace',{'EmbeddingRequests':1})
 def test_metric_sdk_has_finite_timeouts(self):
  from app import observability
  boto=types.ModuleType('boto3');boto.client=Mock(return_value=Mock());module=types.ModuleType('botocore.config');module.Config=lambda **kw:kw
  observability.metric_client.cache_clear()
  try:
   with patch.dict(sys.modules,{'boto3':boto,'botocore':types.ModuleType('botocore'),'botocore.config':module}):observability.metric_client(self.cfg.region)
   cfg=boto.client.call_args.kwargs['config'];self.assertEqual(cfg['retries']['total_max_attempts'],2);self.assertEqual(cfg['read_timeout'],5)
  finally:observability.metric_client.cache_clear()
 def test_invalid_settings(self):
  for k,v in [('embed_min_interval',0),('embed_max_attempts',0),('embed_dimensions',12),('embed_retry_budget',float('nan'))]:
   with self.assertRaises(ValueError):replace(self.cfg,**{k:v}).validate()
if __name__=='__main__':
 print('FAISS backend: '+('NumPy test double; real FAISS pending' if FAISS_DOUBLE else 'real FAISS'),flush=True)
 unittest.main(verbosity=2)

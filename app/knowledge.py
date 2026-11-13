import json,hashlib,os,re,uuid,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import faiss,yaml
from filelock import FileLock, Timeout
from .domain import KnowledgeUnavailable, Denied,Invalid,now
from .embedding import EmbeddingClient, atomic_json, atomic_bytes, error_details
from .observability import emit

class IndexBuildBusy(KnowledgeUnavailable): pass

class Knowledge:
    def __init__(self,settings,store):
        self.cfg=settings;self.db=store;self.indexroot=settings.root/'indexes';self.indexroot.mkdir(parents=True,exist_ok=True)
        self.e5=None;self.state_key='index:state'
        if getattr(settings,'embed_provider','default')=='e5':
            from .e5_provider import E5Provider
            self.e5=E5Provider(settings.e5_model_dir)
            self.indexroot=settings.root/'indexes-e5-v1';self.indexroot.mkdir(parents=True,exist_ok=True)
            self.state_key='index:state:e5-v1'
        self.embed_client=EmbeddingClient(settings,self.indexroot)
    def sources(self):
        if self.cfg.mode=='local':return [(str(p.relative_to(self.cfg.knowledge)).replace('\\','/'),p.read_bytes().decode('utf-8'),None) for p in sorted(self.cfg.knowledge.rglob('*.md'))]
        import boto3
        s3=boto3.client('s3',region_name=self.cfg.region);sources=[]
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=self.cfg.bucket,Prefix='knowledge/'):
            for x in page.get('Contents',[]):
                if x['Key'].endswith('.md'):
                    if x['Size']>100000:raise Invalid('知識文件超過 100 KB。')
                    obj=s3.get_object(Bucket=self.cfg.bucket,Key=x['Key']);sources.append((x['Key'],obj['Body'].read().decode('utf-8'),obj.get('VersionId')))
        return sources
    def parse(self,key,text,version):
        match=re.fullmatch(r'---\s*\n([\s\S]*?)\n---\s*\n([\s\S]+)',text)
        if not match:raise Invalid('文件缺少前置中繼資料：'+key)
        m=yaml.safe_load(match[1]);required=('document_id','policy_version','effective_from','lifecycle_status','allowed_roles','scope')
        if not isinstance(m,dict) or any(k not in m for k in required):raise Invalid('文件標記不完整：'+key)
        m={k:(str(v) if k in ('effective_from','effective_to','policy_version') and v is not None else v) for k,v in m.items()}
        if m['lifecycle_status'] not in ('current','archived') or not isinstance(m['allowed_roles'],list) or m['scope'] not in ('policy','support','it','handover','operations','maintenance'):raise Invalid('文件授權標記不正確。')
        for k in ('allowed_departments','allowed_job_titles'):
            if k in m and not isinstance(m[k],list):raise Invalid('文件部門／職稱標記需為清單。')
        datetime.fromisoformat(m['effective_from'])
        if m.get('effective_to'):datetime.fromisoformat(m['effective_to'])
        return dict(m,key=key,version_id=version,sha256=hashlib.sha256(text.encode()).hexdigest(),body=match[2])
    def eligible(self,doc,u,scope):
        from .it_maintenance_access import alias_access
        today=datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
        current=doc['lifecycle_status']=='current' and doc['effective_from']<=today and (not doc.get('effective_to') or today<=doc['effective_to'])
        normal=(u['role'] in doc['allowed_roles'] and (not doc.get('allowed_departments') or u['department'] in doc['allowed_departments']) and (not doc.get('allowed_job_titles') or u['title'] in doc['allowed_job_titles']))
        return bool(current and doc['scope'] in scope['knowledge'] and (normal or alias_access(doc,u)))

    def chunk_body(self,body):
        return self.e5.chunks(body) if self.e5 else [body[start:start+1000] for start in range(0,len(body),800)]
    def embedding_contract(self):
        if self.e5:return dict(self.e5.contract,mode=self.cfg.mode)
        return {'schema':2,'mode':self.cfg.mode,'region':self.cfg.region,'model':self.cfg.embed_model,
                  'dimensions':128 if self.cfg.mode=='local' else self.cfg.embed_dimensions,
                  'normalize':True if self.cfg.mode=='local' else self.cfg.embed_normalize}
    def _cache_paths(self,text,purpose='document'):
        cache=self.indexroot/'embedding-cache'
        contract=self.embedding_contract()
        if self.e5:
            from .e5_protocol import cache_key
            key=cache_key(contract,purpose,text)
            return key,cache/(key+'.json'),None
        key=hashlib.sha256((json.dumps(contract,sort_keys=True)+':'+text).encode()).hexdigest()
        path=cache/(key+'.json')
        legacy=cache/(hashlib.sha256((self.cfg.mode+':'+self.cfg.embed_model+':'+text).encode()).hexdigest()+'.json')
        compatible=self.cfg.mode=='local' or (self.cfg.region=='us-east-1' and self.cfg.embed_dimensions==1024 and self.cfg.embed_normalize)
        return key,path,legacy if compatible else None

    def plan_rebuild(self):
        docs=[self.parse(*s) for s in self.sources()]
        unique={}
        chunks=0
        for doc in docs:
            for body in self.chunk_body(doc['body']):
                key,path,legacy=self._cache_paths(body)
                chunks+=1
                cached=False
                for candidate in (path,legacy):
                    if candidate is not None and candidate.exists():
                        self._validate_vector(json.loads(candidate.read_text(encoding='utf-8')));cached=True;break
                unique[key]=(cached,len(body))
        missing=[length for cached,length in unique.values() if not cached]
        return dict(documents=len(docs),chunks=chunks,unique_chunks=len(unique),uncached_chunks=len(missing),
                    uncached_characters=sum(missing),embedding_calls_if_success=len(missing),
                    max_embedding_attempts=len(missing)*self.cfg.embed_max_attempts,
                    model_id=self.cfg.embed_model,dimensions=self.cfg.embed_dimensions,
                    note='Read-only plan; actual token count, price, quota and retry billing require account verification.')

    def embedding(self,text,purpose='document'):
        cache=self.indexroot/'embedding-cache';cache.mkdir(exist_ok=True)
        key,path,legacy=self._cache_paths(text,purpose)
        # Serialize identical misses as well as atomic writes, across every container.
        with FileLock(str(cache/(key+'.lock')),timeout=self.cfg.index_lock_timeout):
            if path.exists():return self._validate_vector(json.loads(path.read_text(encoding='utf-8')))
            if legacy is not None and legacy.exists():
                vector=self._validate_vector(json.loads(legacy.read_text(encoding='utf-8')))
            elif self.e5:vector=self._validate_vector(self.e5.embed(text,purpose))
            elif self.cfg.mode=='local':
                v=np.zeros(128,dtype=np.float32)
                for char in re.findall(r'[\u4e00-\u9fff]|[a-z0-9]+',text.lower()):v[int(hashlib.sha256(char.encode()).hexdigest()[:8],16)%128]+=1
                vector=(v/(np.linalg.norm(v) or 1)).tolist()
            else:vector=self._validate_vector(self.embed_client.invoke(text,trace_id=getattr(self,'_build_trace',None)))
            atomic_json(path,vector)
            return vector

    def _validate_vector(self,vector):
        dimensions=self.embedding_contract()['dimensions']
        if not isinstance(vector,list) or len(vector)!=dimensions or any(isinstance(x,bool) or not isinstance(x,(int,float)) for x in vector):
            raise Invalid('Embedding vector dimensions/type mismatch; cache not updated.')
        if not np.isfinite(np.asarray(vector,dtype=np.float32)).all():raise Invalid('Embedding vector must be finite.')
        if self.e5:
            from .e5_protocol import validate_vector
            validate_vector(vector)
        return vector

    @staticmethod
    def source_digest(sources):
        rows=sorted((key,hashlib.sha256(text.encode()).hexdigest(),version or '') for key,text,version in sources)
        return hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()

    def status(self):return self.db.get(self.state_key)[0] or {'status':'uninitialized'}

    def inspect_index(self):
        state=self.status();result=dict(state,integrity_ready=False)
        try:
            if state.get('embedding_contract')!=self.embedding_contract():
                return dict(result,integrity_error='EmbeddingContractChangedOrLegacy')
            generation=state['generation']
            if not re.fullmatch(r'[a-f0-9]{32}',generation):return result
            pointer=json.loads((self.indexroot/'current.json').read_text(encoding='utf-8'))
            if pointer.get('generation')!=generation:return dict(result,integrity_error='PointerStateMismatch')
            folder=self.indexroot/generation
            chunks=json.loads((folder/'chunks.json').read_text(encoding='utf-8'))
            index=faiss.deserialize_index(np.frombuffer((folder/'all.faiss').read_bytes(), dtype=np.uint8))
            if not chunks or index.ntotal!=len(chunks):return dict(result,integrity_error='IndexCountMismatch')
            for chunk in chunks:self._validate_vector(chunk['vector'])
            expected=self.embedding_contract()['dimensions']
            if index.d!=expected:return dict(result,integrity_error='IndexDimensionsMismatch')
            manifest_path=folder/'manifest.json'
            if manifest_path.exists():
                manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
                if any(manifest.get(k)!=state.get(k) for k in ('generation','digest','documents','chunks','dimensions','embedding_contract')):
                    return dict(result,integrity_error='ManifestStateMismatch')
                for filename in ('chunks.json','all.faiss'):
                    if hashlib.sha256((folder/filename).read_bytes()).hexdigest()!=manifest['checksums'][filename]:
                        return dict(result,integrity_error='ArtifactChecksumMismatch')
            result.update(pointer_generation=pointer['generation'],chunks=len(chunks),dimensions=index.d,
                          integrity_ready=state.get('status')=='ready')
            return result
        except Exception as exc:return dict(result,integrity_error=type(exc).__name__)

    def reconcile(self):return self.rebuild(reconcile=True)

    def rebuild(self,reconcile=False):
        trace=uuid.uuid4().hex;started=time.monotonic()
        try:
            lock=FileLock(str(self.indexroot/'build.lock'),timeout=self.cfg.index_lock_timeout)
            with lock:return self._rebuild_locked(reconcile,trace,started)
        except Timeout as exc:
            emit(self.cfg,'indexer','rebuild','deferred',trace,error_type='IndexBuildBusy')
            raise IndexBuildBusy('Another index job holds build.lock; retry after it finishes.') from exc

    def _rebuild_locked(self,reconcile,trace,started):
        self._build_trace=trace
        old,v=self.db.get(self.state_key);old=old or {'status':'uninitialized'}
        healthy=self.inspect_index().get('integrity_ready',False)
        pointer=self.indexroot/'current.json'
        previous=pointer.read_bytes() if pointer.exists() else None
        digest=None;generation=None;docs=[];chunks=[];published=False;source_confirmed=False
        try:
            sources=self.sources();digest=self.source_digest(sources)
            if reconcile and healthy and old.get('digest')==digest:
                return dict(old,reused=True)
            # Queries stay fail-closed throughout a rebuild; only unchanged verified sources may recover the old state.
            self.db.transact([(self.state_key,dict(status='updating',at=now(),previous_generation=old.get('generation'),trace_id=trace),v)])
            docs=[self.parse(*s) for s in sources]
            # Reject simultaneous effective revisions instead of guessing newest year.
            today=datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat();current={}
            for d in docs:
                if d['lifecycle_status']=='current' and d['effective_from']<=today and (not d.get('effective_to') or d['effective_to']>=today):
                    if d['document_id'] in current:raise Invalid('同一政策有重疊生效版本：'+d['document_id'])
                    current[d['document_id']]=d
            for d in docs:
                for body in self.chunk_body(d['body']):
                    chunks.append({'document':{k:v for k,v in d.items() if k!='body'},'text':body,'vector':self.embedding(body)})
            if not chunks:raise Invalid('沒有可索引文件。')
            generation=uuid.uuid4().hex;folder=self.indexroot/generation;folder.mkdir()
            atomic_json(folder/'chunks.json',chunks)
            # A persisted FAISS index is a deployment/restore artifact. Query uses an authorized subset.
            vectors=np.array([c['vector'] for c in chunks],dtype=np.float32);ix=faiss.IndexFlatIP(vectors.shape[1]);ix.add(vectors)
            atomic_bytes(folder/'all.faiss', faiss.serialize_index(ix).tobytes())
            if ix.ntotal!=len(chunks):raise Invalid('FAISS count mismatch.')
            state=dict(status='ready',generation=generation,digest=digest,documents=len(docs),
                       chunks=len(chunks),dimensions=vectors.shape[1],embedding_contract=self.embedding_contract(),at=now(),trace_id=trace)
            manifest=dict(state,checksums={name:hashlib.sha256((folder/name).read_bytes()).hexdigest()
                                          for name in ('chunks.json','all.faiss')})
            atomic_json(folder/'manifest.json',manifest)
            source_confirmed=self.source_digest(self.sources())==digest
            if not source_confirmed:raise KnowledgeUnavailable('Sources changed during rebuild; do not publish this generation.')
            atomic_json(pointer,{'generation':generation});published=True
            _,sv=self.db.get(self.state_key)
            self.db.transact([(self.state_key,state,sv)])
            if not self.inspect_index()['integrity_ready']:raise Invalid('Published index integrity check failed.')
            emit(self.cfg,'indexer','rebuild','success',trace,{'IndexUpdateSuccess':1},
                 model_id=self.cfg.embed_model,index_generation=generation,digest=digest,
                 documents=len(docs),chunks=len(chunks),duration_ms=round((time.monotonic()-started)*1000,1))
            return self.status()
        except Exception as exc:
            # Roll back our pointer, never delete an existing generation or any cache vectors.
            rolled_back=not published
            if published:
                try:
                    if previous is None:pointer.unlink()
                    else:atomic_bytes(pointer,previous)
                    rolled_back=True
                except Exception:pass
            safe=False
            if healthy and rolled_back and digest==old.get('digest'):
                try:safe=self.source_digest(self.sources())==digest
                except Exception:pass
            detail=error_details(exc)
            failed=dict(old,status='ready',last_update_error=detail['error_code'],at=now()) if safe else dict(
                status='failed',at=now(),previous_generation=old.get('generation'),
                candidate_generation=generation,trace_id=trace,error_code=detail['error_code'])
            try:
                _,sv=self.db.get(self.state_key);self.db.transact([(self.state_key,failed,sv)])
            except Exception:
                # A failed store write must not mask the original error or mark the index ready.
                emit(self.cfg,'indexer','index_state_write','failure',trace,error_type='StateWriteFailure')
            emit(self.cfg,'indexer','rebuild','failure',trace,{'IndexUpdateFailure':1},
                 **detail,model_id=self.cfg.embed_model,index_generation=generation,
                 documents=len(docs),chunks=len(chunks),retained_healthy_index=safe,
                 duration_ms=round((time.monotonic()-started)*1000,1))
            raise
        finally:
            self._build_trace=None
    def retrieve(self,question,u,scope):
        state=self.status()
        if state['status']!='ready' or state.get('embedding_contract')!=self.embedding_contract():raise KnowledgeUnavailable('知識庫更新中或模型設定已異動；請先完成索引核對。')
        pointer=json.loads((self.indexroot/'current.json').read_text())
        if pointer['generation']!=state['generation']:raise KnowledgeUnavailable('索引版本切換中。')
        folder=self.indexroot/state['generation']
        raw=(folder/'chunks.json').read_bytes();manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('generation')!=state['generation'] or manifest.get('digest')!=state.get('digest') or hashlib.sha256(raw).hexdigest()!=manifest['checksums']['chunks.json']:
            raise KnowledgeUnavailable('索引檔案與已發布版本不一致。')
        chunks=json.loads(raw.decode('utf-8'))
        allowed=[c for c in chunks if self.eligible(c['document'],u,scope)]
        if not allowed:return [],state['generation']
        vectors=np.array([c['vector'] for c in allowed],dtype=np.float32);index=faiss.IndexFlatIP(vectors.shape[1]);index.add(vectors)
        scores,positions=index.search(np.array([self.embedding(question,purpose='query')],dtype=np.float32),min(4,len(allowed)))
        results=[dict(allowed[p],score=float(score)) for score,p in zip(scores[0],positions[0]) if p>=0 and score>(self.cfg.e5_threshold if self.e5 else 0.10)]
        from .case_evidence import retrieval_context
        results=retrieval_context(allowed,results,question)
        self.assert_current(results,state['generation']);return results,state['generation']
    def assert_current(self,results,generation):
        state=self.status()
        if state.get('status')!='ready' or state.get('generation')!=generation or state.get('embedding_contract')!=self.embedding_contract():raise KnowledgeUnavailable('引用版本或模型設定已異動，請稍後重試。')
        # Recheck the actual sources before/after model invocation, not only cached manifests.
        if self.cfg.mode=='aws':
            import boto3
            s3=boto3.client('s3',region_name=self.cfg.region)
            for c in results:
                doc=c['document'];obj=s3.head_object(Bucket=self.cfg.bucket,Key=doc['key'])
                if obj.get('VersionId')!=doc['version_id']:raise KnowledgeUnavailable('來源文件正在更新。')
        else:
            for c in results:
                doc=c['document'];path=self.cfg.knowledge/doc['key']
                if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=doc['sha256']:raise KnowledgeUnavailable('來源文件正在更新。')

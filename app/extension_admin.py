"""Operator-only source/index observations. No public write API or model calls."""
import hashlib,json,sys
from .config import Settings
from .store import Store
from .knowledge import Knowledge

def observe(cfg):
 db=Store(cfg);kb=Knowledge(cfg,db)
 docs=[{'key':key,'sha256':hashlib.sha256(text.encode()).hexdigest(),'version_id':version} for key,text,version in kb.sources()]
 index=kb.inspect_index();digest=hashlib.sha256(json.dumps(sorted((d['key'],d['sha256'],d['version_id'] or '') for d in docs),ensure_ascii=False).encode()).hexdigest()
 return {'sources':sorted(docs,key=lambda x:x['key']),'index':index,'source_current':index.get('digest')==digest,
         'maintenance_users':sum(u.get('role')=='maintenance' for _,u,_ in db.scan('user:')),
         'maintenance_agents':sum(a.get('profile')=='maintenance' for _,a,_ in db.scan('agent:'))}

def reconcile(cfg,expected):
 before=observe(cfg)
 if before['sources']!=expected:raise ValueError('SOURCE_CHANGED_BEFORE_REINDEX')
 result=Knowledge(cfg,Store(cfg)).rebuild(reconcile=True)
 after=observe(cfg)
 if after['sources']!=expected or not after['index'].get('integrity_ready') or not after['source_current']:raise ValueError('SOURCE_OR_INDEX_NOT_READY')
 return after

def main():
 cfg=Settings();cfg.validate();action=sys.argv[1]
 if action=='observe':result=observe(cfg)
 elif action=='index-plan':result=Knowledge(cfg,Store(cfg)).plan_rebuild()
 elif action=='reindex':result=reconcile(cfg,json.load(sys.stdin))
 else:raise ValueError('Unknown operator action')
 print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()

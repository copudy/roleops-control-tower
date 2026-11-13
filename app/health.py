"""Readiness and last observed provider result. Never sends a model probe."""
import json,time
from pathlib import Path
from .private_secret import secret_metadata
from .runtime_release import version

def snapshot(cfg,db,kb):
 db.get('bootstrap');credential=secret_metadata(cfg.groq_key_file)
 last={'status':'not-probed'}
 try:
  observed=json.loads((Path(cfg.root)/'provider-status.json').read_text(encoding='utf-8'))
  if observed.get('model')==cfg.groq_model:last={'status':'last-call-'+observed.get('phase','unknown'),'stale':time.time()-observed.get('at',0)>300,'observed':observed}
 except (OSError,ValueError,TypeError):pass
 return {'web':'ready','storage':'ready','index':kb.status(),'mode':cfg.mode,'embedding':kb.embedding_contract(),
  'generation':{'provider':cfg.generation_provider,'configured_model':cfg.groq_model,'credential':credential,'output_contract':'authorized-source-facts-subquestion-v2',**last},
  'mcp':'not-probed; run app.ops check-mcp','build':version()}

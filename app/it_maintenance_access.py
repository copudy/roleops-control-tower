"""Reviewed exact-source role/department equivalence; original metadata stays.

Never rewrites a user's identity or source metadata. Only exact approved sources
and explicit department/title pairs gain the unified IT capability.
"""
import json
from pathlib import Path
SOURCE_IDS={'crm-access','it-sop','maintenance-manual','maintenance-history'}
PAIRS={('IT','IT 專員'),('IT','IT專員'),('IT','IT 主管'),('IT','IT主管'),('IT','維修工程師'),('IT','維修主管'),('維修部','維修工程師'),('維修部','維修主管')}
FIELDS=('sha256','policy_version','lifecycle_status','scope','allowed_roles','allowed_departments','allowed_job_titles')
def alias_access(doc,user):
    if user.get('role')=='support':
        allowed=(user.get('department'),user.get('title')) in {('客服','客服專員'),('客服','客服主管')}
        ids={'support-case','support-contract','support-approval'}
    else:
        allowed=user.get('role') in ('it','maintenance') and (user.get('department'),user.get('title')) in PAIRS
        ids=SOURCE_IDS
    if not allowed or doc.get('document_id') not in ids or doc.get('lifecycle_status')!='current':return False
    known=json.loads(Path(__file__).with_name('synthetic_registry.json').read_bytes())['documents'].get(doc['document_id'])
    return bool(known) and all(doc.get(k)==known.get(k) for k in FIELDS)

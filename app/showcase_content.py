"""Engineer-published content. No website write API; no RAG integration."""
import hashlib,json,re,os
from pathlib import Path

def safe_version(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',value) or '..' in value:
        raise ValueError('版本名稱不正確')
    return value

def asset_path(folder,value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value:
        raise ValueError('附件路徑不正確')
    folder=Path(folder).resolve();target=(folder/value).resolve()
    if not target.is_relative_to(folder) or target==folder:raise ValueError('附件超出展示資料包')
    if target.suffix.lower() not in ('.md','.txt','.png','.jpg','.jpeg','.pdf'):raise ValueError('附件格式不支援')
    return target

def validate(folder):
    folder=Path(folder);raw=(folder/'content.json').read_bytes()
    if len(raw)>1_000_000:raise ValueError('content.json 超過 1 MB')
    data=json.loads(raw)
    if data.get('schema')!=1:raise ValueError('展示 schema 必須為 1')
    safe_version(data.get('version'))
    for group in ('reports','journals'):
        items=data.get(group)
        if not isinstance(items,list) or len(items)>100:raise ValueError('報告與日誌需為最多 100 筆清單')
        ids=set()
        for item in items:
            if not isinstance(item,dict):raise ValueError('紀錄必須為物件')
            safe_version(item.get('id'))
            if item['id'] in ids:raise ValueError('紀錄 ID 重複')
            ids.add(item['id'])
            fields=('title','snapshot_date','status','problem','impact','reasoning','change','verification','prevention','technical','contribution') if group=='reports' else ('title','snapshot_date','status','purpose','action','result','next','technical','incident_id')
            if any(not isinstance(item.get(k),str) or not item[k].strip() or len(item[k])>10000 for k in fields):raise ValueError('紀錄欄位不完整或超過長度')
            if not isinstance(item.get('assets'),list):raise ValueError('assets 需為清單')
    assets=data.get('assets')
    if not isinstance(assets,dict) or len(assets)>100:raise ValueError('附件清單不正確')
    for key,a in assets.items():
        safe_version(key)
        if not isinstance(a,dict) or not isinstance(a.get('label'),str):raise ValueError('附件描述不完整')
        p=asset_path(folder,a.get('path'))
        if not p.is_file() or p.stat().st_size>10_000_000:raise ValueError('附件不存在或超過 10 MB')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=a.get('sha256'):raise ValueError('附件 SHA-256 不符')
    for group in ('reports','journals'):
        for item in data[group]:
            if any(x not in assets for x in item['assets']):raise ValueError('紀錄引用不存在的附件')
    report_ids={r['id'] for r in data['reports']}
    if any(j['incident_id'] not in report_ids for j in data['journals']):raise ValueError('日誌關聯事故不存在')
    return data,hashlib.sha256(raw).hexdigest()

def atomic_json(path,data):
    import uuid
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,path)

def publish(root,version):
    root=Path(root);safe_version(version);folder=root/'releases'/version
    data,digest=validate(folder)
    if data['version']!=version:raise ValueError('資料版本與目錄不一致')
    atomic_json(root/'current.json',{'version':version,'sha256':digest})
    return {'version':version,'sha256':digest,'reports':len(data['reports']),'journals':len(data['journals'])}

def load(root):
    root=Path(root);pointer=json.loads((root/'current.json').read_text(encoding='utf-8'))
    version=safe_version(pointer.get('version'));folder=root/'releases'/version
    data,digest=validate(folder)
    if data['version']!=version or digest!=pointer.get('sha256'):raise ValueError('發布內容與指標不一致，停止展示')
    return data,folder

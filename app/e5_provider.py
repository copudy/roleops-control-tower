"""Pinned real E5 runtime for the isolated integration candidate.

No synthetic fallback. Embedding and data backends are separate choices.
"""
import hashlib
import math
from pathlib import Path
from .e5_protocol import ContractError, split_by_offsets, validate_vector

CONTRACT={
 'schema':3,'provider':'onnx-e5','model':'intfloat/multilingual-e5-small',
 'revision':'614241f622f53c4eeff9890bdc4f31cfecc418b3',
 'weights_sha256':'ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665',
 'tokenizer_sha256':'0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39',  # gitleaks:allow (model file SHA-256)
 'dimensions':384,'pooling':'attention-mask mean then L2','normalize':True,
 'query_prefix':'query: ','document_prefix':'passage: ',
 'chunking_version':'tokenizer-offset-480-overlap-48-v1','max_input_tokens':512,
}

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

class E5Runtime:
 def __init__(self,model_dir):
  model_dir=Path(model_dir)
  for name,key in [('model.onnx','weights_sha256'),('tokenizer.json','tokenizer_sha256')]:
   if digest(model_dir/name)!=CONTRACT[key]:raise ContractError('Pinned E5 asset hash mismatch')
  import numpy as np
  import onnxruntime as ort
  from tokenizers import Tokenizer
  self.np=np
  self.tokenizer=Tokenizer.from_file(str(model_dir/'tokenizer.json'))
  self.tokenizer.no_truncation();self.tokenizer.no_padding()
  opts=ort.SessionOptions();opts.intra_op_num_threads=1;opts.inter_op_num_threads=1
  self.session=ort.InferenceSession(str(model_dir/'model.onnx'),opts,providers=['CPUExecutionProvider'])
 def offsets(self,text):return self.tokenizer.encode(text,add_special_tokens=False).offsets
 def vector(self,prefixed):
  np=self.np;x=self.tokenizer.encode(prefixed)
  if len(x.ids)>512:raise ContractError('E5 prefix-inclusive token limit exceeded; no truncation')
  arrays={'input_ids':np.array([x.ids],dtype=np.int64),'attention_mask':np.array([x.attention_mask],dtype=np.int64),'token_type_ids':np.array([x.type_ids],dtype=np.int64)}
  output=self.session.run(None,{n.name:arrays[n.name] for n in self.session.get_inputs()})[0]
  if output.ndim!=3 or output.shape[-1]!=384:raise ContractError('Unexpected real E5 output geometry')
  mask=arrays['attention_mask'][...,None];v=((output*mask).sum(1)/mask.sum(1))[0]
  norm=float(np.linalg.norm(v))
  if not math.isfinite(norm) or norm<=0:raise ContractError('Invalid E5 vector norm')
  return validate_vector((v/norm).tolist())

class E5Provider:
 def __init__(self,model_dir,runtime=None):
  self.contract=dict(CONTRACT)
  self.runtime=runtime if runtime is not None else E5Runtime(model_dir)
 def pieces(self,text):
  if not isinstance(text,str) or not text.strip():raise ContractError('Embedding text is empty')
  return split_by_offsets(text,self.runtime.offsets(text))
 def chunks(self,text):return [p['text'] for p in self.pieces(text)]
 def embed(self,text,purpose):
  if purpose not in ('query','document'):raise ContractError('Invalid E5 purpose')
  pieces=self.pieces(text)
  prefix=self.contract['query_prefix' if purpose=='query' else 'document_prefix']
  total=[0.0]*384;last=0
  for p in pieces:
   v=validate_vector(self.runtime.vector(prefix+p['text']))
   # Non-overlapping coverage weights avoid counting overlap twice in a long query.
   weight=p['end']-max(last,p['start'])
   if weight<=0:raise ContractError('Invalid E5 chunk coverage')
   total=[x+weight*y for x,y in zip(total,v)];last=p['end']
  norm=math.sqrt(math.fsum(x*x for x in total))
  if not math.isfinite(norm) or norm<=0:raise ContractError('Invalid aggregated E5 vector')
  return validate_vector([x/norm for x in total])

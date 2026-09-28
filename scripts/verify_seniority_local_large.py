"""Verify larger demo artifacts directly by IDs, inputs and saved weights."""
import json
from pathlib import Path
import numpy as np
from scripts.demo_seniority_local_train import chunks,norm,sigmoid,metrics
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--root',type=Path,default=Path('artifacts/seniority-demo-1000'))
root=parser.parse_args().root;out=root/'local-model'
jobs=json.loads((root/'results.json').read_text());labels=json.loads((root/'luna-audit/results.json').read_text());splits=json.loads((out/'splits.json').read_text());pred=json.loads((out/'predictions.json').read_text());summary=json.loads((out/'summary.json').read_text())
assert len(jobs)==len(labels)==len(splits)==1000
assert [r['sample_id'] for r in jobs]==[r['sample_id'] for r in labels]
assert len({r['id'] for r in jobs})==1000
company_splits={};text_splits={}
for j,l,s in zip(jobs,labels,splits):
 assert j['id']==l['id']==s['id']
 valid=all(q and (q in j['title'] or q in j['jd_text']) for q in l['evidence']) and (l['decision']!='block' or bool(l['evidence']))
 assert valid==l['evidence_valid']
 if s['split']=='challenge':continue
 for key,groups in [(norm(j['company']),company_splits),(norm(j['jd_text']),text_splits)]:
  if key in groups:assert groups[key]==s['split']
  groups[key]=s['split']
emb=np.load(out/'embeddings.npy');inputs=json.loads((out/'embedding_inputs.json').read_text());t=[];body=[];offset=0
for j in jobs:
 cs=chunks(j['jd_text']);assert ''.join(cs)==j['jd_text']
 assert inputs[offset:offset+len(cs)+1]==[j['title']]+cs
 t.append(emb[offset]);body.append(emb[offset+1:offset+len(cs)+1].mean(axis=0));offset+=len(cs)+1
assert offset==len(inputs)==len(emb)
body=np.array(body);body/=np.maximum(np.linalg.norm(body,axis=1,keepdims=True),1e-12)
arrays={'title_only':np.array(t),'title_plus_full_jd':np.concatenate([np.array(t),body],axis=1)/np.sqrt(2)}
y=np.array([int(r['decision']=='block') for r in labels])
for name,x in arrays.items():
 m=np.load(out/f'{name}.npz');p=sigmoid(x@m['weights']+m['intercept'])
 assert np.allclose(p,[r['probability'] for r in pred if r['model']==name],rtol=0,atol=1e-12)
 for split in ['train','dev','test']:
  ix=[i for i,r in enumerate(splits) if r['split']==split]
  assert metrics(p[ix],y[ix],float(m['auto_keep_below']),float(m['auto_block_above']))==summary['results'][name][split]
(out/'verification.json').write_text(json.dumps(dict(status='passed',n=1000,label_id_alignment=True,exact_evidence_rechecked=True,company_and_identical_text_split_isolation=True,all_input_characters_covered=True,saved_weights_reproduce_scores=True,split_metrics_recomputed=True),indent=2))
print('PASS: 1000 IDs/labels, original quotes, grouped splits, cached input alignment, saved models and recomputed metrics')

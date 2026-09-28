"""Offline rule/Luna cascade replay. Teacher agreement is not measured accuracy."""
import collections,csv,html,json,random,re
from pathlib import Path
from scripts.demo_seniority_100 import classify,SENIOR
ROOT=Path('artifacts/seniority-demo-recent3d-1000-20260918')
OUT=ROOT/'cascade-demo'
MTS=re.compile(r'\bmember of (?:the )?technical staff\b',re.I)
YEARS=re.compile(r'\b(\d{1,2})\s*(?:\+|[-–]\s*\d{1,2}\+?)?\s+years?\b',re.I)

def conflict(job):
    # Conservative ambiguity guard, not a semantic years extractor.
    text=MTS.sub('technical contributor',job['title']+'\n'+job['jd_text'])
    return bool(SENIOR.search(text)) and any(int(m[1])<=3 for m in YEARS.finditer(text))

def rule(job):
    cleaned=dict(job,title=MTS.sub('technical contributor',job['title']))
    result=classify(cleaned)
    if result['decision']=='block' and (conflict(job) or re.search(r'multiple.{0,35}(?:levels|openings)|levels? available|level.{0,80}vary',job['jd_text'],re.I)):
        result.update(decision='uncertain',reason='seniority_and_low_years_need_context')
    return result

def decide(job,label):
    r=rule(job)
    if r['decision']!='uncertain':
        evidence=r['evidence'] or [job['title']]
        return dict(decision=r['decision'],route='rule',reason=r['reason'],evidence=evidence)
    if label is None:
        return dict(decision='keep',route='luna_failure',reason='fail_open',evidence=[])
    quotes=label.get('evidence',[])
    valid=all(q and (q in job['title'] or q in job['jd_text']) for q in quotes)
    if label['decision']=='block' and valid and quotes and not conflict(job):
        return dict(decision='block',route='luna',reason=label['reason'],evidence=quotes)
    reason='conflicting_seniority_and_years' if label['decision']=='block' and conflict(job) else 'invalid_evidence' if not valid or (label['decision']=='block' and not quotes) else label['reason']
    return dict(decision='keep',route='luna',reason=reason,evidence=quotes)

def main():
    OUT.mkdir(exist_ok=True)
    jobs=json.loads((ROOT/'results.json').read_text());labels=json.loads((ROOT/'luna-audit/results.json').read_text())
    assert len(jobs)==len(labels)==1000
    assert all(j['id']==l['id'] and j['sample_id']==l['sample_id'] for j,l in zip(jobs,labels))
    rows=[dict(sample_id=j['sample_id'],id=j['id'],title=j['title'],company=j['company'],url=j['url'],rule_decision=rule(j)['decision'],teacher=l['decision'],teacher_evidence_valid=l['evidence_valid'],**decide(j,l)) for j,l in zip(jobs,labels)]
    keep=[r for r in rows if r['decision']=='keep'];block=[r for r in rows if r['decision']=='block']
    disagreements=[r for r in keep if r['teacher']=='block'];unknown=[r for r in keep if r['teacher']=='uncertain' or not r['teacher_evidence_valid']]
    summary=dict(n=1000,rule_decisions=dict(collections.Counter(r['rule_decision'] for r in rows)),final_decisions=dict(collections.Counter(r['decision'] for r in rows)),rule_blocks=sum(r['route']=='rule' for r in block),luna_blocks=sum(r['route']=='luna' for r in block),luna_routed=sum(r['rule_decision']=='uncertain' for r in rows),new_llm_calls=0,labels_reused=1000,teacher_block_among_pass=len(disagreements),teacher_block_among_pass_rate=len(disagreements)/len(keep),unresolved_teacher_among_pass=len(unknown),teacher_keep_among_blocks=sum(r['teacher']=='keep' for r in block),teacher_uncertain_among_blocks=sum(r['teacher']=='uncertain' for r in block),target_certified=False,limitation='Same Luna labels drive decisions and comparison; NOT independent accuracy or <3% certification. No production changes.')
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));(OUT/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    byid={j['id']:j for j in jobs}
    (OUT/'pass_teacher_block.json').write_text(json.dumps([dict(**r,jd_text=byid[r['id']]['jd_text']) for r in disagreements],ensure_ascii=False,indent=2))
    audit=random.Random(2026091804).sample(keep,min(300,len(keep)))
    # Blind reference material: exclude filter outputs and teacher labels.
    (OUT/'blind_audit_300.json').write_text(json.dumps([dict(id=r['id'],title=r['title'],company=r['company'],jd_text=byid[r['id']]['jd_text'],review_decision=None,review_evidence=[]) for r in audit],ensure_ascii=False,indent=2))
    with (OUT/'results.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    esc=html.escape
    cards=''.join(f'<article><h3>#{r["sample_id"]} {esc(r["title"])}</h3><p>{r["decision"]} · {r["route"]} · {esc(r["reason"])}</p><details><summary>完整JD</summary><pre>{esc(byid[r["id"]]["jd_text"])}</pre></details></article>' for r in rows)
    (OUT/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>规则 + Luna demo</title><style>body{max-width:1000px;margin:36px auto;font:16px system-ui}article{border-top:1px solid #ccc;padding:12px}pre{white-space:pre-wrap}</style><h1>规则 + Luna：1000条离线回放</h1><p>不是独立准确率验证。未修改生产。</p><pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+cards)
    assert len(rows)==len(keep)+len(block)==1000
    assert all(r['evidence'] and all(q in byid[r['id']]['title'] or q in byid[r['id']]['jd_text'] for q in r['evidence']) for r in block)
    (OUT/'verification.json').write_text(json.dumps(dict(status='passed',ids_aligned=True,all_blocks_have_exact_evidence=True,audit_size=len(audit),audit_labels_completed=0),indent=2))
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

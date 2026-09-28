"""Render a traceable rules-versus-Luna comparison, not accuracy claims."""
import csv
import html
import json
from pathlib import Path
root=Path('artifacts/seniority-demo-100');out=root/'luna-audit'
rows=json.loads((out/'results.json').read_text());summary=json.loads((out/'summary.json').read_text());old={r['sample_id']:r for r in json.loads((root/'results.json').read_text())}
fields=['sample_id','id','title','baseline','decision','level','evidence_valid','reason','evidence']
with (out/'comparison.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
e=html.escape;cards=[]
for r in rows:
 flag=r['baseline']=='block' and r['decision']!='block'
 cards.append(f'<article data-flag="{str(flag).lower()}"><h3>#{r["sample_id"]} {e(r["title"])}</h3><p>ID {r["id"]} · 规则 {r["baseline"]} → Luna <b>{r["decision"]}</b> · {r["level"]}</p><p>{e(r["reason"])}</p><p>原文引用校验：{r["evidence_valid"]}</p><ul>'+''.join('<li>'+e(q)+'</li>' for q in r['evidence'])+'</ul><details><summary>完整原始 JD</summary><pre>'+e(old[r['sample_id']]['jd_text'])+'</pre></details></article>')
(out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Luna Seniority Audit</title><style>body{max-width:1000px;margin:35px auto;background:#f5f5f2;font:16px system-ui}article{background:white;padding:22px;margin:18px 0;border-radius:12px}pre{white-space:pre-wrap}li{margin:12px 0}button{padding:12px}</style><h1>Luna × 简单规则：100 条岗位复核</h1><p>同一批冻结 JD，Luna 未看到规则输出。模型对照不是人工真值；引用确实存在也不等于推理正确。未修改岗位状态。美元成本未由 CLI 返回。</p><pre>'+e(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre><button onclick="document.querySelectorAll(\'article\').forEach(x=>x.hidden=x.dataset.flag!==\'true\')">只看规则过滤但 Luna 不同意</button> <button onclick="document.querySelectorAll(\'article\').forEach(x=>x.hidden=false)">全部100条</button>'+''.join(cards))
print('Rendered 100 comparison rows:',out/'index.html')

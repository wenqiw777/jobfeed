"""Render the larger pilot with errors and abstention, not accuracy promises."""

import argparse
import html
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path("artifacts/seniority-demo-1000"))
root = parser.parse_args().root
out = root / "local-model"
s = json.loads((out / "summary.json").read_text())
p = json.loads((out / "predictions.json").read_text())
jobs = {r["sample_id"]: r for r in json.loads((root / "results.json").read_text())}
labels = {
    r["sample_id"]: r
    for r in json.loads((root / "luna-audit/results.json").read_text())
}
rows = [r for r in p if r["model"] == "title_plus_full_jd" and r["split"] == "test"]
errors = [
    r
    for r in rows
    if (r["local_decision"] == "block" and r["teacher"] == "keep")
    or (r["local_decision"] == "keep" and r["teacher"] == "block")
]
(out / "test_auto_errors.json").write_text(
    json.dumps(
        [
            dict(
                **r,
                jd_text=jobs[r["sample_id"]]["jd_text"],
                luna_reason=labels[r["sample_id"]]["reason"],
                luna_evidence=labels[r["sample_id"]]["evidence"],
            )
            for r in errors
        ],
        ensure_ascii=False,
        indent=2,
    )
)
e = html.escape
cards = []
for r in rows:
    j = jobs[r["sample_id"]]
    label = labels[r["sample_id"]]
    error = r in errors
    cards.append(
        (
            '<article data-error="'
            f"{str(error).lower()}"
            '"><h3>#'
            f"{r['sample_id']}"
            " "
            f"{e(r['title'])}"
            "</h3><p>"
            f"{e(r['company'])}"
            " · ID "
            f"{r['id']}"
            " · 本地 "
            f"{r['local_decision']}"
            " / Luna "
            f"{r['teacher']}"
            " · 分数 "
            f"{r['probability']:.4f}"
            "</p><p>"
            f"{e(label['reason'])}"
            "</p><ul>"
        )
        + "".join("<li>" + e(q) + "</li>" for q in label["evidence"])
        + "</ul><details><summary>完整JD</summary><pre>"
        + e(j["jd_text"])
        + "</pre></details></article>"
    )
(out / "index.html").write_text(
    (
        '<!doctype html><meta charset="utf-8"><title>1000-job'
        " local classifier</title><style>body{max-width:1000p"
        "x;margin:40px auto;background:#f4f4f0;font:16px "
        "system-ui}article{padding:20px;background:white;marg"
        "in:20px 0}pre{white-space:pre-wrap}button{padding:12"
        "px}</style><h1>1000岗位本地模型\uff1a留出测试明细</h1><p>L"
        "una标签不是人工真值。分组训练/开发/测试\uff1b仅开发集选阈"
        "值\uff0c测试后不调参。</p><pre>"
    )
    + e(json.dumps(s, ensure_ascii=False, indent=2))
    + (
        "</pre><button onclick=\"document.querySelectorAll('ar"
        "ticle').forEach(x=>x.hidden=x.dataset.error!=='true'"
        ')">只看自动判断分歧</button><button '
        "onclick=\"document.querySelectorAll('article').forEac"
        'h(x=>x.hidden=false)">全部测试样本</button>'
    )
    + "".join(cards)
)
print("Test rows", len(rows), "auto errors", len(errors))
for r in errors:
    print(
        r["sample_id"],
        r["title"],
        r["local_decision"],
        r["teacher"],
        labels[r["sample_id"]]["reason"],
    )

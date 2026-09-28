"""Read-only, zero-LLM-cost seniority demo; never updates application data."""

import collections
import csv
import html
import json
import random
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

SAMPLE_SIZE = 100
MAX_ENTRY_YEARS = 3
MAX_PREFERRED_HEADING_LENGTH = 100
MAX_OTHER_HEADING_LENGTH = 90

OUT = Path("artifacts/seniority-demo-100")
ENTRY = re.compile(
    (
        "\\b(junior|jr\\.?|entry[ -]level|new[ "
        "-]?grad(?:uate)?|intern(?:ship)?|early "
        "career|graduate)\\b"
    ),
    re.I,
)
SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director)\b", re.I)
MID = re.compile(r"\b(mid[ -]?level|(?:engineer|developer)\s+(?:II|III|2|3))\b", re.I)
EXP = re.compile(
    (
        "\\b(\\d{1,2})\\s*(?:\\+|[-\u2013]\\s*\\d{1,2}\\+?)?\\s+yea"
        "rs?[\u2019\\']?\\s+(?:of\\s+)?(?:(?:relevant|professional|"
        "industry|practical|hands[ -]on|work|related|software"
        " development|software engineering|engineering)\\s+){0"
        ",3}experience\\b"
    ),
    re.I,
)
REQ = re.compile(r"\b(required|minimum|at least|must have|requires?)\b", re.I)
PREF = re.compile(r"\b(preferred|ideally|nice.to.have|bonus|desired|ideally)\b", re.I)
HEAD_REQ = re.compile(
    (
        "^(?:minimum |basic |required "
        ")?(?:qualifications|requirements|what you "
        "bring|what you.ll bring|what you need|what we.re "
        "looking for|who you are|your background|skills and "
        "experience)\\s*[:\uff1a]?$"
    ),
    re.I,
)
HEAD_PREF = re.compile(r"^(?:preferred|desired|bonus|nice.to.have).*", re.I)


def _section_for_line(line, section):
    if HEAD_REQ.fullmatch(line):
        return "required"
    if HEAD_PREF.fullmatch(line) and len(line) < MAX_PREFERRED_HEADING_LENGTH:
        section = "preferred"
    if len(line) < MAX_OTHER_HEADING_LENGTH and re.match(
        (
            "^(benefits|about |our company|what we "
            "offer|responsibilities|compensation|equal "
            "opportunity)"
        ),
        line,
        re.I,
    ):
        section = "other"
    return section


def _experience_evidence(jd):
    evidence = []
    required = []
    uncertain = []
    section = "unknown"
    # Preserve full lines/clauses: no arbitrary nearby-word year matching.
    for raw in jd.splitlines():
        line = raw.strip(" \t•*-#")
        if not line:
            continue
        section = _section_for_line(line, section)
        if HEAD_REQ.fullmatch(line):
            continue
        matches = list(EXP.finditer(line))
        if not matches:
            continue
        evidence.append(line)
        # Alternatives, company history, preference and degree paths need semantics.
        if re.search(
            r"\b(company|founded|history|our team|we have|we bring)\b", line, re.I
        ):
            uncertain.append("experience_subject_unclear")
            continue
        if PREF.search(line) or section == "preferred":
            continue
        if (
            re.search(r"\b(or|bachelor|master|ph\.?d|equivalent)\b", line, re.I)
            or len(matches) > 1
        ):
            uncertain.append("alternative_or_degree_path")
            continue
        if REQ.search(line) or section == "required":
            required.append(int(matches[0].group(1)))
        else:
            uncertain.append("requirement_strength_unclear")
    return evidence, required, uncertain


def classify(job):
    title = job["title"]
    evidence, required, uncertain = _experience_evidence(job["jd_text"] or "")
    entry = bool(ENTRY.search(title))
    senior = bool(SENIOR.search(title))
    mid = bool(MID.search(title))
    level = (
        "junior" if entry else "senior" if senior else "mid" if mid else "unspecified"
    )
    decision = "uncertain"
    reason = "no_explicit_boundary"
    if entry and (senior or mid):
        level = "multiple"
        reason = "multiple_title_levels"
    elif entry and (any(x > MAX_ENTRY_YEARS for x in required) or uncertain):
        reason = "entry_title_needs_context"
    elif senior:
        decision = "block"
        reason = "explicit_senior_title"
    elif uncertain:
        reason = ";".join(sorted(set(uncertain)))
    elif required:
        decision = "block" if max(required) > MAX_ENTRY_YEARS else "keep"
        reason = "explicit_required_experience"
    elif entry:
        decision = "keep"
        reason = "explicit_entry_title"
    # Mid title alone is not enough to infer eligibility.
    return {
        "level": level,
        "decision": decision,
        "reason": reason,
        "required_years": required,
        "evidence": evidence,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    samplepath = OUT / "samples.json"
    if samplepath.exists():
        samples = json.loads(samplepath.read_text())
        meta = json.loads((OUT / "sampling.json").read_text())
    else:
        c = sqlite3.connect("file:data/jobfeed.sqlite?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        ids = [
            r[0]
            for r in c.execute(
                "SELECT id FROM jobs WHERE ml_gate_result='pass' AND "
                "ml_gate_version='v20260905T204440Z' ORDER BY id"
            )
        ]
        seed = random.SystemRandom().randrange(2**32)
        chosen = random.Random(seed).sample(ids, SAMPLE_SIZE)
        samples = [
            dict(
                c.execute(
                    (
                        "SELECT id,title,company,url,jd_text,jd_quality,ml_ga"
                        "te_result,ml_gate_version FROM jobs WHERE id=?"
                    ),
                    (i,),
                ).fetchone()
            )
            for i in chosen
        ]
        meta = {
            "seed": seed,
            "population": len(ids),
            "sample_size": SAMPLE_SIZE,
            ("unit"): (
                "job database row; no deduplication or "
                "location/closed/status restriction"
            ),
            ("selection"): (
                "ml_gate_result='pass' AND ml_gate_version='v20260905T204440Z'"
            ),
            "created_at": datetime.now(UTC).isoformat(),
        }
        samplepath.write_text(json.dumps(samples, ensure_ascii=False, indent=2))
        (OUT / "sampling.json").write_text(json.dumps(meta, indent=2))
    start = time.perf_counter()
    results = [dict(sample_id=i + 1, **j, **classify(j)) for i, j in enumerate(samples)]
    elapsed = time.perf_counter() - start
    assert (
        len(results) == SAMPLE_SIZE and len({r[("id")] for r in results}) == SAMPLE_SIZE
    )
    assert all(r["ml_gate_result"] == "pass" for r in results)
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    summary = dict(
        **meta,
        elapsed_seconds=elapsed,
        decisions=dict(collections.Counter(r["decision"] for r in results)),
        levels=dict(collections.Counter(r["level"] for r in results)),
        missing_jd=sum(not r["jd_text"] for r in results),
        api_calls=0,
        api_cost_usd=0,
    )
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    fields = [
        "sample_id",
        "id",
        "company",
        "title",
        "level",
        "decision",
        "reason",
        "required_years",
        "evidence",
        "url",
    ]
    with (OUT / "results.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    def e(value):
        return html.escape(str(value))

    cards = []
    for r in results:
        cards.append(
            (
                '<article data-decision="'
                f"{r['decision']}"
                '"><h3>#'
                f"{r['sample_id']}"
                " "
                f"{e(r['title'])}"
                "</h3><p>"
                f"{e(r['company'])}"
                " · ID "
                f"{r['id']}"
                " · <b>"
                f"{r['decision']}"
                " / "
                f"{r['level']}"
                "</b></p><p>"
                f"{e(r['reason'])}"
                "</p><ul>"
            )
            + "".join("<li>" + e(x) + "</li>" for x in r["evidence"])
            + (
                '</ul><a href="'
                f"{e(r['url'])}"
                '">原始岗位链接</a><details><summary>完整数据库 '
                "JD</summary><pre>"
                f"{e(r['jd_text'])}"
                "</pre></details></article>"
            )
        )
    (OUT / "index.html").write_text(
        (
            '<!doctype html><meta charset="utf-8"><title>Seniorit'
            "y 100 Demo</title><style>body{max-width:1000px;margi"
            "n:40px auto;font:16px system-ui;background:#f5f5f2;c"
            "olor:#222}article{background:white;padding:20px;marg"
            "in:18px 0;border-radius:12px}pre{white-space:pre-wra"
            "p}button{padding:10px;margin:5px}li{margin:12px "
            "0}</style><h1>100 个通过软件岗位筛选的随机样本</h1><p>零 "
            "LLM 调用。规则输出不等于人工确认的准确率\uff1buncertain "
            "默认保留。完整 JD 来自数据库快照\uff0c未实时核验招聘网站。"
            "</p><pre>"
        )
        + html.escape(json.dumps(summary, ensure_ascii=False, indent=2))
        + "</pre>"
        + "".join(
            (
                "<button onclick=\"document.querySelectorAll('article'"
                ").forEach(x=>x.hidden='"
                f"{d}"
                "'!=='all'&&x.dataset.decision!=='"
                f"{d}"
                "')\">"
                f"{d}"
                "</button>"
            )
            for d in ["all", "block", "keep", "uncertain"]
        )
        + "".join(cards)
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in results:
        print(
            json.dumps(
                {
                    k: r[k]
                    for k in [
                        "sample_id",
                        "id",
                        "title",
                        "decision",
                        "level",
                        "reason",
                        "evidence",
                    ]
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()

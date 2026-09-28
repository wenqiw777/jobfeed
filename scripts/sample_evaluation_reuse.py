"""Export a small read-only JD reuse casebook; never runs model evaluation."""

# Long bilingual report prose and source quotes are intentionally kept readable.
# ruff: noqa: E501, RUF001

import difflib
import json
import sqlite3
from pathlib import Path

from jobfeed.domain.display_content import same_display_content
from jobfeed.domain.models import JobPosting

CASES = [
    (
        "visa_new_grad_locations",
        [467688, 467691, 468228],
        "Candidate: share content-fit evaluation; retain three location rows and individual pay.",
        "Responsibilities and qualification requirements align. Salary and extraction punctuation differ. Austin's second salary occurrence has a missing upper bound.",
    ),
    (
        "visa_austin_sources",
        [468228, 472413],
        "Candidate: share evaluation and link sources to the same location posting after identity verification.",
        "Same company/location; full JD equality can be checked directly. Source title punctuation differs.",
    ),
    (
        "visa_intern_cohort",
        [472426, 472425],
        "Separate evaluations / eligibility decisions.",
        "Sophomore internship requires graduation December 2028–August 2029; regular internship December 2027–August 2028. Shared template must not erase this difference.",
    ),
    (
        "amazon_leo_locations",
        [465263, 465264, 465220],
        "Candidate: share content-fit evaluation, retain three location rows.",
        "Same role across Sunnyvale, Redmond, San Diego. Verify full JD equality and keep location-specific application metadata.",
    ),
    (
        "amazon_leo_different_roles",
        [475026, 472684],
        "Separate evaluations.",
        "Government software role versus senior developer-productivity/AI/test role; responsibilities and required experience differ (3+ versus 8+ years in basic qualifications).",
    ),
    (
        "tiktok_source_rendering",
        [473849, 473797],
        "Review: candidate reuse only after source identity and requirement coverage verification.",
        "Same title/location; one representation adds extracted skill lists, another benefits and company-wide H1B history. Company H1B history is not a role-specific sponsorship promise.",
    ),
    (
        "visa_research_specialty",
        [470651, 470653],
        "Separate evaluations.",
        "Agentic AI versus Foundational AI: overlapping research template but different duties and expertise. Location is not the only change.",
    ),
]


def read_posts():
    database = sqlite3.connect("file:data/jobfeed.sqlite?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    posts = {}
    for _, ids, _, _ in CASES:
        for identifier in ids:
            if identifier not in posts:
                posts[identifier] = dict(
                    database.execute(
                        "SELECT id, platform, canonical_id, external_identity, company, title, location, url, jd_text, jd_quality, discovered_at FROM jobs WHERE id=?",
                        (identifier,),
                    ).fetchone()
                )
    database.close()
    return posts


def main():
    output = Path("artifacts/evaluation-reuse-samples")
    output.mkdir(parents=True, exist_ok=True)
    posts = read_posts()
    report = [
        "# 真实 JD 评估复用样本",
        "",
        "数据库快照样本，非在线职位有效性验证。仅提取和比较；没有调用模型、合并记录或改写数据库。",
        "",
        "相似度是空白分词后 SequenceMatcher 的顺序匹配比例（autojunk=False），不是语义置信度，也不是生产复用阈值。建议标签是人工初判，不是已验证的自动分类结果。现有比较器用于展示去重，不等同于新的评估复用规则。薪资/地点必须保留到每条招聘记录；若评分使用这些字段，就不能直接复用整份评分。",
        "",
    ]
    results = []
    for name, ids, decision, evidence in CASES:
        report.extend(
            [f"## {name}", "", f"建议：{decision}", "", f"依据：{evidence}", ""]
        )
        for identifier in ids:
            post = posts[identifier]
            report.append(
                f"- ID {identifier} · {post['company']} · {post['title']} · {post['location']} · {post['platform']} · [来源链接]({post['url']})"
            )
        comparisons = []
        for identifier in ids[1:]:
            left, right = posts[ids[0]], posts[identifier]
            a, b = left["jd_text"].split(), right["jd_text"].split()
            matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)

            def posting(value):
                return JobPosting(
                    **{k: v for k, v in value.items() if k != "jd_quality"}
                    | {"id": str(value["id"])}
                )

            current = same_display_content(posting(left), posting(right))
            result = {
                "left_id": left["id"],
                "right_id": right["id"],
                "raw_equal": left["jd_text"] == right["jd_text"],
                "word_sequence_ratio": matcher.ratio(),
                "current_display_comparator": current,
            }
            comparisons.append(result)
            report.extend(
                [
                    "",
                    f"### {left['id']} vs {right['id']}",
                    "",
                    f"全文完全相同：{result['raw_equal']}；词序匹配：{matcher.ratio():.2%}；现有展示比较器：{current}",
                    "",
                    "差异片段（忽略空白布局；不是忽略薪资或要求）：",
                    "",
                ]
            )
            differences = []
            for tag, i, j, k, end in matcher.get_opcodes():
                if tag != "equal":
                    differences.append(
                        {"left": " ".join(a[i:j]), "right": " ".join(b[k:end])}
                    )
                    report.extend(
                        [
                            "```text",
                            f"- {' '.join(a[i:j])}",
                            f"+ {' '.join(b[k:end])}",
                            "```",
                            "",
                        ]
                    )
            result["differences"] = differences
        results.append(
            {
                "name": name,
                "ids": ids,
                "provisional_decision": decision,
                "evidence": evidence,
                "comparisons": comparisons,
            }
        )
    (output / "posts.json").write_text(
        json.dumps(list(posts.values()), ensure_ascii=False, indent=2)
    )
    (output / "comparisons.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2)
    )
    (output / "README.md").write_text("\n".join(report))
    print(f"Exported {len(posts)} source records, {len(results)} cases to {output}")
    for result in results:
        print(
            result["name"],
            [
                {k: v for k, v in p.items() if k != "differences"}
                for p in result["comparisons"]
            ],
        )


if __name__ == "__main__":
    main()

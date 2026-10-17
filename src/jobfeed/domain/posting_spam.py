"""Conservative, explainable posting screening; no inferred company reputation."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from jobfeed.domain.models import JobPosting

RULE_VERSION = 2
_MIN_PLACEHOLDERS = 2
PUBLISHER_DOMAINS = (
    "fetchjobs.co",
    "bestjobtool.com",
    "meeboss.com",
    "hirelayer.ai",
    "engradar.com",
)
_PLACEHOLDERS = re.compile(
    r"\[(?:job title|brief overview of[^\]]*|relevant skills[^\]]*|"
    r"specific programming[^\]]*|relevant industry[^\]]*)\]",
    re.I,
)
_ATTRIBUTION = re.compile(
    r"^(?:this (?:role|opportunity) is being recruited by [^.!?\n]{1,80}|"
    r"[^.!?\n]{1,60}? (?:is |are )?(?:sharing|recruiting|hiring))"
    r"[^.!?\n]{0,70}\bon behalf of\b[^.!?\n]{1,120}",
    re.I | re.M,
)
_NEGATION = re.compile(r"\b(?:not|never|don.t|do not|no longer)\b", re.I)
_AI = re.compile(
    r"\b(?:AI training|train and evaluate AI models|"
    r"AI model training|LLM evaluation)\b",
    re.I,
)
_GIG = re.compile(
    r"\b(?:task[- ]based|per[- ]task|project[- ]based role|hourly contract)\b", re.I
)
_LABOR = re.compile(
    r"\b(?:help train and evaluate AI models|"
    r"(?:review|evaluate|rate|rank|annotate|label|author)[^.!?\n]{0,100}"
    r"(?:model outputs|AI responses|training data|YAML|AI models|"
    r"datasets|code examples))\b",
    re.I,
)
_CONTRACT_EVALUATOR = re.compile(
    r"\bas (?:a|an) (?:software engineering |AI |model )evaluator\b",
    re.I,
)
_PLACEMENT = re.compile(
    r"\bpaid[^.!?\n]{0,35}training[^.!?\n]{0,100}"
    r"\b(?:then deploy|placement with|placed with|deploy straight to)"
    r"[^.!?\n]{0,65}\b(?:client|corporate partner)",
    re.I,
)
_AGENCY_RECRUITMENT = re.compile(
    r"^(?:we (?:are )?(?:recruiting|hiring|seeking|looking)"
    r"[^.!?\n]{0,100}\b(?:on behalf of|for (?:our|a|an) client)\b"
    r"[^.!?\n]{0,120}|our client is (?:seeking|looking for) "
    r"(?:a|an) [^.!?\n]{1,120})",
    re.I | re.M,
)
_INTRO = re.compile(
    r"about the company\s*\n+\s*(?:At )?(.{2,80}?) (?:is|are|has|offers|provides)\b",
    re.I,
)


def company_key(company: str) -> str:
    """Normalize full company names without losing punctuation.

    Args:
        company: Observed employer or publisher name.

    Returns:
        Casefolded name with collapsed whitespace.
    """
    return " ".join(company.casefold().split())


@dataclass(frozen=True)
class SpamAssessment:
    """One explainable rule result; not a reputation claim about an employer."""

    disposition: str = "clear"
    category: str = ""
    evidence: tuple[str, ...] = ()
    company_evidence: bool = False

    def to_dict(self) -> dict[str, object]:
        """Serialize explainable evidence for review.

        Returns:
            Stable JSON-compatible assessment fields.
        """
        return asdict(self)


def _publisher_url(url: str | None) -> str | None:
    try:
        parsed = urlsplit(url or "")
        host = (parsed.hostname or "").lower()
        if parsed.scheme in ("http", "https") and any(
            host == domain or host.endswith("." + domain)
            for domain in PUBLISHER_DOMAINS
        ):
            return url
    except ValueError:
        return None
    return None


def assess_posting(job: JobPosting) -> SpamAssessment:
    """Require combined evidence and retain ambiguous findings for review.

    Args:
        job: Stored public posting facts.

    Returns:
        Disposition, matched evidence, and company-promotion eligibility.
    """
    body = (job.jd_text or "")[:60000]
    placeholders = tuple(dict.fromkeys(m.group() for m in _PLACEHOLDERS.finditer(body)))
    unfinished_role = re.search(
        r"(?:join[^.!?\n]{0,60} as|team as a|role of|position of)\s*\[job title\]",
        body,
        re.I,
    )
    if len(placeholders) >= _MIN_PLACEHOLDERS and unfinished_role:
        return SpamAssessment("block", "unfinished_template", placeholders)
    attribution = next(
        (
            m.group()
            for m in _ATTRIBUTION.finditer(
                "\n".join(re.split(r"(?<=[.!?])\s+", body[:2500]))
            )
            if not _NEGATION.search(m.group())
        ),
        None,
    )
    intro = _INTRO.search(body[:1500])
    employer = company_key(job.company)
    introduced = company_key(intro[1]) if intro else ""
    mismatch = bool(
        introduced
        and introduced
        not in ("we", "who we", "our client", "our company", "our team", employer)
        and not introduced.startswith((employer + " ", employer + ","))
    )
    redirect = _publisher_url(job.apply_url)
    posting_attribution = (
        attribution
        if attribution
        and re.search(
            r"\bthis (?:active )?(?:role|opportunity|position|job)\b",
            attribution,
            re.I,
        )
        else None
    )
    self_description = (
        re.search(
            rf"\b(?:{re.escape(job.company)}|we) (?:is|are) (?:a|an) "
            r"(?:job board|job radar|job[- ]matching platform|job aggregator)\b",
            body[:3000],
            re.I,
        )
        if job.company.strip()
        else None
    )
    employer_statement = re.search(
        r"[^.!?\n]{1,80} is the employer\b", body[:2500], re.I
    )
    if self_description and (posting_attribution or employer_statement):
        return SpamAssessment(
            "block",
            "publisher_attribution",
            (
                self_description.group(),
                posting_attribution
                or (employer_statement[0] if employer_statement else ""),
            ),
            True,
        )
    if redirect and (posting_attribution or mismatch):
        return SpamAssessment(
            "block",
            "publisher_redirect",
            (posting_attribution or (intro[0] if intro else ""), redirect),
            True,
        )
    # A client introduced as the employer is explicit agency recruitment.
    # Ordinary customer mentions or company aliases are not recruitment evidence.
    agency = next(
        (
            m.group()
            for m in _AGENCY_RECRUITMENT.finditer(body[:2500])
            if not _NEGATION.search(m.group())
        ),
        None,
    )
    if (
        introduced == "our client"
        and intro
        and not _NEGATION.search(body[intro.end() :].split("\n", 1)[0][:120])
    ):
        agency = intro[0]
    if agency:
        return SpamAssessment("block", "agency_recruitment", (agency,))
    contributor = bool(_GIG.search(body) and _LABOR.search(body)) or bool(
        _CONTRACT_EVALUATOR.search(body)
        and re.search(r"\b(?:type|engagement)\s*:\s*contractor\b", body, re.I)
    )
    if _AI.search(body) and contributor:
        sentences = re.split(r"[\n.!?]+", body)
        evidence = tuple(
            s.strip()[:300]
            for s in sentences
            if _AI.search(s) or _GIG.search(s) or _CONTRACT_EVALUATOR.search(s)
        )[:4]
        if not any(_NEGATION.search(s) for s in evidence):
            return SpamAssessment("block", "ai_contributor_work", evidence)
    placement = _PLACEMENT.search(body)
    if placement and not _NEGATION.search(
        body[max(0, placement.start() - 100) : placement.end()]
    ):
        return SpamAssessment("block", "training_placement", (placement.group(),))
    if attribution or redirect or placeholders:
        evidence = tuple(
            x
            for x in (
                attribution,
                redirect,
                *placeholders,
            )
            if x
        )
        return SpamAssessment("review", "possible_publisher_or_template", evidence)
    return SpamAssessment()

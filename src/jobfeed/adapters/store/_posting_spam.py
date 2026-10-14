"""Transactional spam evidence and reversible decisions in existing state storage."""

from __future__ import annotations

import json
from typing import Any

from jobfeed.domain.intermediary import trusted_ats_url
from jobfeed.domain.models import JobPosting
from jobfeed.domain.posting_spam import RULE_VERSION, assess_posting, company_key

MIN_COMPANY_POSTINGS = 3


def spam_allowed_sql(*, postgres: bool = False, alias: str = "j") -> str:
    """Build the effective source exclusion predicate.

    Args:
        postgres: Select PostgreSQL JSON and normalization syntax.
        alias: SQL alias of the source jobs table.

    Returns:
        SQL expression accepting a source unless explicitly or automatically blocked.
    """
    company = (
        f"lower(regexp_replace(trim({alias}.company), '\\s+', ' ', 'g'))"
        if postgres
        else f"jobfeed_company_key({alias}.company)"
    )
    job_key = f"'spam:decision:' || CAST({alias}.id AS TEXT)"
    company_key_sql = f"'spam:company:' || {company}"
    assessment = (
        f"(SELECT value FROM state WHERE key='spam:job:' || CAST({alias}.id AS TEXT))"
    )
    disposition = (
        f"({assessment})::jsonb->>'disposition'"
        if postgres
        else f"json_extract({assessment},'$.disposition')"
    )
    decision = f"(SELECT value FROM state WHERE key={job_key})"
    employer = f"(SELECT value FROM state WHERE key={company_key_sql})"
    return (
        f"(CASE WHEN {employer}='block' THEN 0 "
        f"WHEN {decision}='allow' THEN 1 WHEN {decision}='block' THEN 0 "
        f"WHEN {employer}='allow' THEN 1 WHEN {employer}='auto_block' THEN 0 "
        f"WHEN {disposition}='block' THEN 0 ELSE 1 END)=1"
    )


class SpamDB:
    """Small state-storage adapter for the two existing transaction types."""

    def __init__(self, connection: Any, *, postgres: bool = False) -> None:
        self.connection = connection
        self.postgres = postgres

    def _sql(self, sql: str) -> str:
        if not self.postgres:
            return sql
        for index in range(1, sql.count("?") + 1):
            sql = sql.replace("?", f"${index}", 1)
        return sql

    async def rows(self, sql: str, *args: object) -> list[dict[str, Any]]:
        """Read rows from the caller-owned transaction.

        Args:
            sql: Internal query with positional placeholders.
            args: Bound query arguments.

        Returns:
            Rows keyed by database column name.
        """
        if self.postgres:
            return [dict(r) for r in await self.connection.fetch(self._sql(sql), *args)]
        cursor = await self.connection.execute(sql, args)
        try:
            columns = [d[0] for d in cursor.description]
            return [
                dict(zip(columns, row, strict=True)) for row in await cursor.fetchall()
            ]
        finally:
            await cursor.close()

    async def execute(self, sql: str, *args: object) -> None:
        """Write inside the caller-owned transaction.

        Args:
            sql: Internal statement with positional placeholders.
            args: Bound statement arguments.
        """
        if self.postgres:
            await self.connection.execute(self._sql(sql), *args)
        else:
            cursor = await self.connection.execute(sql, args)
            await cursor.close()

    async def get(self, key: str) -> str | None:
        """Read a single stored decision or assessment.

        Args:
            key: Exact state key.

        Returns:
            Stored text, or None for an unobserved key.
        """
        rows = await self.rows("SELECT value FROM state WHERE key=?", key)
        return str(rows[0]["value"]) if rows else None

    async def put(self, key: str, value: str) -> None:
        """Upsert a decision inside the caller transaction.

        Args:
            key: Exact state key.
            value: Serialized assessment or decision.
        """
        await self.execute(
            "INSERT INTO state(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            key,
            value,
        )

    async def allowed(self, source_id: str) -> bool:
        """Check source eligibility against transaction-consistent decisions.

        Args:
            source_id: Persisted decimal source ID.

        Returns:
            True if the source exists and is not excluded.
        """
        return bool(
            await self.rows(
                "SELECT j.id FROM jobs j WHERE j.id=? AND "
                + spam_allowed_sql(postgres=self.postgres),
                int(source_id),
            )
        )

    async def assess_source(self, source_id: int) -> None:
        """Assess persisted winning content and refresh company evidence.

        Args:
            source_id: Source ID whose stored content may have changed.
        """
        rows = await self.rows("SELECT * FROM jobs WHERE id=?", source_id)
        if not rows:
            return
        r = rows[0]
        # Classification needs only stored public posting facts.
        job = JobPosting(
            platform=r["platform"],
            canonical_id=r["canonical_id"],
            url=r["url"],
            title=r["title"],
            company=r["company"],
            location=r["location"],
            discovered_at=r["discovered_at"],
            jd_text=r["jd_text"],
            apply_url=r["apply_url"],
        )
        assessment = assess_posting(job)
        evidence = dict(
            assessment.to_dict(),
            rule_version=RULE_VERSION,
            job_id=str(source_id),
            company=job.company,
            company_key=company_key(job.company),
            title=job.title,
            url=job.url,
            apply_url=job.apply_url,
        )
        key = f"spam:job:{source_id}"
        prior = await self.get(key)
        if assessment.disposition == "clear" and prior is None:
            if (
                await self.get("spam:company:" + company_key(job.company))
                == "auto_block"
            ):
                await self.promote(job.company, r["company_norm"])
            return
        value = json.dumps(evidence, sort_keys=True)
        if prior != value:
            await self.put(key, value)
            if prior and json.loads(prior)["company_key"] != company_key(job.company):
                previous = json.loads(prior)
                await self.promote(previous["company"], None)
        await self.promote(job.company, r["company_norm"])

    async def promote(self, company: str, normalized: str | None) -> None:
        """Promote only unanimous independent publisher evidence.

        Args:
            company: Observed company display name.
            normalized: Stored company_norm used for indexed candidate lookup.
        """
        key = company_key(company)
        if key in ("", "unknown", "confidential"):
            return
        state_key = "spam:company:" + key
        if self.postgres:
            await self.execute(
                "INSERT INTO state(key,value) VALUES(?,'observe') "
                "ON CONFLICT(key) DO NOTHING",
                state_key,
            )
            await self.rows("SELECT value FROM state WHERE key=? FOR UPDATE", state_key)
        if await self.get(state_key) in ("allow", "block"):
            return
        where = (
            "j.company_norm=?"
            if normalized is not None
            else (
                "lower(regexp_replace(trim(j.company), '\\s+', ' ', 'g'))=?"
                if self.postgres
                else "jobfeed_company_key(j.company)=?"
            )
        )
        rows = await self.rows(
            "SELECT j.id,j.real_job_id,j.company,j.title_norm,j.url,"
            "s.value,d.value AS decision "
            "FROM jobs j LEFT JOIN state s ON s.key='spam:job:' || CAST(j.id AS TEXT) "
            "LEFT JOIN state d ON d.key='spam:decision:' || CAST(j.id AS TEXT) "
            "WHERE " + where,
            normalized if normalized is not None else key,
        )
        rows = [r for r in rows if company_key(r["company"]) == key]
        unanimous = bool(rows) and all(
            r["value"]
            and json.loads(r["value"]).get("company_evidence")
            and r["decision"] != "allow"
            and not trusted_ats_url(r["url"])
            for r in rows
        )
        independent = len(
            {r["real_job_id"] for r in rows if r["real_job_id"] is not None}
        )
        titles = len({r["title_norm"] for r in rows if r["title_norm"]})
        if unanimous and min(independent, titles) >= MIN_COMPANY_POSTINGS:
            await self.put(state_key, "auto_block")
        elif await self.get(state_key) == "auto_block":
            await self.execute("DELETE FROM state WHERE key=?", state_key)

    async def review(self, status: str, limit: int, offset: int) -> dict[str, object]:
        """List retained findings with effective decisions and original evidence.

        Args:
            status: Pending, blocked, reviewed, or all.
            limit: Maximum returned findings.
            offset: Number of matching findings to skip.

        Returns:
            Matching total and paginated evidence records.
        """
        # State has one small record per assessed source. This is a user-opened
        # review view, not the scoring/Results hot path.
        rows = await self.rows("SELECT value FROM state WHERE key LIKE 'spam:job:%'")
        decisions = {
            r["key"]: r["value"]
            for r in await self.rows(
                "SELECT key,value FROM state WHERE key LIKE 'spam:decision:%' "
                "OR key LIKE 'spam:company:%'"
            )
        }
        findings = []
        for row in rows:
            item = json.loads(row["value"])
            jd = decisions.get("spam:decision:" + item["job_id"])
            cd = decisions.get("spam:company:" + item["company_key"])
            if cd == "observe":
                cd = None
            if item["disposition"] == "clear" and not jd and not cd:
                continue
            item.update(job_decision=jd, company_decision=cd)
            effective = (
                "block"
                if cd == "block"
                else jd
                or ("block" if cd == "auto_block" else cd)
                or item["disposition"]
            )
            item["effective_disposition"] = effective
            item["review_status"] = (
                "reviewed"
                if jd or cd in ("allow", "block")
                else ("blocked" if effective == "block" else "pending")
            )
            if status == "all" or item["review_status"] == status:
                findings.append(item)
        findings.sort(key=lambda x: int(x["job_id"]), reverse=True)
        return {"total": len(findings), "items": findings[offset : offset + limit]}

    async def decide(self, job_id: int, scope: str, decision: str) -> None:
        """Apply a reversible human decision without deleting history.

        Args:
            job_id: Source anchoring the decision.
            scope: Job or exact company name.
            decision: Allow, block, or reset.

        Raises:
            ValueError: Missing source or unnamed company scope.
        """
        rows = await self.rows(
            "SELECT company,company_norm FROM jobs WHERE id=?", job_id
        )
        if not rows:
            raise ValueError("Job not found")
        row = rows[0]
        key = (
            "spam:decision:" + str(job_id)
            if scope == "job"
            else "spam:company:" + company_key(row["company"])
        )
        if scope == "company" and company_key(row["company"]) in (
            "",
            "unknown",
            "confidential",
        ):
            raise ValueError("Cannot apply a company decision without a named employer")
        if decision == "reset":
            await self.execute("DELETE FROM state WHERE key=?", key)
        else:
            await self.put(key, decision)
        await self.assess_source(job_id)
        await self.promote(row["company"], row["company_norm"])

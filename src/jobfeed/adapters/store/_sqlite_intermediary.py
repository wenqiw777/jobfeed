"""Indexed candidate lookup without reading the full jobs corpus."""

import aiosqlite

from jobfeed.adapters.store._sqlite_values import _job_from_row
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company


class SqliteIntermediary:
    """Bounded lookup reused by internal and external resolution."""

    _lifecycle: SqliteLifecycle

    async def official_candidates(self, title: str) -> list[tuple[str, JobPosting]]:
        """Find title candidates using the dedicated title/id index.

        Args:
            title: Posting title.

        Returns:
            Up to 201 sources with canonical IDs; overflow is rejected by caller.
        """
        async with self._lifecycle.connection() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM jobs WHERE title_norm=? "
                "AND jobfeed_trusted_ats_url(url)=1 "
                "AND jobfeed_intermediary(company,url,apply_url)=0 "
                "ORDER BY id LIMIT 201",
                (normalize(title),),
            )
            return [
                (str(row["real_job_id"]), _job_from_row(row))
                for row in await cursor.fetchall()
            ]

    async def employer_ats_urls(self, company: str) -> list[str]:
        """Reuse observed employer ATS routes through the existing company index.

        Args:
            company: Employer name extracted from public search evidence.

        Returns:
            Up to 50 observed source URLs; callers validate supported ATS hosts.
        """
        async with self._lifecycle.connection() as connection:
            cursor = await connection.execute(
                "SELECT url FROM jobs WHERE company_norm=? ORDER BY id DESC LIMIT 50",
                (normalize_company(company),),
            )
            return [str(row[0]) for row in await cursor.fetchall()]

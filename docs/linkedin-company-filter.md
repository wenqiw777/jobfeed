# LinkedIn intermediary company filter

Requested: exclude Jobright.ai (spoken as Jobwright), Yara AI, and Dice from future LinkedIn ingestion.

Scope: exact normalized company-name matching (case and whitespace), including Jobright, Jobwright, and Jobs via Dice aliases. No title/JD substring matching. Other sources and historical stored jobs remain unchanged. Local SQLite readback found 406 historical LinkedIn-family listings across the three publishers.

Implementation: the extension discovery gate skips blocked IDs before detail retrieval and cache reuse; stored-company fallback and successful/partial result filtering prevent re-entry. Guest discovery skips before quota accounting; browser discovery skips before clicking a card and continues beyond all-filtered pages.

Verification: regression checks failed before implementation (9 extension failures and 2 fallback-source failures), then 60 relevant source tests passed. Ruff passed on changed files. Idle local API restarted with its existing environment to load the rule; health readback confirmed database and Redis healthy. No live scan was triggered.

Limit: when a search card has no company and there is no cached company, the detail request is necessary to learn the employer; the returned job is then filtered before ingestion. Historical rows are retained; this change does not retroactively hide them.

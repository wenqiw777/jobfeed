# Phase 3: First Real LLM (MockSource + Codex/Claude CLI) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace MockLLM with two real subprocess-based LLM adapters (CodexCliLLM + ClaudeCliLLM), migrate production prompt templates from legacy, wire up per-call LLM usage tracking, and add budget gates + concurrency to EvaluateService. Source remains MockSource (or Phase 2's ATSSource). This phase proves the LLMClient Protocol holds when a mock is replaced with real subprocess IO.

**Architecture:** Same hexagonal structure. Phase 3 introduces two new `LLMClient` adapters (subprocess-based) behind the existing Protocol. The `LLMClient.complete(request)` method shape stays unchanged, but this phase deliberately adds a prompt-rendering adapter and a `StoreOpsMixin.record_llm_usage` method. EvaluateService gains budget gates, retry logic, dual stage clients, and a no-timeout Stage B sweep client.

**Tech Stack additions:** jinja2 (prompt template rendering — legacy templates use Jinja2 for BOTH Stage A and Stage B, including `{% include %}` directives). Jinja2 is adapter-layer only; `domain/` remains stdlib-only.

**Spec reference:** `docs/specs/2026-05-20-jobfeed-rewrite-design.md` (Section 5: LLM Abstraction Layer)

**Legacy behavioral reference:** `/Users/wenqiwang/wwq/job-apply/src/jobfeed/evaluator/runner.py` (subprocess adapters), `/Users/wenqiwang/wwq/job-apply/src/jobfeed/templates/` (prompt templates), `/Users/wenqiwang/wwq/job-apply/src/jobfeed/main.py` (`evaluate_cmd` orchestration)

**Plan path:** `docs/plans/2026-05-23-jobfeed-rewrite-phase3-first-real-llm.md`

**Prerequisite:** Phase 2 substantially complete. Required: PostgresStore with full company + job CRUD, `record_cost` / `get_cost`, config infrastructure. MockSource or ATSSource functional for scan.

**New variables in this phase:** 1 (real subprocess LLM calls via `codex exec` and `claude -p`). Everything else uses existing proven infrastructure.

**Implementation repo:** `/Users/wenqiwang/wwq/jobfeed`. Do not implement Phase 3 tasks in the legacy repo.

**Precedence:** This phase plan is the source of truth when it conflicts with the architecture spec.

**Commit strategy:** Commit each task separately with a task-sized conventional commit. Run `make quality` before each task is marked complete; do not weaken a gate to make the task pass.

**Execution mode:** Run Phase 3 tasks sequentially.

---

## Implementation Decisions

**Decision 1: Both CLI adapters ship together.**
CodexCliLLM and ClaudeCliLLM share the same pattern (subprocess → parse output → return LLMResponse). Building both validates the LLMClient Protocol handles diverse backends. Total adapter code ~200-300 lines combined.

**Decision 2: Anthropic SDK adapter deferred to Phase 4.**
SDK adapter introduces HTTP client management, prompt caching API, streaming, and API key handling — a separate variable. Phase 3 proves the LLMClient abstraction with subprocess adapters first.

**Decision 3: Provider routing via `backend/model` config format.**
```toml
[llm]
stage_a = "codex-cli/gpt-5.4-mini"
stage_b = "codex-cli/gpt-5.5"
```
Alternative backends: `"claude-cli/claude-haiku-4-5"`, `"claude-cli/claude-sonnet-4-6"`, `"mock/stage-a"`. Factory parses prefix, builds corresponding adapter.

**Decision 4: Default models.**

| Stage | codex-cli | claude-cli |
|-------|-----------|------------|
| A | gpt-5.4-mini | claude-haiku-4-5 |
| B | gpt-5.5 | claude-sonnet-4-6 |

**Decision 5: Timeout per backend, not per stage.**
Based on P99 latency analysis of legacy data (2000+ Stage A, 700+ Stage B evaluations):

| Backend | P99 observed | Timeout |
|---------|-------------|---------|
| codex-cli | 28s (Stage B) | 60s |
| claude-cli | 128s (Stage B) | 210s |

**Decision 6: Retry ownership mirrors legacy, with explicit store retry caps.**
- Infrastructure failure (subprocess crash, timeout, OOM): Stage A retries transient subprocess failures once. Stage B concurrent calls do not same-window retry timeouts; those jobs are collected for the post-pool sweep.
- LLM response failure (`ScoringParseError` / schema parse failure): Stage A retries once immediately. Stage B retries parse/schema failure once in the concurrent attempt; jobs still failing after that are collected for the post-pool sweep.
- Stage B sweep: after the concurrent batch completes, every Stage B job that did not complete (timeout, subprocess error, or parse/schema error after its in-attempt retry) is retried once sequentially with a **dedicated sweep adapter** with unbounded timeout (see Decision 12). This matches legacy behavior.
- Store retry caps still apply: error rows are retried only while the current store pending-query semantics include them (Phase 1 cap: `MAX_STAGE_RETRIES`).

**Decision 7: Dual budget gate (call count + dollar).**
- `max_daily_score_calls` (default 150): hard limit, prevents runaway loops
- `max_daily_cost_usd` (default 10.0): soft limit, cost awareness
- Both checked before each LLM call. First to trip stops evaluation gracefully.
- Call count is primary (works for all backends). Dollar is secondary (Codex cost is estimated from price table, Claude cost is exact from envelope).
- **Race condition:** under concurrency, up to `max_concurrent` extra calls may occur past the limit (not `max_concurrent - 1`). All N workers can read the same call count simultaneously, all pass, all call LLM. This is acceptable — the gate prevents runaway loops, not exact budget enforcement.

**Decision 8: LLM price table vendored from LiteLLM.**
LiteLLM maintains `model_prices_and_context_window.json` (community-updated, 1-3 day lag on price changes). Vendored into repo as `src/jobfeed/adapters/llm/model_prices.json`. Codex adapter reads this for cost estimation. Claude adapter ignores it (uses `total_cost_usd` from JSON envelope). `make update-prices` is a manual network helper and is not part of CI or `make quality`.

**Decision 9: Codex uses `--json` mode for structured event stream.**
Legacy uses `-o tempfile` for output. Phase 3 uses `codex exec --json` which outputs JSONL events to stdout: `item.completed` carries response text, `turn.completed` carries usage (input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens). This is cleaner and gives usage data without a second parse step. **Important:** `item.completed` events have an inner `item.type` field — only `item.type == "agent_message"` carries the LLM response text. Other item types (e.g., tool calls) must be skipped.

**Decision 10: Prompt templates migrated from legacy AS-IS; rendering lives outside domain.**
- `stage_a_prompt.md`, `stage_b_prompt.md`, and `_shared_preamble.md` copied from legacy `src/jobfeed/templates/` **without modification**. These prompts are proven to produce good LLM output — do not rewrite them.
- **Both Stage A and Stage B use Jinja2** — Stage A has `{% include "_shared_preamble.md" %}`, Stage B has `{% for b in blocks %}` conditional block selection. Both must be rendered through a Jinja2 Environment, NOT plain `Path.read_text()`.
- Stage B block variable names are full English names: `("verdict", "jd_summary", "fit_analysis", "resume_hooks")`, NOT single letters `("a", "b", "c", "e")`. These match legacy `blocks.py` `DEFAULT_BLOCKS`.
- `preamble_personal.md` support included (appended to rendered system prompt, included in prompt_hash). In legacy this path is hardcoded to `~/.jobfeed/preamble_personal.md`; Phase 3 makes it configurable via `preamble_personal_path` config key (new improvement over legacy).
- `score_rubric.md` NOT migrated (legacy new evaluator doesn't use it — only the old `match.py` path did)
- Jinja2 imports live in a concrete prompt-rendering adapter. `domain/scoring.py` remains stdlib-only and owns parsing / normalization only.

**Decision 11: Parser mapping layer bridges legacy template output → Phase 0 domain model.**
The legacy template tells the LLM to output a schema that differs from Phase 0's simplified domain model. Templates are NOT modified (Decision 10). Instead, `parse_stage_b_response` adds a mapping layer:

| Legacy LLM output | Phase 0 domain model | Parser mapping |
|---|---|---|
| Top-level keys `verdict`, `jd_summary`, `fit_analysis`, `resume_hooks` | `block_a`, `block_b`, `block_c`, `block_e` in raw_blocks | Parser reads legacy keys, stores in `raw_blocks` with legacy names |
| `verdict.recommendation` (`apply\|consider\|skip`) | `StageBResult.verdict` (Verdict enum) | Extract `recommendation` field → map to `Verdict` |
| `verdict.one_line` | `StageAResult.one_line` (Stage A only) | Not used in Stage B — ignored |
| `jd_summary` (structured: `role_in_3_lines`, `must_haves`, `nice_to_haves`, `red_flags_in_jd`) | `StageBResult.jd_summary: str` | Concatenate into readable string: `"{role_in_3_lines}\n\nMust-haves: {must_haves}\nNice-to-haves: {nice_to_haves}\nRed flags: {red_flags_in_jd}"` |
| `fit_analysis.gaps[].severity`: `blocker\|notable\|minor` | `GapItem.severity`: `critical\|major\|minor` | Map `blocker→critical`, `notable→major`, `minor→minor` |
| `resume_hooks`: `{lead_with: str, supporting: list[str], avoid_mentioning: list[str]}` | `StageBResult.resume_hooks: list[str]` | Flatten: `[lead_with] + supporting` (drop `avoid_mentioning` from flat list, but preserve full object in `raw_blocks`) |
| `timing_eligible`: `eligible\|mismatch\|unclear` | `StageAResult.timing_eligible: str` | Store as-is (legacy vocabulary). Update any downstream code that checks for Phase 0 vocabulary (`maybe`, `ineligible`) to also handle legacy vocabulary (`mismatch`, `unclear`) |

**MockLLM must also be updated** to output the legacy schema (not Phase 0's `block_a`/`block_b` format) so that mock tests exercise the same parser path as real LLM calls.

**Decision 11a: Legacy hard filter / ML gate parity is explicit non-scope.**
Legacy `evaluate` runs hard filters and an ML gate before Stage A. Phase 3 keeps the real-LLM call surface bounded with `--corpus`, `--max-days`, `--limit`, the short-JD guard, and budget gates, but does not implement full hard-filter / ML-gate parity. That parity must be handled by the ML-gate phase before broad real-LLM daily use.

**Decision 12: Post-pool sweep uses a dedicated adapter with unbounded timeout.**
The `LLMClient.complete()` Protocol takes only `LLMRequest` — timeout is not a per-call parameter (it is a transport concern, not a request concern). For the Stage B failure sweep, the `EvaluateService` constructor takes an additional optional `llm_stage_b_sweep: LLMClient | None = None`. The CLI layer builds this adapter via the same factory but with the backend timeout overridden to `None` (unbounded). If `llm_stage_b_sweep` is None (e.g., mock path), non-completed Stage B jobs are written as errors without a sweep. This keeps the Protocol clean and explicit.

**Decision 13: Jinja2 rendering lives in adapters behind a prompt-rendering port.**
`domain/` must be pure stdlib only (enforced by architecture boundary tests), and `services/` must remain adapter-free. Add a small `PromptRenderer` Protocol in `ports/prompts.py`. The Jinja2 implementation lives in `adapters/llm/_prompts.py`; `EvaluateService` depends only on the `PromptRenderer` port. Pure stdlib functions stay in `domain/scoring.py`: `compute_prompt_hash`, `compute_resume_hash`, `render_user_message`, `parse_stage_a_response`, `parse_stage_b_response`.

**Decision 14: `save_stage_b()` / `_stage_b_from_record()` updated for legacy key names.**
The Phase 0 PostgresStore persists Stage B JSONB columns using `block_a`/`block_b`/`block_c`/`block_e` key lookups from `raw_blocks`. After the parser rewrite (Decision 11), `raw_blocks` uses legacy keys (`verdict`/`jd_summary`/`fit_analysis`/`resume_hooks`) with legacy internal structure (`evidence_from_resume` instead of `evidence`, `lead_with`/`supporting` instead of `hooks`). `save_stage_b()` and `_stage_b_from_record()` in `postgres.py` must be updated in the same task as the parser rewrite (Task 1) to avoid silent data loss.

**Decision 15: Docker-first runtime remains the acceptance boundary.**
`./bin/jobfeed ...` is still the production-parity CLI. Host-native `jobfeed ...`, `python -m jobfeed ...`, or `uv run jobfeed ...` may be used only for developer debugging and live smoke diagnosis. Phase 3 must either install/configure the `codex` and `claude` subprocess toolchain in the `jobfeed-cli` container, or fail real-backend `./bin/jobfeed evaluate` with a clear configuration error. Milestone acceptance cannot rely on host-native real LLM calls alone. If the implementation chooses not to modify Dockerfile/docker-compose in this phase, Task 10 remains blocked until the Docker runtime is configured externally and verified through `./bin/jobfeed`.

---

## Subprocess Adapter Reference

### CodexCliLLM

**Command construction:**
```bash
codex exec --json --ephemeral --ignore-user-config --ignore-rules \
  --sandbox read-only -m <model> -
```
Prompt passed via **stdin**: `<system>\n{system_prompt}\n</system>\n\n{user_prompt}`

**TTY detach:** `start_new_session=True` in `asyncio.create_subprocess_exec` (legacy fix commit 819a2c9 — prevents codex progress bar noise from corrupting stdout).

**Response parsing (JSONL events):**
- `{"type": "item.completed", "item": {"type": "agent_message", "text": "..."}}` → response content (filter on `item.type == "agent_message"` — other item types like tool calls must be skipped)
- `{"type": "turn.completed", "usage": {...}}` → token usage
- `{"type": "error", "message": "..."}` → error

**Usage fields available:**
- `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`
- `cost_usd`: NOT available — computed from price table

### ClaudeCliLLM

**Command construction:**
```bash
claude -p --no-session-persistence --tools "" --output-format json \
  --model <model> --system-prompt <system_prompt> <user_prompt>
```
Prompt passed as **positional argument** (not stdin). **ARG_MAX note:** very long JD text (10K+ chars) combined with a long resume could approach OS `ARG_MAX` limits (~256KB on macOS). In practice, typical prompts are 5-20KB total which is safe. If ARG_MAX becomes an issue, fall back to piping the user prompt via stdin with `-` as the positional arg.

**Response parsing (single JSON object):**
```json
{
  "result": "<model response text>",
  "total_cost_usd": 0.114,
  "duration_ms": 2275,
  "duration_api_ms": 2092,
  "usage": {
    "input_tokens": 9,
    "output_tokens": 65,
    "cache_creation_input_tokens": 91506,
    "cache_read_input_tokens": 0
  },
  "modelUsage": {
    "<model>": {"inputTokens": ..., "outputTokens": ..., "costUSD": ...}
  }
}
```

**Usage fields available:**
- `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`
- `cost_usd`: directly from `total_cost_usd`
- `latency_ms`: directly from `duration_api_ms`
- `model`: from `modelUsage` keys

---

## File Map

```
jobfeed/                              # repo root (/Users/wenqiwang/wwq/jobfeed)
├── src/jobfeed/
│   ├── adapters/llm/
│   │   ├── mock.py                   # EXISTING — MODIFY (update Stage B output to legacy schema)
│   │   ├── codex.py                  # CREATE — CodexCliLLM
│   │   ├── claude.py                 # CREATE — ClaudeCliLLM
│   │   ├── _factory.py              # CREATE — parse "backend/model" → build adapter
│   │   ├── _prompts.py              # CREATE — Jinja2 prompt rendering (NOT in domain/)
│   │   ├── _pricing.py              # CREATE — vendored price table loading + cost estimate
│   │   ├── _subprocess.py           # CREATE — shared subprocess helpers
│   │   └── model_prices.json        # CREATE — vendored LiteLLM price table
│   │
│   ├── domain/
│   │   ├── scoring.py               # MODIFY — pure parsing/hashing only (no Jinja2)
│   │   └── models_llm.py            # MODIFY — add latency_ms to LLMResponse, add fields to LLMUsage
│   │
│   ├── templates/                    # CREATE — directory
│   │   ├── stage_a_prompt.md         # CREATE — migrated from legacy (Jinja2)
│   │   ├── stage_b_prompt.md         # CREATE — migrated from legacy (Jinja2)
│   │   └── _shared_preamble.md       # CREATE — migrated from legacy (included by BOTH Stage A and Stage B)
│   │
│   ├── ports/
│   │   ├── prompts.py               # CREATE — PromptRenderer protocol
│   │   └── store_ops.py             # MODIFY — add record_llm_usage method
│   │
│   ├── adapters/store/
│   │   └── postgres.py              # MODIFY — update save_stage_b/read key mapping (Task 1) + record_llm_usage (Task 7)
│   │
│   ├── services/
│   │   └── evaluate.py              # MODIFY — concurrency, budget gate, retry, usage recording
│   │
│   ├── cli/
│   │   ├── __init__.py              # MODIFY — remove EvaluateService from create_app (built lazily)
│   │   └── evaluate.py              # MODIFY — lazy LLM/service construction, add bounded evaluate flags
│   │
│   └── config.py                    # MODIFY — add LLM config section
│
├── migrations/versions/
│   └── 0003_llm_usage.py            # CREATE — llm_usage table
│
├── config.example.toml              # MODIFY — add [llm] section
├── pyproject.toml                   # MODIFY — add jinja2
├── Makefile                         # MODIFY — add update-prices target
│
├── tests/
│   ├── unit/
│   │   ├── test_scoring.py          # MODIFY — update for new signatures and legacy schema (Task 1)
│   │   ├── test_mock_adapters.py    # MODIFY — update for legacy schema (Task 1)
│   │   ├── test_pricing.py          # CREATE — price table load + estimate (Task 2)
│   │   ├── test_subprocess_helpers.py # CREATE — subprocess helper behavior (Task 3)
│   │   ├── test_llm_codex.py        # CREATE — mock subprocess (Task 4)
│   │   ├── test_llm_claude.py       # CREATE — mock subprocess (Task 5)
│   │   ├── test_llm_factory.py      # CREATE — provider routing (Task 6)
│   │   ├── test_scoring_production.py # CREATE — production prompt render/parse (Task 1)
│   │   └── test_budget_gate.py      # CREATE — budget gate logic (Task 8)
│   ├── integration/
│   │   ├── test_services.py         # MODIFY — update EvaluateService constructor (Task 8)
│   │   └── test_evaluate_chain.py   # CREATE — real PG + mock subprocess (Task 8)
│   ├── contract/
│   │   ├── test_store_contract.py   # MODIFY — update Stage B raw_blocks keys (Task 1)
│   │   └── test_llm_usage_contract.py # CREATE — usage metrics schema (Task 7)
│   └── live/
│       └── test_llm_live_smoke.py   # CREATE — @pytest.mark.live, real subprocess (Task 11)
```

---

## Task 0: Config Expansion + Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/jobfeed/config.py`
- Modify: `config.example.toml`
- Test: `tests/unit/test_config.py` (expand)

**What to build:**

**pyproject.toml:** Add runtime dependency: `jinja2 >= 3.1`.

**config.py:** Expand the existing `LLMSettings` model (currently has `stage_a`, `stage_b`, `max_concurrent`, `timeout_s`). The class name stays `LLMSettings` — do NOT rename to `LLMConfig`. Add new fields, add per-backend timeouts. **Keep `timeout_s` as deprecated** — it is still read by `evaluate.py` until Task 8 removes the service-level timeout. Removing it here would break the build.

```python
class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_a: str = "codex-cli/gpt-5.4-mini"
    stage_b: str = "codex-cli/gpt-5.5"
    timeout_s: float = 60.0          # DEPRECATED — used by evaluate.py until Task 8; removed in Task 8
    codex_timeout_s: float = 60.0
    claude_timeout_s: float = 210.0
    max_concurrent: int = 4
    master_resume_path: str = ".jobfeed-dev/resume.md"
    preamble_personal_path: str | None = None
    max_daily_score_calls: int = 150
    max_daily_cost_usd: float = 10.0
```

**Migration note:** `max_daily_score_calls` currently lives on `ScoringSettings` in the repo. Move it to `LLMSettings` — it controls LLM call budget, not scoring thresholds. Remove it from `ScoringSettings`. No existing code references `settings.scoring.max_daily_score_calls` at runtime (the field was defined but never consumed), so no call sites need updating — only the config model. **Do NOT remove `timeout_s` yet** — `evaluate.py:192` reads `self.settings.llm.timeout_s` in `_complete_with_timeout()` and `test_services.py:482` constructs `LLMSettings(timeout_s=0.001)`. These references are cleaned up in Task 8.

**config.example.toml:**
```toml
[llm]
stage_a = "codex-cli/gpt-5.4-mini"      # or "claude-cli/claude-haiku-4-5" or "mock/stage-a"
stage_b = "codex-cli/gpt-5.5"            # or "claude-cli/claude-sonnet-4-6" or "mock/stage-b"
codex_timeout_s = 60
claude_timeout_s = 210
max_daily_score_calls = 150
max_daily_cost_usd = 10.0
max_concurrent = 4
master_resume_path = ".jobfeed-dev/resume.md"
# preamble_personal_path = ".jobfeed-dev/preamble_personal.md"
```

**Acceptance criteria:**
- [ ] `jinja2` installable
- [ ] `LLMSettings` model added with `ConfigDict(extra="forbid")`
- [ ] `load_settings()` parses `[llm]` section with all defaults
- [ ] Provider routing format `backend/model` validated (must contain `/`)
- [ ] `mock/` prefix still works for Phase 0 compatibility
- [ ] `make quality` passes before commit

---

## Task 1: Prompt Template Migration

**Files:**
- Create: `src/jobfeed/ports/prompts.py`
- Create: `src/jobfeed/adapters/llm/_prompts.py`
- Create: `src/jobfeed/templates/stage_a_prompt.md`
- Create: `src/jobfeed/templates/stage_b_prompt.md`
- Create: `src/jobfeed/templates/_shared_preamble.md`
- Modify: `src/jobfeed/domain/scoring.py`
- Modify: `src/jobfeed/adapters/llm/mock.py`
- Modify: `src/jobfeed/adapters/store/postgres.py` (update `save_stage_b` / `_stage_b_from_record` key mapping)
- Modify: `tests/unit/test_scoring.py` (update for new signatures and legacy schema)
- Modify: `tests/unit/test_mock_adapters.py` (update for legacy schema)
- Modify: `tests/contract/test_store_contract.py` (update `_make_stage_b_raw()` keys)
- Create: `tests/unit/test_scoring_production.py`

**What to build:**

**Prompt templates:** Copy from legacy `/Users/wenqiwang/wwq/job-apply/src/jobfeed/templates/`. **Both Stage A and Stage B are Jinja2 templates:**
- `stage_a_prompt.md` — contains `{% include "_shared_preamble.md" %}` directive. Must be rendered through a Jinja2 Environment with a loader pointing to the templates directory, NOT read as plain text.
- `stage_b_prompt.md` — contains `{% for b in blocks %}` and `{% if "verdict" in blocks %}` conditionals. Block variable names are full English: `("verdict", "jd_summary", "fit_analysis", "resume_hooks")` matching legacy `blocks.py` `DEFAULT_BLOCKS`. **Also includes `_shared_preamble.md`** via `{% include %}` at line 1 (same as Stage A).
- `_shared_preamble.md` — included by **both** Stage A and Stage B templates.

**Architecture constraint:** `domain/` must be pure stdlib only (enforced by `test_domain_scoring_has_no_external_imports`) and `services/` must stay adapter-free. Jinja2-dependent rendering goes in `adapters/llm/_prompts.py`, behind a `ports/prompts.py` Protocol, NOT in `domain/scoring.py` or `services/evaluate.py` (see Decision 13). Keep the existing stdlib-only prompt helpers used by the current EvaluateService until Task 8 swaps the service to `PromptRenderer`; Task 1 must not make the repo unimportable between commits.

**`ports/prompts.py`** (NEW — service-facing prompt rendering port):
```python
@dataclass(frozen=True, kw_only=True)
class PromptBundle:
    """Rendered LLM request messages plus stable hashes."""

    messages: list[Message]
    prompt_hash: str
    resume_hash: str


@runtime_checkable
class PromptRenderer(Protocol):
    """Render stage prompts without exposing template implementation details."""

    def render_stage_a(self, *, resume_text: str, job: JobPosting) -> PromptBundle: ...
    def render_stage_b(self, *, resume_text: str, job: JobPosting) -> PromptBundle: ...
```

**`adapters/llm/_prompts.py`** (NEW — Jinja2-dependent prompt rendering):

`render_system_prompt(template_name: str, templates_dir: Path, *, preamble_path: Path | None = None, blocks: tuple[str, ...] | None = None) -> str`:
- Create `jinja2.Environment(loader=FileSystemLoader(templates_dir))`
- Load template by name, render with `blocks=blocks` if provided
- If preamble_path exists and is a file: append `"\n\n" + preamble_content`
- Return rendered system prompt

`render_stage_a_prompt(resume_text: str, job: JobPosting, templates_dir: Path, preamble_path: Path | None) -> tuple[list[Message], str, str]`:
- Calls `render_system_prompt("stage_a_prompt.md", templates_dir, preamble_path=preamble_path)` (no blocks arg — Stage A template doesn't use blocks)
- Calls `render_user_message(resume_text, job)` and `compute_prompt_hash` / `compute_resume_hash` from `domain/scoring.py`
- Returns: (messages, prompt_hash, resume_hash)
- messages = [Message(role="system", content=system_prompt), Message(role="user", content=user_message)]

`render_stage_b_prompt(resume_text: str, job: JobPosting, templates_dir: Path, preamble_path: Path | None) -> tuple[list[Message], str, str]`:
- Calls `render_system_prompt("stage_b_prompt.md", templates_dir, preamble_path=preamble_path, blocks=("verdict", "jd_summary", "fit_analysis", "resume_hooks"))`
- Same return pattern
- Export `JinjaPromptRenderer`, a concrete implementation of `PromptRenderer`, whose `render_stage_a()` / `render_stage_b()` methods return `PromptBundle`.

**`domain/scoring.py` migration** (pure stdlib functions only):

Keep the Phase 0 `render_stage_a_prompt` and `render_stage_b_prompt` functions temporarily so the current `EvaluateService` remains importable until Task 8. Add the production stdlib helpers and parser changes here, then delete the old render helpers in Task 8 when the service switches to `PromptRenderer`. If `domain/scoring.py` would exceed the 300-line gate, split pure helpers into `domain/scoring_prompts.py` / `domain/scoring_parse.py` and re-export from `domain/scoring.py`; do not delete useful validation just to satisfy the gate.

`render_user_message(resume_text: str, job: JobPosting) -> str`:
- Format: `# Master Resume\n\n{resume_text}\n\n---\n\n# Job Posting\n\n**Title:** {title}\n**Company:** {company}\n**Location:** {location}\n**Platform:** {platform}\n\n## JD Text\n\n{jd_text}`

`compute_prompt_hash(system_prompt: str) -> str`:
- `hashlib.sha256(system_prompt.encode()).hexdigest()`
- Includes preamble_personal content (changing preamble changes hash → enables re-evaluation)

`compute_resume_hash(resume_text: str) -> str`:
- `hashlib.sha256(resume_text.encode()).hexdigest()`

`parse_stage_a_response`:
- Keep Phase 0 parsing logic (markdown-wrapped JSON, missing keys, out-of-range scores)
- `timing_eligible` stored as-is from LLM response — legacy uses `eligible|mismatch|unclear`, Phase 0 used `eligible|maybe|ineligible`. Accept both vocabularies; update any downstream code that checks specific values.
- Add **new safety checks** (these are improvements over legacy, which has no refusal/truncation detection):
  - **Refusal detection:** response starts with "I cannot", "I'm sorry", "I apologize", or contains "as an AI" → raise `ScoringParseError("LLM refusal")`
  - **Truncated response:** JSON decode fails and response length > 90% of `max_tokens` → raise `ScoringParseError("response likely truncated")`
  - **Token limit exceeded:** check if response ends mid-JSON (unbalanced braces) → raise `ScoringParseError("incomplete JSON — possible token limit")`

`parse_stage_b_response` — **REWRITE required (Phase 0 parser is incompatible with legacy templates):**

Phase 0's parser reads `block_a`/`block_b`/`block_c`/`block_e` top-level keys. Legacy templates make the LLM output `verdict`/`jd_summary`/`fit_analysis`/`resume_hooks` top-level keys with different internal field names and structures. The parser must be rewritten to implement the mapping layer from Decision 11:

```python
def parse_stage_b_response(raw: str, model: str, prompt_hash: str, resume_hash: str, cost_usd: float | None = None) -> StageBResult:
    parsed = _parse_json(raw)  # handles markdown-wrapped, truncated, refusal

    # 1. Verdict: legacy key "verdict", field "recommendation"
    verdict_block = _require_object(parsed, "verdict")
    verdict_str = _require_string(verdict_block, "recommendation")  # NOT "verdict"
    verdict = Verdict(verdict_str)

    # 2. JD Summary: legacy key "jd_summary", structured → concatenate to string
    jd_block = _require_object(parsed, "jd_summary")
    jd_summary = _flatten_jd_summary(jd_block)
    # produces: "{role_in_3_lines}\n\nMust-haves: ...\nNice-to-haves: ...\nRed flags: ..."

    # 3. Fit Analysis: legacy key "fit_analysis", severity mapping
    fit_block = _require_object(parsed, "fit_analysis")
    score = _require_int(fit_block, "score_0_100", min_val=0, max_val=100)
    strengths = [MatchItem(requirement=s["requirement"], evidence=s["evidence_from_resume"])
                 for s in _require_list(fit_block, "strong_match")]
    # NOTE: legacy template outputs "evidence_from_resume", not "evidence".
    # Parser maps it to MatchItem.evidence.
    gaps = [GapItem(requirement=g["requirement"],
                    severity=_MAP_SEVERITY.get(g["severity"], g["severity"]),
                    mitigation=g.get("mitigation") or "")
            for g in _require_list(fit_block, "gaps")]
    # NOTE: legacy template allows "mitigation": null for uncoverable gaps.
    # GapItem.mitigation is str (non-optional), so coalesce None → "".
    # _MAP_SEVERITY = {"blocker": "critical", "notable": "major", "minor": "minor"}

    # 4. Resume Hooks: legacy key "resume_hooks", structured → flatten
    hooks_block = _require_object(parsed, "resume_hooks")
    resume_hooks = [hooks_block["lead_with"]] + hooks_block.get("supporting", [])
    # avoid_mentioning preserved in raw_blocks but NOT in flat resume_hooks list

    # 5. raw_blocks: store entire parsed dict with legacy key names for audit
    return StageBResult(
        verdict=verdict, jd_summary=jd_summary,
        fit_analysis=FitAnalysis(score=score, strengths=strengths, gaps=gaps),
        resume_hooks=resume_hooks, model=model, cost_usd=cost_usd,
        prompt_hash=prompt_hash, resume_hash=resume_hash,
        raw_blocks=parsed,  # legacy keys preserved for audit/snapshot
    )
```

**MockLLM update:** `MockLLM._stage_b_content()` must also output the legacy schema (top-level keys `verdict`/`jd_summary`/`fit_analysis`/`resume_hooks` with legacy field names) so mock tests exercise the same parser path as real calls. Phase 0's `block_a`/`block_b`/`block_c`/`block_e` format is retired.

**Service call-site migration is deferred to Task 8.**
Task 1 creates the production prompt-rendering port and adapter, but the existing `EvaluateService` still uses its stdlib skeleton prompt helpers until Task 8 changes the constructor to accept a `PromptRenderer`. Do not import `adapters.llm._prompts` from `services/evaluate.py`.

**PostgresStore `save_stage_b()` / `_stage_b_from_record()` update (Decision 14):**
The Phase 0 store extracts JSONB columns from `raw_blocks` using `_block_json(result.raw_blocks, "block_a")` etc. After this task, `raw_blocks` uses legacy keys. Update:

| DB column | Phase 0 key | New key |
|-----------|-------------|---------|
| `stage_b_verdict_json` | `block_a` | `verdict` |
| `stage_b_summary_json` | `block_b` | `jd_summary` |
| `stage_b_fit_json` | `block_c` | `fit_analysis` |
| `stage_b_hooks_json` | `block_e` | `resume_hooks` |

Also update `_stage_b_from_record()` which reads the JSONB columns back. The internal JSON structure changes:
- `fit_analysis.strong_match[].evidence_from_resume` (was `block_c.strong_match[].evidence`)
- `resume_hooks.lead_with` + `resume_hooks.supporting` (was `block_e.hooks[]`)
- `fit_analysis.gaps[].mitigation` can be `null` → coalesce to `""`
- `fit_analysis.gaps[].severity` uses legacy vocabulary (`blocker`/`notable`/`minor`) in the raw JSON; map to domain vocabulary (`critical`/`major`/`minor`) on read

**Existing test cascade (all must be updated in this task for `make quality` to pass):**
- `tests/unit/test_scoring.py`: `make_stage_b_payload()` uses `block_a`/`block_b`/`block_c`/`block_e` → update to legacy keys. `test_render_stage_b_prompt_declares_canonical_json_shape` asserts `"block_a.verdict" in content` → rewrite for Jinja2 output. `test_parse_stage_b_response_rejects_missing_required_blocks` parametrizes Phase 0 keys → update. Remove old `render_stage_a_prompt`/`render_stage_b_prompt` tests (functions moved to `_prompts.py`).
- `tests/unit/test_mock_adapters.py`: `test_mock_llm_stage_b_response_matches_parser_contract` → update for legacy schema.
- `tests/contract/test_store_contract.py`: `_make_stage_b_raw()` uses Phase 0 keys → update to legacy keys.
- Any `test_store_pg_behaviors.py` references to Phase 0 block keys → update.

**Acceptance criteria:**
- [ ] Template files committed (stage_a, stage_b, _shared_preamble) — copied from legacy AS-IS
- [ ] `_prompts.py` created in `adapters/llm/` (Jinja2 rendering — NOT in domain/)
- [ ] `render_stage_a_prompt` renders Stage A via Jinja2 Environment (not plain read)
- [ ] `render_stage_b_prompt` passes `blocks=("verdict", "jd_summary", "fit_analysis", "resume_hooks")` to Jinja2
- [ ] `compute_prompt_hash` changes when preamble changes (stays in `domain/scoring.py`, stdlib only)
- [ ] `compute_resume_hash` is deterministic (stays in `domain/scoring.py`, stdlib only)
- [ ] `domain/scoring.py` has zero Jinja2 imports (architecture boundary test passes)
- [ ] `parse_stage_a_response` handles: valid JSON, markdown-wrapped, truncated, refusal, out-of-range
- [ ] `parse_stage_a_response` accepts both `timing_eligible` vocabularies (legacy + Phase 0)
- [ ] `parse_stage_b_response` reads legacy top-level keys (`verdict`, `jd_summary`, `fit_analysis`, `resume_hooks`)
- [ ] `parse_stage_b_response` maps `verdict.recommendation` → `Verdict` enum
- [ ] `parse_stage_b_response` flattens structured `jd_summary` → single string
- [ ] `parse_stage_b_response` maps gap severity `blocker→critical`, `notable→major`
- [ ] `parse_stage_b_response` flattens structured `resume_hooks` → `list[str]` (lead_with + supporting)
- [ ] `parse_stage_b_response` preserves full legacy JSON in `raw_blocks` for audit
- [ ] MockLLM updated to output legacy schema (not Phase 0 `block_a`/`block_b` format)
- [ ] `save_stage_b()` in `postgres.py` updated: `block_a`→`verdict`, `block_b`→`jd_summary`, `block_c`→`fit_analysis`, `block_e`→`resume_hooks`
- [ ] `_stage_b_from_record()` in `postgres.py` updated for legacy internal field names
- [ ] No `services/` module imports `adapters.llm._prompts`
- [ ] `test_scoring.py` updated for legacy keys while keeping current service import compatibility
- [ ] `test_mock_adapters.py` updated for legacy schema
- [ ] `test_store_contract.py` `_make_stage_b_raw()` updated for legacy keys
- [ ] `make quality` passes after this task (codebase remains importable)
- [ ] `make quality` passes before commit

---

## Task 2: LLM Price Table

**Files:**
- Create: `src/jobfeed/adapters/llm/model_prices.json`
- Create: `src/jobfeed/adapters/llm/_pricing.py`
- Modify: `Makefile`
- Test: `tests/unit/test_pricing.py`

**What to build:**

**`model_prices.json`:** Vendor from LiteLLM's `model_prices_and_context_window.json`. Download and commit a snapshot. Only need OpenAI models for Phase 3 (Codex backend). File is ~200KB.

**`_pricing.py`:**
```python
def load_price_table(path: Path | None = None) -> dict[str, ModelPricing]:
    """Load vendored price table. Returns model → pricing mapping."""

@dataclass
class ModelPricing:
    input_cost_per_token: float
    output_cost_per_token: float
    cached_input_cost_per_token: float | None = None
    reasoning_output_cost_per_token: float | None = None

def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    *,
    price_table: dict[str, ModelPricing],
) -> float:
    """Estimate USD cost from token counts. Returns 0.0 if model not in table.
    Logs a warning if the model is not found (prevents silent zero-cost accounting)."""
```

**Makefile:** Add `update-prices` target. This is a manual network-refresh helper, not part of `make quality` or CI.
```makefile
update-prices:
	curl -sL https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json \
	  -o src/jobfeed/adapters/llm/model_prices.json
```

**Acceptance criteria:**
- [ ] `model_prices.json` committed with OpenAI model data
- [ ] `estimate_cost("gpt-5.4-mini", 1000, 100, price_table=table)` returns sensible USD value
- [ ] Unknown model returns 0.0 (no crash)
- [ ] `make update-prices` downloads fresh file when run manually with network access
- [ ] `make quality` passes before commit

---

## Task 3: Shared Subprocess Helpers + LLMResponse.latency_ms

**Files:**
- Create: `src/jobfeed/adapters/llm/_subprocess.py`
- Modify: `src/jobfeed/domain/models_llm.py` (add `latency_ms: int = 0` to `LLMResponse`)
- Test: `tests/unit/test_subprocess_helpers.py`

**What to build:**

**`models_llm.py` update:** Add `latency_ms: int = 0` to `LLMResponse` — currently `LLMResponse` has no latency field. ClaudeCliLLM gets `duration_api_ms` from the envelope; CodexCliLLM uses `SubprocessResult.elapsed_ms`. Without this field on `LLMResponse`, latency cannot propagate from adapter to `LLMUsage`. Added here (before Tasks 4-5) so the adapters can populate it when they are built.

**Shared async subprocess execution for both CLI adapters:**

```python
@dataclass(frozen=True, kw_only=True)
class SubprocessOptions:
    """Transport options for one subprocess invocation."""

    input_text: str | None = None
    timeout_s: float | None = None
    start_new_session: bool = False


@dataclass(frozen=True, kw_only=True)
class SubprocessResult:
    stdout: str
    stderr: str
    returncode: int
    elapsed_ms: int


async def run_subprocess(
    cmd: list[str],
    *,
    options: SubprocessOptions,
    logger: JobfeedLogger,
) -> SubprocessResult:
    """Run a subprocess with optional timeout. Returns stdout/stderr/returncode.

    When timeout_s is None, waits indefinitely (used by post-pool serial sweep).
    When timeout_s is a float, kills the process after that many seconds.

    Raises:
    - SubprocessTimeout: process killed after timeout_s (only when timeout_s is not None)
    - SubprocessError: non-zero exit code
    """

class SubprocessTimeout(Exception):
    """Subprocess exceeded timeout and was killed."""

class SubprocessError(Exception):
    """Subprocess exited with non-zero code."""
    def __init__(self, returncode: int, stderr: str) -> None: ...
```

Implementation:
- `asyncio.create_subprocess_exec(*cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE)`
- pass `start_new_session=options.start_new_session` through to `create_subprocess_exec` (Codex sets it true for TTY detach)
- `process.communicate(options.input_text.encode() if options.input_text is not None else None)` wrapped in `asyncio.wait_for(..., timeout=options.timeout_s)`
- Decode stdout/stderr bytes to text before returning `SubprocessResult`
- On timeout: `process.kill()`, `await process.wait()`, raise `SubprocessTimeout`
- Elapsed time measured via `time.monotonic()`
- Logs: command (redacted — no prompt content), elapsed_ms, returncode

**Infrastructure retry wrapper:**
```python
@dataclass(frozen=True, kw_only=True)
class RetryOptions:
    """Retry settings for subprocess infrastructure failures."""

    max_retries: int = 2
    retry_delay_s: float = 5.0


async def run_with_retry(
    cmd: list[str],
    *,
    options: SubprocessOptions,
    retry: RetryOptions = RetryOptions(),
    logger: JobfeedLogger,
) -> SubprocessResult:
    """Run subprocess with retry on infrastructure failure.

    Retries on: SubprocessTimeout, SubprocessError.
    Does NOT retry on success (even if content is unparseable — that's the caller's problem).
    """
```

**Argument-count constraint:** Keep helper signatures within the hard max of 5 arguments. If implementation needs more knobs, add a dataclass options object rather than more keyword parameters.

**Acceptance criteria:**
- [ ] `run_subprocess` returns stdout/stderr/returncode on success
- [ ] `run_subprocess` raises `SubprocessTimeout` after timeout
- [ ] `run_subprocess` raises `SubprocessError` on non-zero exit
- [ ] `run_with_retry` retries on timeout, succeeds on second attempt
- [ ] `run_with_retry` gives up after max_retries
- [ ] `start_new_session=True` passed through (TTY detach)
- [ ] Elapsed time measured correctly
- [ ] `LLMResponse.latency_ms: int = 0` added (default 0, backward-compatible)
- [ ] Tests mock `asyncio.create_subprocess_exec`
- [ ] All committed

---

## Task 4: CodexCliLLM Adapter

**Files:**
- Create: `src/jobfeed/adapters/llm/codex.py`
- Test: `tests/unit/test_llm_codex.py`

**What to build:**

```python
class CodexCliLLM:
    """LLMClient adapter for Codex CLI (codex exec).

    Uses --json mode for structured JSONL event stream.
    Prompt passed via stdin. TTY detached via start_new_session.
    """

    def __init__(
        self,
        *,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        price_table: dict[str, ModelPricing],
        logger: JobfeedLogger,
    ) -> None: ...

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
```

**Command construction:**
```python
cmd = [
    "codex", "exec",
    "--json",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--sandbox", "read-only",
    "-m", self.model,
    "-",  # read prompt from stdin
]
```

**Prompt composition for stdin:**
```python
input_text = f"<system>\n{request.messages[0].content}\n</system>\n\n{request.messages[1].content}"
```

**Response parsing:** Parse JSONL stdout line by line:
- Find `item.completed` event where `item.type == "agent_message"` → extract `item.text` as response content. **Skip** `item.completed` events with other `item.type` values (e.g., tool calls).
- Find `turn.completed` event → extract `usage` dict
- If `error` event found → raise a dedicated `CodexApiError` (NOT `SubprocessError` — the subprocess itself succeeded with exit code 0; see note below)
- If no `item.completed` with `item.type == "agent_message"` found → raise with descriptive error

**Codex API error handling note:** When Codex outputs an `error` event (e.g., rate limit), the subprocess exits with code 0 and `run_with_retry` sees success. The adapter must detect the error event during JSONL parsing and raise `CodexApiError(message)`. This error should be treated as retriable infrastructure failure — the adapter's `complete()` method should have its own retry loop around the full subprocess + parse cycle for this case, separate from `run_with_retry`'s subprocess-level retry.

**LLMResponse construction:**
```python
LLMResponse(
    content=item_text,
    model=self.model,
    input_tokens=usage["input_tokens"],
    output_tokens=usage["output_tokens"],
    cost_usd=estimate_cost(
        self.model, usage["input_tokens"], usage["output_tokens"],
        cached_input_tokens=usage.get("cached_input_tokens", 0),
        reasoning_output_tokens=usage.get("reasoning_output_tokens", 0),
        price_table=self.price_table,
    ),
    cached=usage.get("cached_input_tokens", 0) > 0,
    latency_ms=result.elapsed_ms,
)
```

**`start_new_session=True`** passed to `run_with_retry` (TTY detach fix).

**Acceptance criteria:**
- [ ] `CodexCliLLM` satisfies `LLMClient` Protocol (`isinstance` check)
- [ ] `complete()` constructs correct command with `--json` and model flag
- [ ] Prompt passed via stdin with `<system>` wrapper
- [ ] JSONL events parsed: `item.completed` with `item.type == "agent_message"` → content, `turn.completed` → usage
- [ ] Non-`agent_message` `item.completed` events are skipped
- [ ] Cost estimated from price table (includes `reasoning_output_tokens`)
- [ ] `latency_ms` set from `SubprocessResult.elapsed_ms`
- [ ] `start_new_session=True` set (TTY detach)
- [ ] Infrastructure retry on subprocess timeout/crash
- [ ] Codex API error event → `CodexApiError` raised, retried
- [ ] Tests mock subprocess (no real codex calls)
- [ ] All committed

---

## Task 5: ClaudeCliLLM Adapter

**Files:**
- Create: `src/jobfeed/adapters/llm/claude.py`
- Test: `tests/unit/test_llm_claude.py`

**What to build:**

```python
class ClaudeCliLLM:
    """LLMClient adapter for Claude Code CLI (claude -p).

    Uses --output-format json for structured response with usage metadata.
    Prompt passed as positional argument.
    """

    def __init__(
        self,
        *,
        model: str,
        timeout_s: float = 210.0,
        max_retries: int = 2,
        logger: JobfeedLogger,
    ) -> None: ...

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
```

**Command construction:**
```python
cmd = [
    "claude", "-p",
    "--no-session-persistence",
    "--tools", "",
    "--output-format", "json",
    "--model", self.model,
    "--system-prompt", request.messages[0].content,
    request.messages[1].content,  # user prompt as positional arg
]
```

**Response parsing:** Single JSON object from stdout:
- `envelope["result"]` → response content
- `envelope["total_cost_usd"]` → cost (exact, no price table needed)
- `envelope["duration_api_ms"]` → latency
- `envelope["usage"]` → token counts
- `envelope["modelUsage"]` → per-model breakdown

**LLMResponse construction:**
```python
LLMResponse(
    content=envelope["result"],
    model=self.model,
    input_tokens=usage["input_tokens"],
    output_tokens=usage["output_tokens"],
    cost_usd=envelope["total_cost_usd"],
    cached=(usage.get("cache_read_input_tokens", 0) > 0),
    latency_ms=envelope.get("duration_api_ms", 0),
)
```

No `start_new_session` needed (Claude CLI doesn't have TTY noise issues).

**Acceptance criteria:**
- [ ] `ClaudeCliLLM` satisfies `LLMClient` Protocol (`isinstance` check)
- [ ] `complete()` constructs correct command with `--output-format json`
- [ ] System prompt via `--system-prompt`, user prompt as positional arg
- [ ] JSON envelope parsed: `result` → content, `total_cost_usd` → cost, `usage` → tokens
- [ ] `duration_api_ms` captured as `LLMResponse.latency_ms`
- [ ] Infrastructure retry on subprocess timeout/crash
- [ ] Tests mock subprocess (no real claude calls)
- [ ] All committed

---

## Task 6: LLM Factory (Provider Routing)

**Files:**
- Create: `src/jobfeed/adapters/llm/_factory.py`
- Test: `tests/unit/test_llm_factory.py`

**What to build:**

```python
def build_llm_client(
    spec: str,
    *,
    settings: LLMSettings,
    price_table: dict[str, ModelPricing],
    logger: JobfeedLogger,
) -> LLMClient:
    """Parse 'backend/model' spec and build the corresponding adapter.

    Supported backends:
    - 'codex-cli' → CodexCliLLM
    - 'claude-cli' → ClaudeCliLLM
    - 'mock' → MockLLM

    Raises ValueError on unknown backend or missing '/' separator.
    """
```

Parse logic:
- Split on first `/` → `(backend, model_name)`
- `codex-cli` → `CodexCliLLM(model=model_name, timeout_s=settings.codex_timeout_s, ...)`
- `claude-cli` → `ClaudeCliLLM(model=model_name, timeout_s=settings.claude_timeout_s, ...)`
- `mock` → `MockLLM()` (Phase 0 compatibility)

Used by the CLI `evaluate` command handler to build per-stage LLM clients lazily (NOT in `create_app()` — see Task 9):
```python
llm_stage_a = build_llm_client(settings.llm.stage_a, settings=settings.llm, ...)
llm_stage_b = build_llm_client(settings.llm.stage_b, settings=settings.llm, ...)
```

**Runtime preflight:** For real subprocess backends, the factory or adapter constructor must check the selected executable with `shutil.which()` before returning the client:
- `codex-cli` requires `codex`
- `claude-cli` requires `claude`

If the executable is missing, raise a dedicated `LLMRuntimeUnavailable` / `click.ClickException` path with an actionable message that includes the selected backend, missing binary, and the fact that `./bin/jobfeed` runs inside the Docker `jobfeed-cli` runtime. This check must happen lazily in the evaluate command path, never in `create_app()`, so `scan`, `digest`, and `migrate` remain usable without LLM tools.

**Acceptance criteria:**
- [ ] `build_llm_client("codex-cli/gpt-5.4-mini", ...)` returns CodexCliLLM
- [ ] `build_llm_client("claude-cli/claude-haiku-4-5", ...)` returns ClaudeCliLLM
- [ ] `build_llm_client("mock/stage-a", ...)` returns MockLLM
- [ ] `build_llm_client("unknown/model", ...)` raises ValueError
- [ ] `build_llm_client("no-slash", ...)` raises ValueError
- [ ] Missing `codex`/`claude` executable for a selected real backend raises a clear lazy runtime error; `create_app()` and non-evaluate commands do not check it
- [ ] All committed

---

## Task 7: LLM Usage Store Method + Alembic Migration

**Files:**
- Modify: `src/jobfeed/domain/models_llm.py`
- Modify: `src/jobfeed/ports/store_ops.py`
- Modify: `src/jobfeed/adapters/store/postgres.py`
- Create: `migrations/versions/0003_llm_usage.py`
- Test: `tests/contract/test_llm_usage_contract.py`

**What to build:**

**Domain model expansion (`models_llm.py`):**
The existing `LLMUsage` dataclass has: `model`, `input_tokens`, `output_tokens`, `cost_usd`, `cached`, `latency_ms`, `timestamp`. Add three new optional fields:
- `job_id: str | None = None` — which job this call was for
- `stage: str | None = None` — `"a"` or `"b"`
- `run_id: str | None = None` — pipeline run ID

**Note:** `latency_ms: int = 0` was already added to `LLMResponse` in Task 3 (moved there so Tasks 4-5 adapters can populate it).

**Protocol addition (`store_ops.py`):**
```python
async def record_llm_usage(self, usage: LLMUsage) -> None:
    """Record a single LLM call's usage metrics."""
```

**FK type note:** The SQL migration uses `job_id INTEGER REFERENCES jobs(id)` because PostgreSQL `jobs.id` is `SERIAL` (integer). The Python `LLMUsage.job_id` is `str | None` because domain models use string IDs. The PostgresStore adapter converts `str → int` at the boundary (same pattern as other FK columns).

**Alembic migration (`0003_llm_usage.py`):**
```sql
CREATE TABLE llm_usage (
    id SERIAL PRIMARY KEY,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    cached BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    job_id INTEGER REFERENCES jobs(id),
    stage TEXT,
    run_id TEXT
);
CREATE INDEX idx_llm_usage_timestamp ON llm_usage(timestamp);
CREATE INDEX idx_llm_usage_run ON llm_usage(run_id);
```

**PostgresStore implementation:** INSERT into `llm_usage`.

**Contract test:** Record usage → query back → verify all fields preserved.

**Acceptance criteria:**
- [ ] `record_llm_usage` added to `StoreOpsMixin` Protocol
- [ ] Alembic migration creates `llm_usage` table
- [ ] `alembic upgrade head` applies cleanly
- [ ] `alembic downgrade -1` reverses cleanly
- [ ] PostgresStore implements `record_llm_usage`
- [ ] Contract test: record with `job_id` set → read back → fields match (including FK)
- [ ] Contract test: record with `job_id=None` → inserts successfully with NULL FK (column is intentionally nullable)
- [ ] All committed

---

## Task 8: EvaluateService Enhancement

**Files:**
- Modify: `src/jobfeed/services/evaluate.py`
- Modify: `src/jobfeed/config.py` (remove deprecated `timeout_s` from `LLMSettings`)
- Modify: `tests/integration/test_services.py` (update all 11 EvaluateService constructor call sites)
- Create: `tests/integration/test_evaluate_chain.py`
- Create: `tests/unit/test_budget_gate.py`

**What to build:**

Major expansion of Phase 0's EvaluateService. The existing service already has `asyncio.Semaphore` concurrency and `asyncio.gather` (Phase 0 implemented basic concurrency). Phase 3 expands it with dual LLM clients, budget gates, retry sweep, and usage recording.

**Config cleanup (deferred from Task 0):** Remove the deprecated `timeout_s` field from `LLMSettings` and delete `_complete_with_timeout()` from `evaluate.py`. Update `test_services.py:test_evaluate_service_persists_llm_timeout_and_continues` — this test is architecturally invalid after timeout moves to adapter-internal; redesign or remove it.

**Constructor change (BREAKING — cascades to `cli/evaluate.py` and all tests):**

The existing constructor is `__init__(self, store, llm, settings, logger)` with a single `llm: LLMClient` and `settings: Settings`. Phase 3 changes this to:

```python
@dataclass(frozen=True, kw_only=True)
class EvaluateDependencies:
    """Ports used by EvaluateService."""

    store: JobStore
    store_ops: StoreOpsMixin
    prompt_renderer: PromptRenderer
    llm_stage_a: LLMClient
    llm_stage_b: LLMClient
    llm_stage_b_sweep: LLMClient | None = None


@dataclass(frozen=True, kw_only=True)
class EvaluateRuntimeConfig:
    """Runtime knobs used by EvaluateService."""

    llm: LLMSettings
    stage_a_threshold: int
    resume_text: str


class EvaluateService:
    def __init__(
        self,
        *,
        deps: EvaluateDependencies,
        config: EvaluateRuntimeConfig,
        logger: JobfeedLogger,
    ) -> None: ...
```

Key differences from Phase 0:
- `llm` → `llm_stage_a` + `llm_stage_b` (dual LLM clients)
- `llm_stage_b_sweep` — optional dedicated adapter with unbounded timeout for post-pool sweep (Decision 12). When None (e.g., mock path), non-completed Stage B jobs are written as errors without a sweep.
- `PromptRenderer` port renders Stage A/B prompts; service never imports the Jinja2 adapter.
- `settings: Settings` → `EvaluateRuntimeConfig(llm=LLMSettings, stage_a_threshold=int, resume_text=str)`
- `store_ops: StoreOpsMixin` — for `record_cost`, `record_llm_usage`, `get_cost`
- `logger: JobfeedLogger` — uses the protocol, not concrete `structlog.BoundLogger`
- Argument-count gate: constructor has three keyword arguments (`deps`, `config`, `logger`) rather than a long parameter list.

**Cascade updates required:**
- `cli/__init__.py` `create_app()`: **remove** `EvaluateService` construction (it is now built lazily in the `evaluate` command handler — see Task 9). Remove `evaluate_service` from `AppContext`.
- All existing tests that instantiate `EvaluateService` (11 call sites in `test_services.py`) → update constructor args. Tests that use `CountingLLM`, `BadStageALLM`, `SlowLLM`, `TrackingLLM` must pass them as both `llm_stage_a` and `llm_stage_b`.
- The service-level `asyncio.wait_for` timeout is **removed** — timeouts are now adapter-internal. The `timeout_s` field is removed from `LLMSettings` in this task. Tests that relied on `LLMSettings(timeout_s=0.001)` must be redesigned to mock adapter-level timeout behavior instead.

**`async def run(*, stage: str = "both", corpus: str = "unrated", limit: int | None = None, max_days: int | None = None, dry_run: bool = False) -> PipelineRun`:**

Note: the existing `run()` only accepts `dry_run: bool`. Phase 3 adds `stage`, `corpus`, `limit`, and `max_days` so the real-LLM call surface can be bounded like legacy evaluate.

1. Generate `run_id`, create PipelineRun with `source="evaluate"`
2. `resume_text` already available via constructor (read by CLI layer)
3. Compute `resume_hash`

**Stage A phase (skip if `stage == "b"`):**
4. Load pending Stage A jobs: `store.load_pending_stage_a(quality_bands=frozenset({"full", "good"}), corpus=corpus, limit=limit or 100, max_days=max_days)`. This preserves legacy's quality gate and freshness/corpus controls before real LLM calls.
5. Budget gate check: `store_ops.get_cost(today)` → compare against `max_daily_score_calls` and `max_daily_cost_usd`. If either exceeded, log and stop.
6. Concurrent execution: expand existing `asyncio.Semaphore(settings.max_concurrent)` + `asyncio.gather`
7. Per job:
   - Budget gate check (before each call — concurrent workers check independently). **Budget gate is best-effort under concurrency: up to `max_concurrent` extra calls may occur past the limit** (all N workers can read the same call count simultaneously). This is acceptable — the gate prevents runaway loops, not exact budget enforcement.
   - If `len(job.jd_text or "") < 200`, save retryable Stage A error `jd_text_too_short: ...`, increment `errors`, and do not call LLM or record usage/cost.
   - Render Stage A prompt via `self.deps.prompt_renderer.render_stage_a(...)` → `PromptBundle(messages, prompt_hash, resume_hash)`
   - `await llm_stage_a.complete(LLMRequest(messages=messages, model=..., ...))`
   - Immediately after a successful `complete()` return, call `store_ops.record_cost(...)` and `store_ops.record_llm_usage(...)` before parsing. Parse errors are still billable LLM calls.
   - Parse response → `StageAResult` (with model, prompt_hash, resume_hash, cost_usd + latency_ms from LLMResponse)
   - On success: `store.save_stage_a(job.id, result)`, increment `stage_a_scored`
   - On `ScoringParseError`: retry the LLM call once; if the second parse fails, `store.save_stage_a_error(job.id, str(error))`, increment `errors`
8. Threshold check: for each Stage A success, if `score < self.config.stage_a_threshold` → `store.mark_stage_b_skipped(job.id)`

**Stage B phase (skip if `stage == "a"`):**
9. Load pending Stage B jobs: `store.load_pending_stage_b(limit=limit or 100, max_days=max_days)` — note this method does NOT take a threshold parameter. The store returns jobs where `stage_a_status='completed'` and Stage B is pending or retryable error, subject to the store retry cap. Threshold filtering was already applied in step 8 via `mark_stage_b_skipped`.
10. Same concurrent execution pattern as Stage A
11. Per job: render Stage B prompt via `PromptRenderer` → call `llm_stage_b` → record usage/cost after `complete()` returns → parse → save. Parse/schema failures retry once in the concurrent attempt.
12. **Post-pool failure sweep (Decision 12):** collect every Stage B job that did not complete in the concurrent phase (timeout, subprocess error, or parse/schema error after in-attempt retry). After `asyncio.gather` completes, retry them sequentially using `self.deps.llm_stage_b_sweep` (a dedicated adapter instance with unbounded timeout). If `llm_stage_b_sweep is None` (mock path), write failed jobs as errors. On second failure → write error. This matches legacy behavior.

**Budget gate implementation:**
```python
async def _check_budget(self) -> bool:
    """Returns True if budget allows more calls. False to stop.

    Best-effort under concurrency — up to max_concurrent extra
    calls may slip through before the gate trips (all N workers
    can read the same call count simultaneously).
    """
    cost = await self.store_ops.get_cost(today_str())
    if cost and cost.calls >= self.config.llm.max_daily_score_calls:
        self.logger.warning("daily call limit reached", calls=cost.calls)
        return False
    if cost and cost.spent_usd >= self.config.llm.max_daily_cost_usd:
        self.logger.warning("daily cost limit reached", spent=cost.spent_usd)
        return False
    return True
```

**Acceptance criteria:**
- [ ] Constructor takes `EvaluateDependencies` + `EvaluateRuntimeConfig` dataclasses and stays within the argument-count gate
- [ ] Service depends on `PromptRenderer` port, not `adapters.llm._prompts`
- [ ] `create_app()` no longer constructs `EvaluateService` (removed from `AppContext`)
- [ ] All 11 `test_services.py` call sites updated for new constructor
- [ ] Deprecated `timeout_s` removed from `LLMSettings`, service-level timeout removed
- [ ] Timeout test redesigned or removed (no more `LLMSettings(timeout_s=0.001)`)
- [ ] `run()` accepts `stage`, `corpus`, `limit`, `max_days`, and `dry_run` parameters
- [ ] Concurrent evaluation with existing semaphore pattern expanded
- [ ] Budget gate stops evaluation when call count exceeded
- [ ] Budget gate stops evaluation when cost exceeded
- [ ] Budget gate race condition documented (best-effort, up to `max_concurrent` overshoot)
- [ ] Stage A → threshold check via `self.config.stage_a_threshold` → mark_stage_b_skipped for below-threshold jobs
- [ ] Stage A queue uses `quality_bands={"full","good"}`, `corpus`, `limit`, and `max_days`
- [ ] Short JD guard (`<200` chars) writes Stage A error without calling LLM
- [ ] Stage B loads pending via `load_pending_stage_b(limit=..., max_days=...)` (no threshold param, retryable error rows honored by store)
- [ ] Post-pool failure sweep: all non-completed Stage B jobs retried via `llm_stage_b_sweep`
- [ ] `record_cost` called after each returned LLM response, before parse, including parse-error cases
- [ ] `record_llm_usage` called after each returned LLM response (with `job_id`, `stage`, `run_id`)
- [ ] `ScoringParseError` retry semantics match Decision 6; final failure writes `save_stage_a_error` / `save_stage_b_error` and continues
- [ ] `dry_run=True` skips LLM calls
- [ ] `stage="a"` runs only Stage A, `stage="b"` runs only Stage B
- [ ] `corpus="unrated"|"all"|"failed"`, `limit`, and `max_days` are covered by service tests
- [ ] PipelineRun tracks stage_a_scored, stage_b_scored, errors
- [ ] Budget gate tests: 4 scenarios (not exceeded, call limit, cost limit, first call of day)
- [ ] Integration tests with mock subprocess + real PG
- [ ] All committed

---

## Task 9: CLI evaluate Command Update

**Files:**
- Modify: `src/jobfeed/cli/__init__.py` (remove `evaluate_service` from `AppContext`)
- Modify: `src/jobfeed/cli/evaluate.py`

**What to build:**

**Lazy construction in `evaluate` command (NOT in `create_app()`):**
Following the Phase 2 pattern where ATSSource is built lazily in `_run_scan()`, LLM clients, prompt renderer, resume text, and `EvaluateService` are built lazily inside the `evaluate` command handler. This ensures `./bin/jobfeed scan`, `./bin/jobfeed digest`, and `./bin/jobfeed migrate` work without requiring LLM CLI tools to be installed or a resume file to exist.

```python
async def _run_evaluate(
    app: AppContext,
    *,
    stage: str,
    corpus: str,
    limit: int | None,
    max_days: int | None,
    parallel: int | None,
    threshold: int | None,
    dry_run: bool,
) -> PipelineRun:
    async def action() -> PipelineRun:
        settings = app["settings"]
        llm_settings = settings.llm
        if parallel is not None:
            llm_settings = llm_settings.model_copy(update={"max_concurrent": parallel})

        # Validate resume file
        resume_path = Path(llm_settings.master_resume_path)
        if not resume_path.is_file():
            raise click.ClickException(f"Resume file not found: {resume_path}")
        resume_text = resume_path.read_text()

        # Build LLM clients lazily
        price_table = load_price_table()
        logger = app["logger"]
        llm_a = build_llm_client(llm_settings.stage_a, settings=llm_settings, price_table=price_table, logger=logger)
        llm_b = build_llm_client(llm_settings.stage_b, settings=llm_settings, price_table=price_table, logger=logger)

        # Build sweep adapter with unbounded timeout (Decision 12)
        sweep_settings = llm_settings.model_copy(update={"codex_timeout_s": None, "claude_timeout_s": None})
        llm_b_sweep = build_llm_client(llm_settings.stage_b, settings=sweep_settings, price_table=price_table, logger=logger)

        prompt_renderer = JinjaPromptRenderer(
            templates_dir=templates_dir_from_package(),
            preamble_path=Path(llm_settings.preamble_personal_path) if llm_settings.preamble_personal_path else None,
        )
        store = app["store"]
        service = EvaluateService(
            deps=EvaluateDependencies(
                store=store,
                store_ops=cast(StoreOpsMixin, store),
                prompt_renderer=prompt_renderer,
                llm_stage_a=llm_a,
                llm_stage_b=llm_b,
                llm_stage_b_sweep=llm_b_sweep,
            ),
            config=EvaluateRuntimeConfig(
                llm=llm_settings,
                stage_a_threshold=threshold if threshold is not None else settings.scoring.stage_a_threshold,
                resume_text=resume_text,
            ),
            logger=logger,
        )
        return await service.run(
            stage=stage,
            corpus=corpus,
            limit=limit,
            max_days=max_days,
            dry_run=dry_run,
        )

    return await run_with_store(app, action)
```

**`create_app()` change:** Remove `EvaluateService` construction and `evaluate_service` key from `AppContext`. The evaluate command builds the service itself. This also removes the need for `MockLLM()` in `create_app()` — mock/real selection happens via the factory.

**CLI `evaluate` command flags:**
```python
@click.option("-j", "--parallel", default=None, type=int, help="LLM concurrency (default: from config)")
@click.option("--stage", type=click.Choice(["a", "b", "both"]), default="both")
@click.option("--corpus", type=click.Choice(["unrated", "all", "failed"]), default="unrated")
@click.option("--limit", default=None, type=int, help="Maximum jobs to evaluate per stage")
@click.option("--max-days", default=None, type=int, help="Freshness filter on discovered_at")
@click.option("--threshold", default=None, type=int, help="Stage A threshold override")
@click.option("--dry-run", is_flag=True)
```

`-j N` overrides `settings.llm.max_concurrent` for this run by creating a modified `LLMSettings` copy before constructing the service.

**Resume file validation:** If `master_resume_path` does not exist, print clear error and exit 1 (don't silently evaluate with empty resume). Validation happens in the evaluate command handler, NOT in `create_app()`.

**Acceptance criteria:**
- [ ] `./bin/jobfeed evaluate` runs with the configured backend in the Docker runtime, or exits with a clear backend/toolchain configuration error
- [ ] `./bin/jobfeed evaluate -j 2 --stage a --limit 5` runs Stage A only with 2 concurrent workers
- [ ] `./bin/jobfeed evaluate --dry-run --corpus unrated --max-days 7` shows what would be evaluated
- [ ] Missing resume file → clear error, exit 1
- [ ] `./bin/jobfeed scan --source mock` still works without LLM tools installed
- [ ] `./bin/jobfeed digest` still works without LLM tools installed
- [ ] `create_app()` no longer creates `MockLLM()` or `EvaluateService`
- [ ] `--help` shows `--stage`, `--corpus`, `--limit`, `--max-days`, `--threshold`, `-j/--parallel`, and `--dry-run`
- [ ] All committed

---

## ~~Task 10: Unit Tests + Contract Tests~~ (REMOVED — tests folded into respective tasks)

Tests are created alongside their implementations to avoid duplication and ensure `make quality` passes after each task:
- **Task 1**: `test_scoring_production.py` (prompt render/parse), updates to `test_scoring.py`, `test_mock_adapters.py`, `test_store_contract.py`
- **Task 4**: `test_llm_codex.py` (6 scenarios: happy path, timeout+retry, Codex API error, non-zero exit, missing agent_message, cost estimation)
- **Task 5**: `test_llm_claude.py` (5 scenarios: happy path, timeout+retry, malformed envelope, cost propagation, latency capture)
- **Task 6**: `test_llm_factory.py` (provider routing)
- **Task 7**: `test_llm_usage_contract.py` (PG round-trip, `@pytest.mark.postgres`)
- **Task 8**: `test_budget_gate.py` (4 scenarios), `test_evaluate_chain.py` (integration), `test_services.py` updates

---

## Task 10: Live Smoke Tests + CI Update + Verification (was Task 11)

**Files:**
- Create: `tests/live/test_llm_live_smoke.py`
- Modify: `pyproject.toml` only if the `live` marker is not already registered

**What to build:**

**Live smoke tests:**
```python
@pytest.mark.live
class TestLLMLiveSmoke:
    async def test_codex_stage_a(self):
        """Real codex exec call with Stage A prompt."""
        llm = CodexCliLLM(model="gpt-5.4-mini", timeout_s=60, ...)
        response = await llm.complete(stage_a_request)
        result = parse_stage_a_response(response.content, ...)
        assert 0 <= result.score <= 100

    async def test_codex_stage_b(self):
        """Real codex exec call with Stage B prompt."""
        llm = CodexCliLLM(model="gpt-5.5", timeout_s=60, ...)
        response = await llm.complete(stage_b_request)
        result = parse_stage_b_response(response.content, ...)
        assert result.verdict in Verdict

    async def test_claude_stage_a(self):
        """Real claude -p call with Stage A prompt."""
        llm = ClaudeCliLLM(model="claude-haiku-4-5", timeout_s=210, ...)
        response = await llm.complete(stage_a_request)
        result = parse_stage_a_response(response.content, ...)
        assert 0 <= result.score <= 100

    async def test_claude_stage_b(self):
        """Real claude -p call with Stage B prompt."""
        llm = ClaudeCliLLM(model="claude-sonnet-4-6", timeout_s=210, ...)
        response = await llm.complete(stage_b_request)
        result = parse_stage_b_response(response.content, ...)
        assert result.verdict in Verdict
```

Live smoke tests must skip cleanly when the relevant CLI binary or authentication is unavailable. They are debug/diagnostic tests, not CI acceptance.

**CI update:** No workflow changes needed for Phase 3 — unit tests mock subprocess (no real LLM), integration tests mock subprocess + real PG. Live tests are excluded from CI.

**Docker runtime gate:** Before claiming Task 10 or Phase 3 complete, verify the selected real backend through `./bin/jobfeed`, not host-native `jobfeed`. If the `jobfeed-cli` container does not contain the selected CLI executable and valid auth, either add a task-sized Dockerfile/docker-compose change to configure it, or leave Task 10 explicitly blocked. Host-native live smoke can diagnose adapter behavior but cannot close the milestone by itself.

**End-to-end verification:**
1. `./bin/jobfeed scan --source mock` → mock jobs in DB
2. `./bin/jobfeed evaluate -j 2 --limit 5` → real LLM scores jobs (with codex or claude per config) in the Docker runtime. If this fails because the Docker runtime lacks the CLI toolchain/auth, stop and mark Task 10 blocked.
3. `./bin/jobfeed digest` → digest includes scored jobs with real scores
4. Verify: `llm_usage` table has entries, `cost_ledger` updated, PipelineRun recorded
5. Verify: `./bin/jobfeed evaluate --dry-run` shows nothing new to evaluate (all jobs already scored)
6. Verify: budget gate triggers when `max_daily_score_calls = 1` (after the first call, second is blocked)

**Acceptance criteria (Phase 3 milestone):**
- [ ] Full scan → evaluate → digest chain works through `./bin/jobfeed` with real LLM in the configured Docker runtime
- [ ] Missing real CLI toolchain/auth in Docker produces a clear actionable error, but this is a blocked state for Task 10, not a passing milestone
- [ ] MockLLM path unbroken (`stage_a = "mock/stage-a"`)
- [ ] Both codex and claude backends produce valid Stage A + Stage B results
- [ ] `llm_usage` table populated with correct per-call data
- [ ] `cost_ledger` updated with daily totals
- [ ] Budget gate (call count) stops evaluation when limit reached
- [ ] Budget gate (cost) stops evaluation when limit reached
- [ ] `-j N` concurrency works
- [ ] `--stage a` / `--stage b` / `--stage both`, `--corpus`, `--limit`, `--max-days`, and `--threshold` work
- [ ] Post-pool failure sweep retries every non-completed Stage B job, not only timeout failures
- [ ] Architecture boundary tests pass (adapters don't leak into services)
- [ ] `make quality` passes
- [ ] All committed

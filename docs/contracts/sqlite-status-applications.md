# SQLite status、application 与 interview 行为合同

**状态：** Task 0 frozen baseline（实现前合同）

**验证日期：** 2026-08-11

**范围：** 当前 `StoreStatusMixin`、`StoreApplicationMixin`、
`StoreInterviewMixin` 的 26 个公开方法；SQLite 目标 API 不要求保留相同方法数

**目的：** 在 PostgreSQL → SQLite 重写前冻结用户可观察行为、原子性和兼容
边界

**约束：** 本文件只描述合同，不授权实现 SQLite、删除旧方法或改变产品行为

**source of truth：** 已批准的 SQLite cutover 计划定义目标和 task 顺序；本文件
定义本 slice 的逐方法行为。若本文件标为“当前未定义”，实现不得自行发明新语义，
必须先补 golden test 或取得决策。

## 1. 审计口径与结论

本合同以以下证据交叉核对，而不是只抄 port docstring：

- 端口：`src/jobfeed/ports/store_status.py`、
  `src/jobfeed/ports/store_application.py`、`src/jobfeed/ports/store_ext.py`。
- 现有实现：`src/jobfeed/adapters/store/postgres.py`。
- 上层调用：`src/jobfeed/services/workflow.py`、
  `src/jobfeed/services/application.py`、CLI 与 Web routes。
- schema：`migrations/versions/0001_initial_schema.py` 与
  `migrations/versions/0006_phase6_status_apply.py`。
- tests：第 8 节列出的 contract、integration、unit、web 与 e2e 证据。

审计结果：

- 当前 Status 12 个、Application 10 个、Interview 4 个，共 26 个公开方法；它们是
  行为审计输入，不是 SQLite facade 的目标 method count。
- `status row + history row` 是不可分割写入单元。
- `variant + snapshots + applied row + status + history` 是 apply 的不可分割写入
  单元；duplicate apply 是唯一特殊路径，详见 4.2。
- bulk transition 是“每个 twin cluster 原子”，不是整批原子。
- interview add/complete 与 `last_status_change_at` 更新同事务。
- 4 个方法没有静态 production call site，但仍有测试或兼容价值；Task 0 不删除。
- `StoreEvaluationBatchMixin` 与 `StoreStageBPreviewMixin` 虽也位于
  `store_ext.py`，属于 evaluation/claim slice，不在本文 26 个方法内。

### 1.1 API 收敛 disposition

合同测试锁定行为，不强迫 SQLite 继续暴露每个旧入口。本文使用四种处置：

- **retain：** 继续作为 typed public capability operation。
- **merge：** 行为并入更高层 aggregate/query family，旧方法不进入最终 facade。
- **wrapper：** 迁移期保留薄兼容入口，最终不计入 public target。
- **retire：** 没有 production caller 且已有权威替代，完成调用审计后删除。

| 当前方法 | disposition | SQLite 目标 |
|---|---|---|
| `transition_status` | retain | Status aggregate 的单条原子 command。 |
| `get_status` | retain | Status projection 单条读取。 |
| `restore_from_archived` | retire | 由现有 `WorkflowService.restore` 的 get-history-transition 流程取代，统一支持 ghosted/archived。 |
| `auto_decay` | retain | 一次 sweep aggregate command。 |
| `list_statuses` | retain | 一个 typed status query，不拆 filter 方法。 |
| `append_note` | retain | Status aggregate command。 |
| `set_followup` | retain | Status aggregate command。 |
| `workflow_attention` | retain | 一个 attention projection；保留当前多查询弱一致性，不承诺跨 bucket snapshot。 |
| `compute_reapply_notice` | merge | 并入 application projection/query family，不再属于通用 status facade。 |
| `get_status_history` | retain | Restore 与 job detail 共用的 projection。 |
| `expand_twin_ids` | merge | 变为 bulk transition/twin projection 共用的 adapter 内部 query。 |
| `transition_status_bulk` | retain | Twin-cluster aggregate command。 |
| `record_application` | wrapper | 旧无-snapshot入口薄转发到 aggregate apply；调用迁移完成后 retire。 |
| `record_application_with_snapshots` | retain | 重命名为唯一权威 application aggregate command。 |
| `get_application` | retain | Application projection。 |
| `list_applications` | retain | Application collection projection。 |
| `application_stats` | retain | Application aggregate projection。 |
| `save_resume_snapshot` | merge | 运行时写入并入 aggregate apply；独立导入写入属于 migration tooling。 |
| `get_resume_snapshot` | retain | 精确 identity projection。 |
| `get_resume_snapshot_by_prefix` | wrapper | 薄转发到统一 snapshot resolver；保留 distinct not-found/ambiguous errors。 |
| `list_resume_snapshots` | retain | Snapshot collection projection。 |
| `register_resume_variant` | merge | 并入 transition/apply aggregate transaction，避免 caller 预注册。 |
| `add_interview_round` | retain | Interview aggregate command。 |
| `list_interview_rounds` | retain | Interview projection。 |
| `complete_interview_round` | retain | Guarded interview aggregate command。 |
| `list_upcoming_interviews` | merge | 与 `workflow_attention` 共用 upcoming query family，不保留无 caller 的 facade 方法。 |

本 slice 的建议终态是 **18 个 retain operations + 2 个临时 wrappers**；5 个 merge、
1 个 retire（`record_application` wrapper 最终再 retire）。这让全项目朝约 55–70 个
public capability operations 收敛，同时 26 个既有行为仍有迁移期回归证据。

## 2. 跨方法冻结规则

### 2.1 标识、NULL、时间与 JSON

- 所有公开 `job_id` 输入保持字符串接口；adapter 在 SQL 边界转换为整数。非数字
  输入当前抛出 `ValueError`。返回的 `StatusInfo.job_id` 与
  `ApplicationRecord.job_id` 保持字符串，`InterviewRound.job_id` 保持整数。
- SQLite 必须启用 foreign keys。删除 job 时级联删除 `job_status`、history、
  `applied` 与 interview rounds；resume snapshots 和 variants 独立保留。
- 可空字段必须保持 `None ↔ SQL NULL`，不得转换为空串、空对象或字符串
  `"null"`。
- 所有 domain `datetime` 返回 aware UTC。SQLite 以 canonical UTC 表示存储，并
  以绑定的 UTC cutoff 代替数据库 session timezone；精度必须足够保持 history
  顺序外的时间比较。
- `verdict_snapshot`、`fit_snapshot`、`hooks_snapshot` 是可空原始 TEXT。Store
  不解析、不重排、不 canonicalize，也不验证其 JSON 结构；round-trip 必须字节
  等价于输入 Python 字符串。
- resume snapshot `content` 是原始 TEXT；内容寻址的 key 由调用方提供。
  `ResumeSnapshot` 只验证 hash 非空且为 hex，不强制 64 位。
- 明确 `ORDER BY` 的顺序必须保持；没有次级 tie-break 的查询，其 tie 顺序当前
  未定义，不得写依赖测试。SQLite 实现可以增加稳定主键 tie-break，但必须保留
  第一排序键和结果集合。

### 2.2 状态与 history

- 状态值、合法转换、terminal 集合、decay 集合均由
  `src/jobfeed/domain/status.py` 决定，不在 adapter 复制第二份业务表。
- 新 job 创建时必须在同一 job 保存事务中 seed `job_status(status='new')`，并追加
  `from_status=NULL, to_status='new'` history。当前 PG 由 trigger 完成；SQLite
  可由 adapter 完成，但可观察结果相同。
- 每次成功 status transition 恰好更新一个 current status row，并追加一条
  history。任一步失败必须全部 rollback。
- history 的权威顺序是 append-only `id DESC`，不是 wall-clock
  `changed_at DESC`。允许序列存在 gap；合同只依赖单调插入顺序。
- transition 到 `applied` 时，`next_followup_at = now + grace_days`；
  `applied → interviewing` 保留 follow-up；离开 active application statuses
  (`applied`, `interviewing`) 时清空 follow-up。
- 提供 `resume_variant` 时更新 current status；未提供时保留 current 值。
  history 记录的是“本次提供的 variant”，不是回填后的 current 值。
- forced transition 且 reason 未提供时，history reason 固定为 `FORCE`。

### 2.3 事务可见性

- 单写方法没有显式 `transaction()` 时仍是一条 SQL statement 的原子提交。
- `workflow_attention()` 与 `application_stats()` 当前由多个 SELECT 组成且没有
  read snapshot transaction；并发写期间各 SELECT 可能观察到不同 commit。这是
  当前弱一致性，不应误写为单快照保证。
- `WorkflowService.transition(..., note=...)` 的 transition 与 append-note 是两个
  store 调用，不是一个原子事务。
- `WorkflowService.add_round()` 的 applied→interviewing transition 与 round insert
  是两个 store 调用，不是一个原子事务。本文只冻结每个 store 方法自身边界。

## 3. Status 方法矩阵（12）

### 3.1 单条状态与查询

| 方法 | 输入、输出与 error | 事务与幂等 | 排序、NULL、时间及其他规则 | 现有证据 |
|---|---|---|---|---|
| `transition_status(request)` | 输入完整 `TransitionRequest`；返回目标 status。status row 不存在抛 `KeyError`；未知或非法转换抛 `ValueError`；archived→new 还需 `force+i_mean_it`。 | current status update 与一条 history insert 同事务。普通调用不是幂等：重复同状态通常非法；force 可写 same-state history。 | 遵守 2.2 follow-up/variant/reason 规则；`last_status_change_at` 取 DB now。 | `tests/contract/test_store_contract.py::TestStatusLifecycle`；`tests/contract/test_status_apply_persistence.py::TestRowShapeAssertions::test_single_transition_history_row`；`tests/integration/test_store_pg_behaviors.py::test_transition_followup_lifecycle`；`tests/unit/test_status.py`。 |
| `get_status(job_id)` | 返回 joined `StatusInfo`，不存在返回 `None`；非数字 ID 抛 `ValueError`。 | 单条只读，无显式事务，无副作用。 | company/title 来自 jobs；所有 nullable 字段原样返回；理论上的 NULL `last_status_change_at` mapper fallback 为 Unix epoch，但 schema 为 NOT NULL。 | `tests/contract/test_store_contract.py::TestStatusLifecycle::test_fresh_job_auto_seeded_to_new`；`tests/integration/test_list_output_store.py::test_get_status_carries_company_title`。 |
| `restore_from_archived(job_id)` | 仅接受当前 `archived`；否则 `ValueError`。取最近一条 `to_status != 'archived'` 并返回；没有则 `ValueError`。 | read current、read history、forced transition 与新 history 同事务。成功后再次调用会因不再 archived 而失败，不幂等。 | 历史按 `id DESC`。注意它会接受最近的 `ghosted` 作为 target；这与 production `WorkflowService.restore()` 跳过 archived+ghosted 的语义不同。 | `tests/contract/test_store_contract.py::TestStatusLifecycle::test_restore_from_archived`。该方法无 production call site，见 7.1。 |
| `get_status_history(job_id)` | 返回 `to_status` 字符串列表；不存在/无 history 返回 `[]`；非数字 ID 抛 `ValueError`。 | 单条只读。 | 严格 `id DESC` newest-first；不以 `changed_at` 排序，能够抵抗 wall-clock skew。 | `tests/integration/test_status_queries_store.py::test_get_status_history_newest_first`、`test_get_status_history_fresh_job`、`test_get_status_history_ignores_clock_skew`。 |

### 3.2 筛选、note、follow-up 与 attention

| 方法 | 输入、输出与 error | 事务与幂等 | 排序、NULL、时间及其他规则 | 现有证据 |
|---|---|---|---|---|
| `list_statuses(filters=None)` | 返回 joined `StatusInfo` 列表；filters 全部 AND。`statuses`、`days`、`since`、`no_response_days`、`needs_followup`、`notes_contain`、`limit` 均可选。 | 单条只读；无 filter 表示全部。 | 主排序 `last_status_change_at DESC`，tie 未定义。`days/since` 用 `>=`；no-response 仅 applied/interviewing 且严格 `< cutoff`；due 为 UTC calendar date `<= today`，不是瞬时时间。notes 当前是 case-insensitive SQL pattern substring，`%/_` 未转义；literal wildcard 语义未被测试。 | `tests/contract/test_store_contract.py::TestStatusListing`；`tests/integration/test_status_queries_store.py`；`tests/integration/test_followup_store.py`；`tests/integration/test_list_output_store.py`。 |
| `append_note(job_id, text)` | 返回是否更新到 status row；缺失返回 `False`；非数字 ID 抛 `ValueError`。 | 单条 UPDATE 原子；不是值幂等，每次都会 append。 | 追加格式固定为 `[YYYY-MM-DD HH:MM] {text}\n`，应用侧 UTC、分钟精度；NULL notes 视为空串；同时把 `last_status_change_at` 重置为 DB now。空 text 仍追加 timestamp 行。 | `tests/contract/test_store_contract.py::TestStatusLifecycle::test_append_note`；`tests/integration/test_list_output_store.py::test_notes_contain_matches_case_insensitively`；`tests/contract/test_status_apply_persistence.py::TestEdgeCases::test_note_resets_clock_in_append_sql`。 |
| `set_followup(job_id, at)` | 返回是否更新到 status row；缺失返回 `False`；非数字 ID 抛 `ValueError`。 | 单条 UPDATE 原子；相同值重复设置是状态幂等。 | 精确保留 aware datetime；不改变 status、history 或 `last_status_change_at`。`needs_followup` 对当天稍晚的时间也视为 due。 | `tests/integration/test_followup_store.py`；`tests/unit/test_workflow_service.py::TestSetFollowup`；`tests/e2e/test_cli_parity.py::test_followup_set_and_needs_followup_round_trip`。 |
| `workflow_attention(auto_ghost_days, lookahead_days)` | 返回三个 list：follow-up、interview prep、going ghosted；无匹配均为空。 | 三个以上 SELECT，无 snapshot transaction、无写入。 | follow-up：active statuses、due-date `<= today`、followup ASC。interview prep：每 job 最早 open scheduled round 且 `scheduled_at <= now+lookahead`，没有 lower bound；scheduled 部分按 job ID、round time 排序，随后 append “没有任何 open scheduled round”的 interviewing jobs（status clock DESC）。仅有 far-future open round 的 job 不属于两类。going-ghosted：DECAY_SOURCES 且严格早于 `now-(ghost-lookahead)`，oldest first。各 bucket 的 `days_since` 为整日差。 | `tests/contract/test_store_contract.py::TestWorkflowQueries::test_workflow_attention_structure`；`tests/integration/test_store_pg_behaviors.py::test_workflow_attention_warns_interview_stage`；Web insights tests。scheduled/unscheduled 边界没有完整 golden coverage，见 7.2。 |
| `compute_reapply_notice(job_id, lookback_days)` | job 不存在、company normalization 为空或无 match 返回 `None`；否则返回固定 human-readable notice。非数字 ID 抛 `ValueError`。 | 1–2 条只读，无显式事务。 | 排除自己；以 `company_norm`（缺失时运行 `normalize_company(company)`）精确匹配；只看 active statuses 且 `last_status_change_at >= cutoff`。`LIMIT 1` 无 ORDER，多 match 时选择当前未定义。 | `tests/integration/test_store_pg_behaviors.py::test_reapply_notice_detects_interview_substage`；`tests/contract/test_store_contract.py::TestWorkflowQueries`；`tests/web/test_apply_routes.py::test_reapply_notice_for_same_company_active_application`。 |

### 3.3 decay、twins 与 bulk

| 方法 | 输入、输出与 error | 事务与幂等 | 排序、NULL、时间及其他规则 | 现有证据 |
|---|---|---|---|---|
| `auto_decay(ghost_days, archive_ignored_days)` | 返回被选择并转换的 ghosted/archived 数量。 | 两批 select 与逐行 status+history 写入在一个事务；任一行失败整个 sweep rollback。已转换行下次不再匹配，结果意义上幂等。 | active statuses 严格早于 ghost cutoff → ghosted；ignored 严格早于 archive cutoff → archived；reason `auto_decay`、force=True。row 处理顺序当前未定义。 | `tests/contract/test_store_contract.py::TestStatusLifecycle`，包含 aged-row whole-sweep rollback injection；`tests/contract/test_status_apply_persistence.py::TestEdgeCases`；`tests/unit/test_evaluate_funnel.py`。 |
| `expand_twin_ids(job_ids)` | 返回每个输入 int ID → cluster IDs。空输入 `{}`；不存在或任一 norm 为空则 singleton `[id]`。 | 多条只读，无 snapshot transaction。输入重复最终只有一个 dict key。 | twin key 是同 `company_norm + title_norm`；cluster list 无 ORDER，顺序未定义。 | `tests/integration/test_twin_cascade_store.py::test_expand_twin_ids_*`；domain twin contract。 |
| `transition_status_bulk(request)` | 返回 `BulkResult(succeeded, failed, skipped, cascaded)`；selected job 失败记录 `(job_id, str(error))`，其他 cluster 继续。 | 每个 cluster 独立事务；整批非原子。同一成功 cluster 后续重复 selected ID 被跳过。selected 总是尝试；terminal twin 只计 skipped。 | selected history reason=`reason_selected`；非terminal twin reason=`reason_cascade`。一个 cluster 任何 transition 失败，该 cluster 全 rollback且不增加 counts。cluster/item 处理按请求顺序；cluster member 顺序未定义。 | `tests/integration/test_twin_cascade_store.py`；`tests/contract/test_status_apply_persistence.py::TestRowShapeAssertions::test_bulk_twin_cascade_reasons`；Web bulk tests。 |

## 4. Application 方法矩阵（10）

### 4.1 不可分割 apply 单元

对新 application，以下状态必须在同一事务完成：

```text
optional variant registration
→ zero or more content-addressed snapshot inserts
→ applied insert
→ terminal-status guard
→ status update or active-status no-op
→ exactly one history insert
→ commit
```

terminal guard 或后续任一步失败时，上述全部 rollback。不得出现“snapshot 已写但新
application/status 没写”的部分提交。

### 4.2 apply 与读取

| 方法 | 输入、输出与 error | 事务与幂等 | NULL、时间、JSON、排序及其他规则 | 现有证据 |
|---|---|---|---|---|
| `record_application(record)` | 新 job application 返回 `True`；同 job 已有 applied row 返回 `False`；新 insert 遇 terminal current status 抛 `ValueError`；missing job 由 FK 报错。 | applied insert、terminal guard、status update、history 同事务。duplicate check 发生在 terminal guard 前，因此“已 applied 后又 terminal”的重复调用仍返回 False 且不改原 row。 | 新写把 status 设 applied，follow-up 设 now+7d，history reason=`record_application`。所有 snapshot JSON 字段按原 TEXT/NULL 保存。该 legacy 方法不会写 resume snapshot/variant；异常状态下可能把 interviewing 回退 applied。 | `tests/contract/test_store_contract.py::TestApplicationAudit`；`tests/contract/test_status_apply_persistence.py::TestRowShapeAssertions::test_application_audit_idempotent`。无 production call site，见 7.1。 |
| `record_application_with_snapshots(record, snapshots, resume_variant)` | 新 application 返回 `True`；duplicate 返回 `False`；新 insert 遇 terminal status 抛 `ValueError`；missing job 由 FK 报错。 | 整个 4.1 单元一个事务。**特殊 duplicate 语义：** variant registration 与 snapshot upserts 先发生，随后 applied conflict 返回 False 并正常 commit；原 applied row/status/history 不变，但新 snapshots（及 supplied variant）保留。terminal error 则全部 rollback。 | status 已 applied/interviewing 且尚无 applied row时，不回退 status：写 same-state history，reason=`record_application_noop`；truthy variant 更新 status。其他非terminal status → applied、follow-up now+7d、reason=`record_application`。active no-op 不更新 status clock/follow-up。JSON 原样 TEXT。 | `tests/integration/test_apply_tx_store.py`；`tests/web/test_apply_routes.py::test_apply_persists_snapshots_audit_and_transition`、terminal/noop tests；`tests/unit/test_application_service.py::TestApply`。active-status no-op 分支缺直接 golden test，见 7.2。 |
| `get_application(job_id)` | 返回一个完整 `ApplicationRecord` 或 `None`；非数字 ID 抛 `ValueError`。 | 单条只读。 | 所有 optional hash、letter、method、JSON snapshot 与 notes 原样 NULL/TEXT；job ID 转 string。 | `tests/web/test_apply_routes.py::test_apply_persists_snapshots_audit_and_transition`。直接 store coverage 较薄，见 7.2。 |
| `list_applications(limit, resume_hash_prefix)` | 返回最多 limit 条；prefix 对 master **或** tailored hash 匹配。 | 单条只读。 | prefix 对 `\`, `%`, `_` 做 literal escaping，再加 trailing `%`；case-sensitive。排序 `applied_at DESC`，ties 未定义。字段与 get 相同；NULL hash 不匹配 prefix。 | `tests/contract/test_store_contract.py::TestApplicationAudit`；`tests/integration/test_snapshot_lookup_store.py::test_list_applications_*`；apply tx tests；e2e apply history。 |
| `application_stats(since_days_ago, by_resume)` | 返回 applied/response/interview/offer/rejection counts、median days，可选 by-resume。无 window applications 时全 0、median None、by_resume None。 | 多个 SELECT，无 snapshot transaction。 | cohort 是 window 内 `to_status='applied'` 的最小 history ID，不是 `applied.applied_at`。response 资格只由 append order 决定：`response.id > applied_id`；clock 不决定因果。interview count 包含 reached interviewing **或 offer**。duration 使用 response/apply timestamps 的整日差并 clamp 至最小 0；median 取每 job 第一个 causal response。同 job 多条 window applied history 时，variant 同样取该最小 apply ID；NULL→`unknown`。 | `tests/contract/test_store_contract.py::TestApplicationAudit::test_application_stats_*`，包含 fixed median、pre-apply ID exclusion、clock rollback clamp 与 variant ordering；`tests/contract/test_status_apply_persistence.py::TestEdgeCases::test_application_stats_zero_applications`；`tests/unit/test_application_service.py::TestStats`。 |

### 4.3 Resume snapshots 与 variants

| 方法 | 输入、输出与 error | 事务与幂等 | NULL、时间、JSON、排序及其他规则 | 现有证据 |
|---|---|---|---|---|
| `save_resume_snapshot(snapshot)` | 无返回值；domain 先验证 hash 为非空 hex。 | 单条 `INSERT ... ON CONFLICT DO NOTHING`；同 hash 幂等且 first write wins，后续 content/source/notes 不覆盖。 | captured_at/source/content 必填；notes nullable；content 原样 TEXT。 | `tests/contract/test_store_contract.py::TestResumeSnapshots`；`tests/integration/test_snapshot_lookup_store.py`。无 production call site，见 7.1。 |
| `get_resume_snapshot(resume_hash)` | 完整 hash 精确读取，返回 snapshot 或 `None`。 | 单条只读。 | 大小写敏感精确 key；字段原样 round-trip。 | `tests/contract/test_store_contract.py::TestResumeSnapshots`；`tests/integration/test_apply_tx_store.py`；`tests/unit/test_application_service.py::TestGetSnapshot`。 |
| `get_resume_snapshot_by_prefix(prefix)` | 唯一 match 返回 snapshot；0 match 抛 `SnapshotNotFoundError`；2+ match 抛 `SnapshotAmbiguousError`。 | 单条只读，最多取 2 行判歧义。 | prefix 对 `\`, `%`, `_` literal escape；case-sensitive。空 prefix 的结果取决于总行数（0=not found、1=return、2+=ambiguous）。match 选择无 ORDER，但唯一性判定不依赖排序。 | `tests/integration/test_snapshot_lookup_store.py::test_unique_prefix_resolves`、not-found、ambiguous、literal-wildcards；e2e snapshot show/diff。 |
| `list_resume_snapshots(source=None)` | 返回不含 content 的 summaries；source 为 stored source 精确过滤。orphan snapshot 也返回。 | 单条只读。 | usage 是引用该 hash 为 master **或** tailored 的 applied row 数；同一 row 两列都等于 hash 仍计 1。排序 `captured_at DESC, resume_hash ASC`，因此稳定。 | `tests/integration/test_snapshot_lookup_store.py::test_list_snapshots_*` 与 `test_same_hash_as_master_and_tailored_counts_usage_once`；e2e global list。 |
| `register_resume_variant(name, description)` | 新 name 返回 True；已存在返回 False。 | 单条 insert-on-conflict；first write wins，重复不会补写/更新 description。 | name 为主键且非 NULL；description nullable；created_at DB now。当前无 store 层空 name validation。 | `tests/contract/test_store_contract.py::TestResumeSnapshots::test_register_resume_variant`；status/apply persistence test；workflow service variant delegation tests。 |

## 5. Interview 方法矩阵（4）

| 方法 | 输入、输出与 error | 事务与幂等 | NULL、时间、排序及其他规则 | 现有证据 |
|---|---|---|---|---|
| `add_interview_round(job_id, label, scheduled_at)` | 返回新 `InterviewRound`；missing/non-numeric job 分别 FK error/`ValueError`。 | `MAX(round_index)+1` insert 与 status clock bump 同事务。PG 对 unique race 用 savepoint 后重试一次；SQLite 必须通过 serialized write 保留唯一、单调递增的 assignment。重复调用会新增 round，不幂等。 | index 为该 job 当前 max+1，从 1 开始；label 必填但 store 不拒绝空串；scheduled_at nullable；created_at DB now。 | `tests/integration/test_interview_rounds_store.py::test_add_rounds_sequential_index`、scheduled、clock、cascade；workflow/web auto-transition tests。 |
| `list_interview_rounds(job_id)` | 返回该 job rounds；无 row/不存在 job 都返回 `[]`；非数字 ID 抛 `ValueError`。 | 单条只读。 | 严格 `round_index ASC`；字段 NULL 原样返回。 | `tests/integration/test_interview_rounds_store.py::test_list_interview_rounds_ordered`、`test_list_empty_rounds`；web job-detail test。 |
| `complete_interview_round(job_id, round_index, notes)` | 指定 index 时完成该 open round；None 时完成最大 index 的 open round。无匹配、已完成、不存在或并发 loser 均抛 `ValueError("no open interview round...")`。 | target select、guarded update (`completed_at IS NULL`)、status clock bump 同事务；同一 round 的并发完成恰好一个成功。 | completed_at DB now。`notes is None` 保留旧 notes；非-None（包括空串）覆盖。未指定时按 `round_index DESC LIMIT 1`。 | `tests/contract/test_store_contract.py::TestInterviewRoundQueries::test_concurrent_completion_has_exactly_one_winner`；`tests/integration/test_interview_rounds_store.py::test_complete_*`、clock/notes tests；web complete tests。 |
| `list_upcoming_interviews(within_days)` | 返回 future scheduled、open 且 parent status 正为 interviewing 的 rounds。 | 单条只读。 | `now <= scheduled_at <= now + within_days`；NULL scheduled、past、completed 与 non-interviewing parent 均排除。排序 `scheduled_at ASC`，tie 未定义。 | `tests/contract/test_store_contract.py::TestInterviewRoundQueries::test_upcoming_requires_future_round_and_interviewing_parent`；`tests/integration/test_interview_rounds_store.py::test_list_upcoming_interviews` 与 `test_upcoming_excludes_completed`。无 production call site，见 7。 |

## 6. SQLite 实现必须保持的原子失败场景

前 8 个场景必须在 SQLite contract suite 中做 failure injection 或 contention
验证，而不是只做 happy path；第 9 个是必须先决策的当前未定义并发语义：

1. status UPDATE 成功、history INSERT 故障：两者都不可见。
2. history 可写但 status row 缺失：抛 `KeyError`，不可追加孤立 transition。
3. bulk cluster 中一个 twin transition 故障：该 cluster 所有 status/history rollback；
   已提交的其他 cluster 保留，后续 cluster 继续。
4. apply snapshots/variant 已暂存后 terminal guard 失败：variant、snapshots、applied、
   status、history 全部 rollback。
5. duplicate apply：返回 False；原 applied/status/history 不变；但本次提供的新
   content-addressed snapshot 与 variant 按当前执行顺序正常 commit。
6. add interview round 后 status clock update 故障：round insert rollback。
7. complete round 后 status clock update 故障：completed_at/notes update rollback。
8. 两个 writer 同时 add round：`(job_id, round_index)` 不重复、不丢 round。
9. 两个 writer 同时 complete 同一 open round：恰好一个成功，另一个得到 no-open
   `ValueError`；不得产生两次成功响应。

## 7. 静态无生产调用候选与未覆盖行为

### 7.1 只列候选，不授权删除

| 方法 | 静态 production 调用 | 仍存在的价值/证据 | Task 0 处置 |
|---|---:|---|---|
| `restore_from_archived` | 0 | PG contract test；但 WorkflowService 使用 `get_status + get_status_history + transition_status`，且语义不同。 | 保留；先决定统一到 service restore 还是兼容旧 port。 |
| `record_application` | 0 | 大量 shared contract/persistence tests；旧兼容入口。Production apply 使用 `record_application_with_snapshots`。 | 保留；SQLite 首轮需通过合同，退休另行审批。 |
| `save_resume_snapshot` | 0 | snapshot lookup/integration tests与独立维护用途。Production apply 在组合事务内写 snapshots。 | 保留；不得用它拆开 production apply 事务。 |
| `list_upcoming_interviews` | 0 | integration tests；`workflow_attention` 自己实现相邻但不同的 scheduled query。 | 保留；后续可评估由统一 query family 复用。 |

“0”来自对 `src/jobfeed` 排除 port 与 concrete method definition 后的静态直接调用
搜索；不证明没有动态调用、第三方使用或 CLI/API 兼容要求。

### 7.2 SQLite 实现前应补的 golden coverage

- status/bulk/apply 的其余双 writer contention tests；complete-round 已冻结为单
  winner。

本轮已补 active-status apply no-regression、application NULL/TEXT round-trip、
notes wildcard、upcoming boundaries、application stats median/variant/order、
auto-decay rollback 与 concurrent complete-round。`workflow_attention` 的多 SELECT
弱一致性已明确 disposition 为 retain，不把它误写成 snapshot guarantee。

这些 gap 不否定现有合同；它们阻止实现者把未定义细节误当成可自由改变的行为。

## 8. 测试证据索引

### 8.1 跨后端 contract 候选

- `tests/contract/test_store_contract.py`：status lifecycle、application audit、resume
  snapshots、workflow queries、status listing。
- `tests/contract/test_status_apply_persistence.py`：column pins、history row shape、
  bulk reason、application idempotency 与 domain edge cases。当前部分测试直接 inspect
  `PostgresStore` 或执行 raw PG SQL；SQLite contract 化时必须改成行为断言，而不是
  复制 backend source inspection。

### 8.2 PostgreSQL golden integration

- `tests/integration/test_apply_tx_store.py`：apply/snapshot 原子性、terminal rollback、
  duplicate 特殊语义、content-addressed dedup、variant registration。
- `tests/integration/test_snapshot_lookup_store.py`：literal prefix、歧义、usage count、
  source filter、application prefix filter。
- `tests/integration/test_interview_rounds_store.py`：round index、完成规则、upcoming、
  clock bump、cascade。
- `tests/integration/test_twin_cascade_store.py`：twin expansion、per-cluster bulk、
  terminal skip、失败隔离、reason tags。
- `tests/integration/test_status_queries_store.py`：no-response filter 与 history ID
  ordering。
- `tests/integration/test_followup_store.py`：date-due semantics 与 missing row。
- `tests/integration/test_list_output_store.py`：case-insensitive notes、since boundary、
  joined fields。
- `tests/integration/test_store_pg_behaviors.py`：reapply、follow-up lifecycle、ghost
  warning与 auto-seed trigger。

### 8.3 上层行为

- `tests/unit/test_status.py`、`tests/unit/test_status_transitions.py`：状态图与 restore
  pure domain rules。
- `tests/unit/test_workflow_service.py`、`tests/unit/test_application_service.py`：
  service/port orchestration。
- `tests/web/test_workflow_routes.py`、`tests/web/test_apply_routes.py`、
  `tests/web/test_jobs_routes.py`：HTTP error mapping 与 aggregate response。
- `tests/e2e/test_cli_parity.py`：notes/status listing、apply history、snapshot prefix 与
  follow-up CLI parity。

## 9. 本 slice 验收标准

- [x] 当前 26/26 个公开方法均有输入、输出、error、事务与幂等描述。
- [x] 所有适用的排序、tie、NULL、UTC/time、TEXT/JSON 规则已列出。
- [x] status+history 与 apply+snapshots 原子边界已明确冻结。
- [x] 每个方法映射到现有测试证据或明确标注 coverage gap。
- [x] 静态无 production 调用候选只登记、未删除。
- [x] 每个当前方法已标为 retain/merge/wrapper/retire；SQLite 不复制 26-method
  facade。
- [ ] 对应 SQLite slice 验收前，把 7.2 的并发与 failure-injection blockers 转为
  跨后端 golden contract tests。

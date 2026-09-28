# JD 合同与开发 demo 验证记录

日期：2026-09-04。仅离线开发，不是生产准入报告。

## 已完成的合同工作

- 新增独立 Pydantic 合同、required/preferred 条件树、政策范围投影、同一毕业方案和同一 YOE 见证值、三值逻辑、Apply/Pending/Blocked 映射。
- 薪资保留地区、币种、周期、组成和多范围；所有观察分数为空，不在实验中暗定评分规则。
- 新增证据检查：只允许空白差异，恢复原文字符范围。词语、金额和逻辑替换无法借此通过。
- 没有导入旧实验管线，没有改生产、数据库记录、原有测试或评分政策，没有提交/部署。

## 测试与独立审查

先记录新模块不存在的失败，再为实际反例补红测及修正。出现过并已修复：返校未知误 Block、嵌套 AND 改变经验矛盾、类型算子不匹配、coverage 未知丢失提示、无数量经验被强制填写单位、无日期毕业状态无法表达。

当前限定验证：

```sh
.venv/bin/python -m pytest tests/unit/test_jd_semantic_contract.py tests/unit/test_demo_jd_contract.py -q
.venv/bin/python -m ruff check scripts/jd_semantic_contract.py scripts/demo_jd_contract.py tests/unit/test_jd_semantic_contract.py tests/unit/test_demo_jd_contract.py
.venv/bin/python -m scripts.demo_jd_contract contract
```

结果：41 tests passed；ruff passed；6 个构造合同示例符合预期。GPT-6 Astra 独立运行相同测试得到 41 passed，确认没有阻塞当前离线 demo 的问题，无需扩大范围。这些是合同行为证据，不是 NLP 准确率。

## 样本和运行边界

使用原 50 条开发样本中的 3、9、22、26、29、30、31、46，来自 Stripe、AvePoint、MongoDB。读取公开 JD 的完整存储正文；完整指“本次提供文本完整输入”，没有重新验证网页抓取是否完整。没有引入新网站、没有重新采集 500 条。

Sol 首次提取、Terra 独立看相同全文；资格和薪资各为一个任务。每条 JD 共 4 次调用，8 条共 32 次。模型之间不共享输出。每条均全量复核，不能据此估计生产选择性升级比例或节省费用。CLI token 计数包含调用框架开销，不能直接当作简化 API 的成本。

## 运行历史（保留失败，不覆盖）

| 运行 | 范围 | 结果 | 解释 |
| --- | --- | --- | --- |
| data/jd-contract-smoke-v1 | 样本 26，4 次调用 | 2 valid，2 invalid | 两份资格解析都用 unit=none 表达未量化经验，原合同不合理地拒绝；薪资缺失正确表达 |
| data/jd-contract-pilot-v2 | 8 JD，32 次调用 | 22 valid，10 invalid，0 pending | 包含 coverage 与事实种类不一致、无日期毕业状态，以及换行证据问题；不是 68.75% 准确率 |
| data/jd-contract-pilot-v3 | 同样 8 JD，当前合同重新实跑 | 31 valid，1 invalid，0 pending | 修正合同表达并明确 coverage/经验 scope 指令，仍属开发集 |

valid 仅表示 schema、关系引用和证据校验通过。invalid 原始输出仍保存，不能丢掉后只统计成功项。substantive_exact_agreement 是保守的结构比较，可能仍包含同义短语差异，不等于真实语义分歧数量。

## 已见的实质限制

1. v2 样本 22：Sol 保留了 Master+3 / Bachelor+5 路径及额外 8 组技能年限；Terra 的原始输出漏掉了这些额外技能年限。更高阶/独立模型不能仅凭名称当作正确答案。
2. v2 样本 9：两者均没有把 college/university graduated 直接归一为 Bachelor，和用户对该条的确认不同。此类已确认的应用归一化须与原始事实分开记录，不能宣称模型已经自然学会该政策。
3. v2 薪资存在 bonus/equity 是否构成条件性事实的状态差异；正文只写美元符号但未写币种的例子保留 currency=null，没有根据公司猜币种。
4. 技能同义表达、事实粒度和专业经验 scope 会导致结构不一致。当前选择保留分歧，不静默降低校验强度后宣称准确。

## v3 关键项人工核查（主任务核查，不是新增复核 agent）

这是对已讨论关键条件的核查，不是所有技能条目的人工金标，也不计算总体准确率。

| 样本 | 核查结果 | 剩余问题 |
| --- | --- | --- |
| 3 / Stripe | 两份均得到 required >=2 年，排除实习；未添加 12 年硬上限 | preferred 技能的拆分粒度、领域短语不同 |
| 9 / AvePoint | 两份保留已毕业状态、毕业后 >=3 年；没有伪造毕业日期 | Sol 单列 degree，Terra 只用 graduation/领域表达；两者均未达到用户确认的 Bachelor 归一化 |
| 22 / Stripe | 两份均保留 Master+3 或 Bachelor+5，并保留额外 8 组技能年限，实质结构一致 | 薪资 base 金额与 /yr 正确；正文币种未明确而保留 null；非数额 bonus/equity 粒度不同 |
| 26 / AvePoint | 两份均保留 required 非量化经验，以及 preferred >5 年，未混成最低 5 年 | 其余技能组织和措辞仍不同 |
| 29 / MongoDB | 两份均保留 required >=5 年和 preferred 的经验/Master/PhD OR | Terra 漏提 Senior 级别；两份薪资均把未明确周期的范围标为 year |
| 30 / AvePoint | 两份均保留 Degree OR Diploma，没有伪造学历层级，并提取 >=7 年测试经验 | Terra 漏提 Senior；测试经验 scope=domain 的政策消费还需单独验证 |
| 31 / Stripe | 两份均区分 start before 2026-10-01 与 degree by summer 2026；保留 <=18 months 且排除实习、经验替代路径 | Sol 把标题当作 body evidence，导致该份未通过；两份没有发明 summer 的精确日期 |
| 46 / MongoDB | 两份均提取 122000–170000 CAD base / Canada | 两份均填 year，但正文没有明确支付周期；这是共同推断，不是被原文证实的单位 |

重要反例：样本 29、46 的 base 金额/币种正确，不代表单位也正确。两个模型均给出 year，而对应 JD 正文没有年度单位；即使值与原文位置校验通过，仍存在共同语义错误。当前记录级 evidence 无法证明每个属性，后续应区分金额、币种、周期等属性的直接证据与推断；本轮保留错误输出，不覆盖成“已修好”。

v3 的 16 个双模型任务中仅 6 个满足当前保守结构比较，其余包含实质遗漏和同义表达差异。这个比例不是正确率或错误率。全部 32 次调用成功返回，没有超时；单次调用中位数约 17.53 秒，p95（nearest-rank）约 58.37 秒。CLI 共报告 637672 input tokens、35435 output tokens，含系统/框架开销；未测美元成本。

本轮关键数据文件：data/jd-contract-pilot-v3/inputs.json、summary.json、各 sample-task.json。模型原始输出、输入、schema、prompt、调用时间、usage、校验错误和原文字符区间均可读回。真实岗位 production_verdict 和 human_accuracy 始终为 null。

复跑（使用一个不存在的新输出目录，避免覆盖历史）：

```sh
.venv/bin/python -m scripts.demo_jd_contract extract --output data/jd-contract-next-run
```

按用户最新要求，不再对每轮小改动派 Astra 重新复核；末轮模型结果由主任务整理，没有继续启动复核 agent 或新模型运行。

## v4 / v5：继续优化后的实跑结果

本节更新前述 v3 的未解决状态；旧输出保留，没有重写历史结果。

设计改动：v2 输出合同要求薪资的金额、币种、周期、组成、地区、级别分别给证据；模型选出原子证据，代码只验证原子值及出处，不用规则从 JD 挑句子。标题与正文分别溯源；用户确认的 college/university 归一化另存 user_policy，保留原始事实。默认有效输出不重复调用模型，失败最多修正一次，两次失败仍未解决。

| 运行 | 相同 8 条 JD 的 16 个任务 | 模型调用 | 说明 |
| --- | --- | --- | --- |
| v4 | 15 任务通过，1 未解决 | 17 | 样本 22 的 company bonus / sales bonuses 被过窄属性词表拒绝；首轮和修正都失败，保留两份输出 |
| v5 | 16 任务通过结构与属性证据校验 | 16 | 修正 bonus 原子验证、明确可选福利状态后完整重跑；没有触发修正调用 |

额外仅做两次同模型回归：Terra 重跑样本 29、30 的资格，两个都通过且提取到 Senior。该两次不计入 v5 主流程的 16 次，也不是全量重复复核。完整此次优化的模型调用数为 17 + 16 + 2 = 35，没有再次派 Astra。

### 关键反例对照（不是整体准确率）

| 核查项 | v3 | v5 |
| --- | --- | --- |
| 29、46 缺少薪资周期 | Sol 和 Terra 均推断 year | 两条 Sol 输出均为 unknown，原金额和 USD/CAD 保留 |
| 22 明确 /yr | 正确 year | 仍为 year，235090–285600 不变；币种未明仍为 null |
| 29、30 Senior，控制同一 Terra 模型 | 两条漏项 | 两条均提取 Senior，title/body 溯源有效 |
| 9 college/university graduated | 未应用用户确认的 Bachelor 归一化 | 提取原话含义，单列 Bachelor user_policy；不是说原文写了 Bachelor |
| 31 标题证据 | Sol 把标题当正文导致被拒绝 | title 与 body 正确区分；该任务通过 |
| 3 最低两年 | >=2，实习排除 | 保持正确，不制造 12 年上限 |
| 22 学历和年限组合 | Master+3 OR Bachelor+5，额外 8 组技能年限 | 关系及全部 8 组保留 |
| 26 工作经验 | required 未量化，preferred >5 | 保持分开，没有把 preferred 变成最低 5 年 |
| 29 preferred 替代关系 | 工作经验 OR MSc OR PhD | 保持 preferred OR，不变成必须研究生 |
| 30 Degree or Diploma | 不指定学位层级的 OR | 保持 OR，没有伪造 Master 要求 |
| 31 毕业/入职/经验 | 入职 Oct 1 前、summer 毕业、<=18 months | 保持区分；summer 不伪造精确日期，经验替代路径保留 |
| 5 条未写 compensation | not_stated | 仍为 not_stated，没有生成数值或 50 分 |

样本 22 可选 bonus/equity 现在均为 record.state=ambiguous，保留 may include / if applicable 原文；不能理解成确定提供。样本 46 的 Software Engineer 3 没有稳定归为通用 seniority；未把公司内部数字级别强行等同 Senior。学历 eq/gte 和事实拆分粒度仍有波动，不从本表推导全部字段一致或生产可用。

新增测试先红后绿：属性证据不存在/不支持金额或年周期、币种仅有 $、标题位置、用户归一化、无必要复核调用、修正再次失败、bonus 合法修饰语、逗号数字格式不能静默缩放。最终限定测试共 **54 passed**，ruff passed，6 个构造合同示例仍通过。

```sh
.venv/bin/python -m pytest tests/unit/test_jd_semantic_contract.py tests/unit/test_demo_jd_contract.py tests/unit/test_jd_attribute_evidence.py -q
.venv/bin/python -m scripts.demo_jd_contract extract --inputs data/jd-contract-pilot-v3/inputs.json --output data/jd-contract-next-run
```

v5 inputs.json 与 v3 inputs.json 直接比较相等。所有输入和逐任务输出在 data/jd-contract-pilot-v5；两份 Terra 定点结果名为 29-terra-seniority-check.json、30-terra-seniority-check.json。human_accuracy 和 production_verdict 仍为 null：这次确实优化并重新实跑，但 8 条已见开发案例不是 500 条独立盲测，16/16 校验通过不等于 100% 语义准确率。

属性校验只保证原子值有支持及引用存在，不证明所有跨句作用范围或漏项；缺失但形式合法的事实不会自动触发修正。原生来源、泛化准确率与生产评分仍须分开验收。本轮只改 demo、合同、对应测试和文档，没有修改生产逻辑、提交或部署。

## 结论边界

合同可以表达并测试核心难例。当前实验不产生真实岗位的生产 eligibility、priority 分数或模型准确率。仍未验证原生来源适配、公司数据、完整 Evidence Fit、生产模型选择、500 条独立人工金标、生产评分的多薪资消费规则。后续若做这些工作，必须分别验收，不能由本轮测试通过代替。

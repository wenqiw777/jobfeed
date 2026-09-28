# 新版 JD parser：50 条扩展测试

## 测试边界

从现有 500 条中排除原来 50 条的全部 job ID，按已有 5 个采样桶各取 10 条、每家公司只取一条。共 50 条、50 家公司。采样桶不是内容标签；例如 no_signal 桶仍包含明确年限。这不是随机总体样本，也不是人类独立裁定的盲测金标。

旧 500 条有 2000 个字段、512 个模型决策分歧，未找到完整裁定结果。本轮不把模型一致当正确，不导入旧处理流程。直接读数据库保存的全文；保存本次输入，未验证实时网站的采集完整性。

固定上一轮 parser，运行资格与薪资两个任务。先保留首轮错误；验证失败最多一次 Terra 修正。主任务读取全部 50 条原文，再检查输出。报告结构校验、关键语义核查、待裁定问题三个不同层面；不声称是人类标注，也不输出生产分数。

## 原文核查要点

下表由主任务阅读全文记录，作为输出核查依据，不是独立金标。毕业栏同时检查在读与入职时间是否混为毕业窗口。经验栏不把未量化经验填成 0。薪资未明确币种或周期时保留未知。

| ID | 毕业/在读 | 经验 | 学历 | 薪资要点 |
| --- | --- | --- | --- | --- |
| 49 | 无候选人毕业窗口；graduates 是薪酬复核对象 | 领导/开发经验，未量化 | Bachelor/equivalent，Valued 段落 | base 未量化；annual bonus；2000 学习预算不能算工资 |
| 50 | 2025 或 2026；已获或在读 | 以往 SWE preferred；ML infra explicitly not required | BS/MS/PhD OR | US 97600–139000 USD；Toronto/Vancouver 125320–142783 CAD；其他 Canada 116965–133264 CAD；无周期 |
| 51 | post-graduation 是经验限定，不是毕业日期 | >=5 毕业后；两个 >=3 ML 领域要求 | BS/MS/PhD；其他科学专业伴随 substantial engineering experience | 未写 |
| 52 | recent/early-career 为岗位描述；3 个月是任期 | 未量化；模拟实习/项目 preferred | Bachelor OR equivalent practical experience | Denver 75000、Long Beach 80000 base；币种/周期未知 |
| 53 | 毕业 2025/2026 | AI tools 未量化；LLM preferred | four-year CS degree | 125000–140000 annual base；币种未知；可选 equity |
| 54 | 在读；Fall 2026 是实习时间 | 编程未量化；RF/RTOS preferred | pursuing BS/equivalent | 29 USD/hour base |
| 56 | 在读；May/June–Aug/Sept 2027 实习时间 | experience OR strong interest | pursuing Master OR PhD | 45–51/hour；币种未知；不可把 4–12 周 parental leave 算经验 |
| 57 | pursuing OR equivalent experience；June–Aug 2026 实习 | 未量化 | degree 不限定层级 OR experience | 未写 |
| 59 | recently graduated OR enrolled graduating 2026 | exposure 未量化，若干 preferred | university program 未指定层级 | 未写 |
| 60 | 在读 | ML 经验未量化 | BS/MS/PhD OR | 50–70/hour，base+variable targets；币种未知；不是纯 base |
| 157 | 未写 | 未量化；不把公司 1B annual bookings 当工资 | BS/MS/PhD | 130000–280000 USD annual OTE；senior-staff 标题与 early-career 描述冲突 |
| 158 | 未写 | 编程 >=10，Kubernetes >=5 | 未写 | cash 163000–204000/year Denver等、197000–247000/year SF等；币种未明确；equity |
| 159 | 未写 | required >=12；30 天部署不是年限 | Bachelor CS | 6000–11000 USD/month gross，非年薪 |
| 160 | 未写 | ML >=4 | Master OR PhD | 未提供金额，不能补薪资 |
| 161 | 未写 | required 未量化；>=8 excluding internships 是 preferred | Bachelor OR comparable experience；教育/训练/经验替代 | 320000–485000 USD annual；OTE 声明仅用于 sales，不能套本岗 |
| 162 | 未写 | >=5 scientific engineering software | Master OR PhD required | base 162800–217600，币种/周期未知 |
| 164 | 未写 | Bachelor+6 OR Master+3 OR PhD+1 | 学历与年限同分支 | 177300–212800 USD，周期未知；bonus/stock；条件 sign-on |
| 165 | 未写 | >=5 SWE AND >=3 security AND 两个 >=1 AI 领域 | B/M OR equivalent industry experience | 58250–69750 EUR Bulgaria base，周期未知 |
| 166 | 未写 | >=8 SWE | BS/BA | 195000–250000 USD/year base；可选 equity；sales incentive 条件 |
| 167 | 未写 | >=5 product marketing；非 SWE 年限 | 未写 | 无金额；competitive salary 仅定性 |
| 247 | 未写 | backend required 未量化 | PhD 是员工背景，不是候选人要求 | competitive compensation 仅定性 |
| 248 | 未写 | >=5 SWE | B/M/doctorate OR equivalent experience | 15000 是推荐奖金给推荐人，不是候选人工资 |
| 250 | 未写 | embedded 未量化 | B/M/PhD OR | 未写 |
| 251 | 未写 | >=5 production ML | B/M/PhD | I4 137100–201600、I5 167800–246800、I6 203500–299300 USD base；周期未知；PTO 的 per year 不能借用 |
| 252 | 在读；Spring/Summer 2027 实习 | 已做项目/实习，未量化 | degree CS/equivalent 未限定层级 | 8500/month 工资；2500/month 住房、70/day 餐费必须区分，不算 base |
| 254 | 未写 | >=6 professional C++/Rust | MS/PhD 只是 preferred；industry or school 无必须学位 | 220000–292000 USD base，无周期；通常 equity 有条件 |
| 255 | 未写 | 5–8 SWE，至少保留原始范围 | Bachelor OR equivalent experience | E04 122430–168340，E05 141380–194390，另总范围 122430–224500 USD；不丢范围、不选最高 |
| 256 | 未写 | 未量化 | BS or higher | 未写 |
| 261 | 未写 | 2–5 backend；ML research explicitly not needed | BS/MS/PhD OR work experience | 未写 |
| 262 | 未写 | >=5 distributed systems | advanced degree 只是 bonus；层级不明确 | 只链接外站，无正文金额 |
| 357 | 未写 | 3–8 SWE | 未写 | 无工资数额；referral bonus 不是候选人 signing bonus |
| 358 | 未写 | >=6 building products/platforms | 未写 | 180000–250000 base，币种/周期未知，equity |
| 359 | 未写 | >=8 distributed systems | 未写 | 180000–230000/year + equity，币种未知 |
| 360 | 未写 | distributed/backend 未量化 | Research Engineer 不等于必须 PhD | 200K–400K，base+equity上下文；币种/周期未知 |
| 362 | 未写 | >=5 SWE/infra/SRE/production | 未写 | 161300–241900 USD；组成/周期未明确 |
| 363 | 未写 | >=5 data pipelines | 未写 | 三个 zone base USD：196000–230000、172000–202000、153000–179000；周期未知；bonus/equity |
| 365 | 未写 | >=5 leadership AND >=2 managing managers | BS/MS or equivalent | 246600–369800 USD base，周期未知；可能 bonus |
| 366 | 未写 | backend/ML 未量化；6 个月是申请冷却期 | 未写 | 180000–250000 USD annual，组成未明确 |
| 367 | 未写 | 必须带过consumer embedded团队，未量化 | 未写 | 290000–350000 USD annual；ESPP/referral 不应变成招聘奖金 |
| 368 | 未写 | >=4 SWE | 未写 | 124900–228900 USD base，周期未知；PTO 年份不能作为工资周期 |
| 447 | 未写 | >=3 iOS；6/12 月是入职后成果 | BS/MS/equivalent | 未写 |
| 448 | 未写 | >=12 professional SWE | 未写 | 未写 |
| 449 | 未写 | 未量化 distributed systems | 未写 | 未写 |
| 450 | 未写 | Minimum 5–7，最低 5；上界语义有歧义 | 未写 | 未写 |
| 451 | New Grad 2027 仅标题，不等于明确 required 毕业年 | 未量化；创始人 18 年不是候选人要求 | 未写 | 没有工资数额，eligible equity 有条件 |
| 452 | 未写 | Desired distributed/backend，未量化 | 未写 | 350m 是融资，不是薪资 |
| 453 | 未写 | 未量化；公司 3 decades 不是候选人年限 | 未写 | 未写 |
| 454 | 未写 | professional SWE 未量化 | 未写 | 2016/2022 为公司年份，不是毕业窗口 |
| 455 | 未写 | perception/industry 未量化 | Research Engineer 不等于必须 PhD | 未写 |
| 456 | 未写 | >=15 full-stack | 未写 | 未写 |

## 运行与核查进度

- [x] 50 条 / 50 公司已保存，排除原 50 个 job ID。
- [x] 主任务已读取全部 50 条保存的原文，记录上述核查点。
- [x] 完成 100 个模型任务及一次失败修正（若需要）。
- [x] 对照原文核查所有资格与薪资输出，分开记录明确错误与歧义。
- [x] 汇总首轮、修正后、拒判和语义核查结果。

输入及运行目录：data/jd-contract-expansion-50-v1。当前不改生产、不提交、不部署。

## 实跑结果（2026-09-04）

冻结 parser 后完成 50 条 × 2 任务，Sol 首轮 100 次、Terra 校验失败修正 8 次，共 108 次调用。没有全量第二模型复核，没有再次派 Astra。

| 任务 | 首轮校验通过 | 修正次数 | 修正通过 | 最终校验通过 | 未解决 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 资格 | 50/50 | 0 | 0 | 50/50 | 0 |
| 薪资 | 42/50 | 8 | 4 | 46/50 | 4 |
| 合计 | 92/100 | 8 | 4 | 96/100 | 4 |

**96/100 是结构及属性证据校验通过，不是准确率。** 本轮逐条进行的是主任务 AI 原文核查，没有独立人类裁定，不能给出已验证的总体准确率。50 条均不同于原开发 50 条，但来自已做过模型标注的 500 条池；不是全新独立盲测。通用 runner 的 summary.json 沿用旧的 “Previously discussed development cases” 固定说明；该句不描述本轮抽样，实际边界以保存的 cohort.json 和本报告为准。

四个未解决任务均为 salary：251 DoorDash 修正后拼接了不连续的原文引用；359 Sift、363 Robinhood 仍触发组成属性校验；368 Trade Desk 修正后用空字符串表示未写地点，未通过地点校验。数值可读不等于整个输出可接受；所有首轮与修正失败均保留。

## 发现与处置

以下均未在本轮修改 parser，以保留扩展测试基线；不是已修复清单。

| 类别 | 样本 | 证据与影响 | 后续处置 |
| --- | --- | --- | --- |
| 修正引入遗漏 | 50、157、164、254 | 首轮包含原文明写的 equity/stock，修正后通过校验但对应记录消失；部分还丢 bonus | 修正必须保留不受错误影响的事实，验证完整性，不能仅追求合法输出 |
| 合同表达不足 | 252 | 原文 8500/月工资、2500/月住房、70/日餐补；金额保留，但没有 day/allowance 类型，餐补变成 unknown 周期 | 扩展周期和组成类别，补贴与工资分开；不是强行推算年薪 |
| 条件未结构化 | 357 | referral bonuses 成为普通 bonus/present，只有原文还保留 referral | 保留适用条件或明确类型；present 不能被消费者理解为无条件发放 |
| 替代路径遗漏 | 447 | 原文 BS/MS or equivalent；equivalent 事实被标为 waived 且未被 required 树引用，只剩 BS OR MS | 保留明确替代分支；不能用孤立 waived 事实代替关系 |
| 范围过度确定 | 450 | Minimum 5–7 years 被拆成 required >=5 AND <=7，均 state=present | 最低 5 保留；上界不能当成明确最高限制 |
| preferred 关系错误 | 162 | 整段独立 Bonus qualifications 被连接为一大组 ANY_OF | 保留每条偏好及其内部 OR，不把整段压成任选一条 |
| 在读/已获消费边界 | 56 等在读职位 | pursuing Master/PhD 被表示为 degree AND enrollment；现有 CandidateScenario 不能独立表示最高已获学历和当前在读层级 | 补候选人在读字段和学历条件绑定后再验证 gate；本轮未证明发生真实错误 Block |
| 校验词表/修正问题 | 251、359、363、368 | 仍有正常表述被拒绝，或修正引入引用/空值错误 | 对各失败保留原文反例，局部修正再重跑；不得通过删除有效记录绕过校验 |

待裁定而不强判错误：49 的 Valued 段落、53 的 Ideal Candidate 段落如何映射 required/preferred；59 的 recently 与毕业年绑定；262 的 advanced degree 层级归一化。词句存在及 quote 校验不能解决这些歧义。

正例：50 的三地区金额/币种未合并；54 的 29/hour、159 的 6000/month 未被年化；161 的 8 年仍是 preferred 且排除实习；247 员工 PhD、455 Research Engineer 标题未变成候选人学历要求；248 推荐人的 15000、49 学习预算 2000 未当工资。451 未将创始人 18 年经验或标题 New Grad 2027 编成明确候选人毕业窗口。

## 可重复验证

新增 scripts/probe_jd_contract_expansion.py：只读保存输出，检查 16 个原文核查后的定向断言。实际 **8 通过、8 失败，退出码 1**。失败为 4 个 equity 遗漏、日餐补表达、推荐奖金条件、同等资格路径和经验上界。它是针对已发现问题选出的反例与正例，不是 50 条总体正确率；8/16 不能解释成准确率 50%。没有 accepted 输出时直接报错，不能把缺失当成检查通过。

运行：

```sh
.venv/bin/python -m scripts.probe_jd_contract_expansion data/jd-contract-expansion-50-v1/run
.venv/bin/python -m pytest tests/unit/test_jd_semantic_contract.py tests/unit/test_demo_jd_contract.py tests/unit/test_jd_attribute_evidence.py tests/unit/test_prepare_jd_contract_expansion.py -q
```

现有相关单测 **56 passed**；这些单测没有覆盖全部真实语义，所以不能抵消上述失败。抽样脚本及定向检查脚本另做 ruff 检查。

结论：扩展测试已完成，暴露了真实语义遗漏和合同缺口。该版本仍不能直接用于自动 Block 或已验证的 priority 评分。下一次优化先用这些失败作回归，再增加未参与调参的样本；不把本轮曝光样本继续称为盲测。

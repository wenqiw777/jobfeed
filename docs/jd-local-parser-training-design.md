# JD 本地语义解析器：最小训练设计

日期：2026-09-05。状态：已完成最小可行性实验，不改生产。

## 选择

使用现成的结构化抽取模型进行 JD 专项微调，不自行设计新的模型架构。

首选验证 GLiNER2 的本地模型与官方训练接口。官方实现已提供实体、分类、结构化记录和关系抽取；它与此前测试的 GLiNER small v2.1 纯 NER 基线不是同一个模型。
实际验证 checkpoint：fastino/gliner2.5-base-v1。已完成任务输出、LoRA
forward/backward、adapter 保存和留出样例验证；不能因为模型支持结构化抽取就假设它已懂
JD 条件。
[官方代码与说明](https://github.com/fastino-ai/GLiNER2)

此次选型替代前稿“自己给 ModernBERT 加文档 Transformer、树解析器和多个自定义任务头”的方案。保留 ModernBERT 为未来有实证需要时的备选，不同时开发两套。

## 验证后采用的最小流程

完整 JD → evidence 候选 → 本地语义分类 → 条件关系 → 已有事实合同 →
既有资格/评分消费者。
本地解析确有疑点时，交给已有 Luna 复核。
本轮只验证到事实合同，不接入生产资格或评分。

第一次实验尝试让一个结构化记录同时生成 evidence、kind、modality、subject 和
OR 路径。这个表示在 22 条训练 case、80 steps 后仍为 0/12 严格命中，并从 12 个
假记录增加到 39 个；choice 字段始终没有学会。因此不继续使用这条单体路径。

第二次实验把已知 evidence clause 单独交给语义分类器，不让分类器同时解决 span
边界和关系分组。25 个训练 record、17 个留出 record、40-step LoRA 后：

- 完整 record 全部标签正确：5/17（29.4%）→ 10/17（58.8%）。
- 单标签正确：26/45（57.8%）→ 37/45（82.2%）。
- kind：7/11 → 9/11；modality：5/11 → 8/11；subject：6/11 →
  10/11；salary component：2/6 → 4/6；beneficiary 保持 6/6。

这个结果只证明“分开抽 evidence、再做局部语义分类”有学习潜力。样本是人工控制的
小样例，evidence 还是 gold candidate，因此不是端到端准确率，也没有达到生产标准。

模型只有两类输出任务，不需要两套完整权重：

1. 资格：degree、YOE、毕业/在读、岗位级别，连同 required/preferred、对象、替代关系和证据。
2. 薪资：金额、币种、周期、组成、适用地区和条件，分别关联成记录。

不让模型直接生成 priority 或 Block。Company Strength 和完整 Evidence Fit 不在此次训练范围内。

## 复用什么

- 已有 JD 输入、来源完整性标记、证据位置。
- scripts/jd_semantic_contract.py 的事实、AND/OR 和未知状态语义。
- 现有 Luna 复核入口与回归案例。
- 已配置好的 3080；使用独立实验目录，不覆盖另一项 SDE 实验。

只增加模型输出到事实合同的薄适配器，不重建采集平台、数据库或训练平台。
合同确实不能表达的日补贴、推荐人奖金等，按真实样例最小补齐；不能静默丢字段。
不新增任何 hash 相关机制。

## 条件怎么表示

例如 Bachelor + 3 years OR Master + 1 year，训练标签必须保留两条替代路径，而不是四个互不相关的值。
实验确认不能把路径标成任意的 A/B 类别。下一步只训练 evidence 之间的
`alternative_to` / `combined_with` 关系，再适配到已有 AND/OR 树；分组由模型提取，
适配器不能按关键词自行猜关系。
第一步用少量已裁定真实难例验证这种映射能无损表达。复杂跨段嵌套无法表达时保留疑点并复核，同时记录其出现比例。

不会把所有 OR 默认交给 LLM；常见学历+经验替代条件是本地微调必须改善的目标。
若现成模型的结构确实限制高频需求，再针对这个限制改设计，不提前建设通用树解析器。

## 数据与训练

先整理已有样本，不先承诺标一万条。
从旧开发材料中准备约 200 条裁定训练样本，以及按公司/模板隔离的约 100 条开发样本；数量服从分组隔离。
标注包括最终事实、原文位置、required/preferred、对象和必要关系。旧双模型一致项不能自动当正确答案。

### 按来源能力选测试样本

Greenhouse、Lever、Ashby 的公开 API 可以稳定提供标题、地点、正文等来源事实，
部分还提供结构化薪资；但它们没有通用的“最低学历、最低 YOE、毕业窗口”字段。
Greenhouse 的 `education_required` 表示申请表是否要求填写教育经历，不是岗位要求的
学历等级，不能拿它自动 Block。

所以来源必须按字段处理：

- ATS 已提供的薪资、地点、employment type：直接使用 native field，不交给文本模型
  重猜。
- ATS 正文中的 degree、YOE、graduation、required/preferred：仍属于语义解析范围。
- parser 的下一轮 200 条压力测试不从 Greenhouse、Lever、Ashby 原生 API 结果抽取，以免正文格式和
  native salary 让结果虚高。
- 200 条全部限定为软件工程/SDE 类岗位；其中 100 条来自没有可用 Greenhouse、Lever、Ashby
  原生 API 结果的官方职位页，100 条来自 LinkedIn，并确认当前语料库中没有相同
  company/title 的官方全文副本。
- “只有 LinkedIn”只能先表示本语料库未找到官方副本，不能声称互联网上绝对不存在；
  冻结样本前按公司、职位、地点再查一次官方站点。
- 官方页面即使长得像公司自建站，也必须做 capability detection；例如自有域名可能
  仍由 Greenhouse 托管，不能只根据 hostname 分类。

已冻结 200 条完整 JD：100 条 official-unstructured、100 条 LinkedIn-only-in-current-corpus，
共 191 家不同公司。官方组包括 Workday、iCIMS、SmartRecruiters 和官方 JSON-LD；这类来源
可能有页面结构，但没有我们需要的通用 degree、YOE、graduation native fields，因此仍是
正文语义解析测试。样本平均 5,309 字符，最长 12,566 字符，不使用摘要或截断文本。

### 200 条 raw-JD evidence baseline

`fastino/gliner2.5-base-v1` 使用库自带 long-text chunk/overlap 在 Apple MPS 跑完 200/200：

- graduation：27/200 有候选，38 个候选。
- YOE：142/200 有候选，230 个候选。
- degree：167/200 有候选，608 个候选。
- compensation：151/200 有候选，347 个候选。
- 1,061,882 字符共耗时 374.7 秒，即 0.53 JD/秒。
- 所有返回 span 都通过原文 start/end 逐字校验；未检出仍保持 `no_detection`，不解释成字段不存在。

这只是召回层运行结果，不是准确率。对每个字段均匀抽取 12 个命中样本做第一轮人工
screening，明显正确的候选分别只有 graduation 4/12、YOE 8/12、degree 8/12、
compensation 9/12。典型误报包括实习起止日期、`Mon-Fri`、公司成立 40 年、入职后六个月、
`IEC`、福利中的学历、401(k) 和融资金额。对 no-detection 做明确词面信号复核，又确认至少
漏掉 3 条 graduation/new-grad 信号、3 条 YOE 和 1 条 Bachelor requirement。

因此该 base model 可以继续作为 evidence-candidate 召回层验证，但不能独立输出事实、分数或
Block。下一步应裁定这 200 条的 gold spans，并训练/验证 span recall；候选随后必须经过已验证
有潜力的 clause classifier，再处理 required/preferred、对象和替代关系。

用官方训练接口在 3080 微调，先跑短 JD 和长 JD 的 forward/backward、保存重载与全文覆盖检查。
不默默截断长文；使用库提供且验证过的长文处理能力，超出能力明确记录。
在相同开发样本上比较训练前后，分别报告实体、属性、关系及完整字段结果。
有改善但仍受数据覆盖限制时扩到 400/500 条继续测；错误集中在关系时补关系难例，不无条件扩总量。

开发集用于优化，不能称为盲测。模型和配置稳定后，再准备独立裁定、未参与调参的最终测试集；不在本轮先建设大规模标注系统。

## 未写与不确定

- 没写薪资：保留未写，不补 0、50、币种或年薪。
- 写了金额但没写周期：保留金额，周期未知。
- 没检出不等于没写；训练字段状态，并审查缺失类的漏检。
- 正文不完整：重新获取来源，不让 LLM 猜缺失内容。
- 模型确实拿不准、关系冲突或拟 Block：按已有规则交 Luna；复核不能凭空补来源事实。
- 保持现有拟 Block 必须复核的政策，分别测它与解析疑点的调用成本，不预先承诺总体低于 10%。

## 当前结论与下一轮

当前应保留这个简单拆分，不造新的 Transformer 或通用树解析器：

1. span 模型只找 verbatim evidence；未检出保持未知。
2. clause classifier 判断 kind、modality、subject 和薪资 component/beneficiary。
3. relation extractor 只判断 evidence 之间的替代/组合关系。
4. 任一关键字段低置信、冲突或拟 Block，再交 Luna。

下一轮应把 25 条人工训练 record 换成这 200 条真实裁定 record，并新增独立的
relation 留出测试。生产门槛必须在另一个未参与优化的真实 JD 测试集上决定，不能拿
这 17 条结果外推。

实验代码：`scripts/demo_jd_gliner2_potential.py`、
`scripts/demo_jd_gliner2_clause_classifier.py`、`scripts/prepare_jd_gliner2_200.py` 和
`scripts/demo_jd_gliner2_200.py`。200 条输入与 baseline 结果保存在
`data/jd-gliner2-200-v1/`。未重写生产 parser，也未部署。

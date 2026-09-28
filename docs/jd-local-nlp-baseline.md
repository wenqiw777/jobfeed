# 本地 JD NLP 基线与 GPU 入口

## 本轮结果

已完成 GLiNER small v2.1 对固定 50 条 JD 的零样本实体抽取实验。
没有 Sol/Luna 调用，没有训练，也没有接入生产 eligibility 或 priority。
这验证了本地推理成本与初始错误类型，不证明已经能可靠解析完整资格逻辑。

- 输入：`data/jd-contract-expansion-50-v1/inputs.json`。
- 模型：`urchade/gliner_small-v2.1`，阈值固定 0.3，允许重叠和多标签实体。
- 独立环境：`data/jd-nlp-runtime`；gliner 0.2.21、torch 2.14.0、transformers 4.57.6。
- Mac CPU，4 线程，batch 4；加载 3.901 秒；50 条推理合计 31.923 秒。
- 全部 title/body 分开处理，共 441 个窗口；1000 字符窗口、300 字符重叠。
  检查模型 word budget，不允许静默截掉窗口尾部；原文偏移直接验证。
- 输出：`data/jd-local-ner-50-v1/`，每条独立 JSON 与 summary.json。
- 7 个单元测试通过，ruff 通过。先记录模块不存在的失败，再实现并验证通过。

本轮为已保存文本的实验，不是新抓取 50 个官网，也不是独立盲标准确率测量。
模型加载时有 fast tokenizer byte-fallback 警告；不据此声称所有特殊字符都无损建模。

## 检出统计，不是准确率

| 标签 | 有检出的 JD / 50 |
| --- | ---: |
| graduation date | 10 |
| years of experience | 27 |
| education degree | 36 |
| salary amount | 30 |
| salary currency | 29 |
| salary payment period | 1 |

没有检出保持 `no_detection`，不是 `not_stated`。检出保持 `detected_unverified`，
不是满足要求。模型 score 未校准，不可拿来当作正确概率或 priority 分数。

## 原文与输出定点核查

主任务对照原文检查下列诊断样例；不是独立人类金标，没有据此调整阈值重跑。

| 样本 | 实际观察 |
| --- | --- |
| 50 | 检出 2025/2026 和 BS/MS/PhD；salary amount 漏 US 139000、Toronto/Vancouver 142783 上界；还把 experience 标成金额 |
| 53 | 检出毕业年份和 four year degree；把 Competitive salary 标成金额；未检出 annual 周期 |
| 56 | 把标题 Summer 2027 标成毕业日期；唯一检出 payment period 的 JD，检出的却是实习日期，不是原文 per hour |
| 159 | 检出 6000/11000、USD、12+ years；漏 per month；部分金额同时被标成年限，12+ years 同时被标成金额 |
| 247 | 检出公司员工背景中的 PhD，甚至 Stanford 被标为 degree；没有候选人学历要求判定能力 |
| 248 | 把公司 nearly two decades 标成年限、推荐人 15000 奖金标成工资金额；也检出了真正的候选人 5+ years |
| 251 | 未把所有 salary 上界识别为金额，有些上界只被标成 currency；不能恢复完整的多个职级薪资记录 |
| 252 | 检出了工资、住房和餐费金额，但没有区分组成或关联月/日周期 |
| 450 | 检出完整 Minimum 5–7 years 原文，但没有最低要求或上界语义解释 |
| 451 | 检出标题 New Grad 2027；同时把创始人 18 years 标成年限，不能用作候选人要求 |
| 454 | 没有检出公司年份为毕业日期；这只是此样例未出现该错误，不是普遍保证 |

结论：本地推理可行且这轮很快，但这个固定零样本 NER 配置不能直接承担 parser。
不仅缺上下文关系，金额/币种/周期实体本身也不够可靠。NER 输出后的机械数值归一化
不能补回这些漏检，模型换成 ML 不会自动解决语义问题。

## 后续训练如何决定样本量

不先承诺“一万条就足够”，也不要求积累到一万条才能开始。
先确认统一的 span、subject、required/preferred/negated、AND/OR、金额属性关联标签，
裁定一批高质量样本；现有模型双标一致并不自动构成人类金标。
学历、YOE、毕业日期与薪资要分别报告实体与关系表现。

建议训练样本规模逐步做 200/500/1000/2000 的学习曲线，再决定是否扩至 5000/10000。
这些是后续实验规模，不是已完成标注或已验证足够的数据量。
按公司/模板隔离训练与测试；开发诊断集不得继续冒充未见测试集。
按每类真实证据和难例覆盖度计数，一份 JD 可以贡献多条实体/关系标注。
缺失薪资、公司介绍、推荐奖、实习日期、preferred 与替代路径都要有负例。
小模型校准后的不确定结果才选择性升级；不能预先保证升级比例一定很低。

## 已配置的 3080

连接说明来源：`data/sde-gpu-experiments-v1/HANDOFF.md` 的 3080 desktop 小节。
该文件在 Git 忽略的 data 目录中，普通 rg 搜索可能跳过。

```sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 wenqiw777@192.168.1.104
```

本轮只读连接已确认：DESKTOP-RVJ9QNC；RTX 3080，10240 MiB 显存。
GPU 工具是 `/usr/lib/wsl/lib/nvidia-smi`。
现成 Python 是 `/home/wenqiw777/sde-modernbert/.venv/bin/python`。
本轮实测 Python 3.12.3、PyTorch 2.7.1+cu128，`torch.cuda.is_available()` 为 True。
这是另一项 SDE 二分类实验的环境，不把它的已训练模型或标签误当本轮字段抽取模型。
保留其任务、模型和 CUDA 进程；本轮未启动、停止或重配任何远端训练。

## 重跑

使用不存在的新输出目录，避免覆盖本轮原始结果：

```sh
data/jd-nlp-runtime/bin/python scripts/demo_jd_local_ner.py \
  --input data/jd-contract-expansion-50-v1/inputs.json \
  --out data/jd-local-ner-50-v2 --device cpu
```

模型依据：[官方 model card](https://huggingface.co/urchade/gliner_small-v2.1)、
[GLiNER 论文](https://aclanthology.org/2024.naacl-long.300/)。
论文的通用 NER 结果不替代本项目实测。

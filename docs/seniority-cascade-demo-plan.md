# 规则 + Luna 离线demo

目标：验证规则明确结果直接处理、unknown交给Luna、失败/不确定放行的执行路径。复用最近72小时1000条冻结样本与既有Luna标注；禁止生产写入。

完成：规则394 keep / 6 block / 600 unknown；最终955 keep / 45 block。放行池对原Luna block残留16条（1.68%），56条参考标签未解决。不是独立验证，不宣称3%达标。

验证：先记录缺失模块失败；实现后6测试通过；多职级回归先失败，修复后7通过。全部ID和拦截证据检查通过。误杀修复包括Member of Technical Staff和多职级Principal广告。低年限冲突保护过宽的问题记入报告；生产缓存/独立新样本人工验收未实施。

产物：artifacts/seniority-demo-recent3d-1000-20260918/cascade-demo/REPORT.md。

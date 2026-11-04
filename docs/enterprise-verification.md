# 验证证据与边界

证据目录：`reports/enterprise/20261023`。最终以summary.json与JUnit实际统计为准。

- 真实StarRocks3.5.21＋PG17、独立QA库；500表合成目录，197105行确定性源资产。原3202订单/205397行历史资产和成绩保留，两套不能混算。
- 7指标37批准问题逐题执行；净销售额独立Decimal累计，其他指标由独立raw-record Python oracle核对。没有用模板SQL结果作为自身标准答案。
- 75项Linux/Python3.12非root/只读根后端回归通过，身份/生成/Embedding用MockTransport协议桩；没有调用真实27B/BGE服务。
- 前端30项通过，含持久任务轮询/取消、PKCE、Decimal图表边界。headless字体测量为桩；企业浏览器和SSO未联调。
- 原始首轮10失败、流截断失败、PG权限失败、Linux间接旧依赖失败没有改写为首次全过。first-run.xml/expanded.xml/role-vector-contract.xml保留过程证据；最终Linux报告单独统计。
- 数据库只读/R1实际隔离、幂等冲突、撤权、attempt/租约、双进程领取、物理取消与结果截断通过；限条件结果不外推生产吞吐。
- 小规模增量更新/删除、不可变SR物化、对账、发布/回滚通过；不是已接真实ERP/Flink，也不等于真实企业区域CDC已接通。
- 独立PG备份恢复记录于restore-proof.json；不是生产HA灾备演练。

## 尚未评分

真实27B三档冻结问答、首次/修复后准确率、BGE召回、GPU容量、生产数据新鲜度SLA、目标并发P95/P99、节点故障/StarRocks全量恢复均未运行，不生成预测分数充作成绩。模板路径、模型路径、澄清和权限题分开；37/37不等于任意问法100%。

## 复核命令

`pytest enterprise_tests -q`必须先明确QA确认变量；`npm run build && npm test`在web目录；正式镜像使用deploy/enterprise/Dockerfile。依赖审计使用npm官方registry，镜像源安全审计接口未实现不能解释为零漏洞。

历史tests依赖与新运行依赖分离，历史406项已在全新*_qa PG与独立文件目录完整回归通过，仅兼容资产验证，不作为正式分析引擎验收；未弱化或删除旧失败断言。正式验证的source hash包含未提交工作，不只记HEAD。

正式API冻结评测工具：`scripts/evaluate_enterprise.py`，输入独立JSONL参考、difficulty与source_kind；只记录首次结果、单独检查generation_mode，缺参考拒绝数值评分。模型revision必须由实际部署方确认。工具自身3项Linux无网络测试通过，不表示真实模型评测通过。


## P1/P2后续实施（2026-10-23）

已按授权继续独立工程部分：服务端结构化意图/批准SQL结构与7指标输出校验、同发行快照对账恢复/隔离/未applied禁止发布、低基数阶段观测与独立监控身份、前端图表懒加载、授权与发行绑定追问、持久反馈人工审核、默认关闭的内网独立语义复核、检索/复核对照与候选版本清单。

最新正式后端89项、工具92项、前端31项分别通过；完整Linux报告181项含重叠工具子集，不与独立工具报告累加。真实StarRocks/PG已验证，真实模型/IdP/ERP/平台仍未接；没有准确率、容量或微调收益结论。P1复杂口径/真实检索与平台浏览器验收、P2真实消融/训练数据与实际微调仍待条件。见23号交付记录及仓库docs/enterprise-p1-p2-delivery.md。P0原13项外部验收状态保持不变。

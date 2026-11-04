# 问数批准意图路由与生成契约修复（2026-10-31）

## 已交付

1. 正式 Query 在 derive 完整校验后，以指标ID＋分组＋明确月份匹配当前发布的唯一批准编译计划。精确原句和等价问法都可复用模板。未知月份不会退化为全量快照，未知筛选和未批准复杂口径仍先澄清；指标启用、授权、SQL AST/语义、执行资源和结果校验全部保留。context仍先按权限获取，不通过客户端指定模板。
2. 响应继续使用 approved_template/local-chat 两类 generation_mode，并新增 routing.reason=exact_question/validated_intent_signature/model_required 和 model_bypassed，明确记录路由原因。模型路径评测可显式关闭新路由，避免用模板成绩冒充模型成绩。
3. Settings新增 approved_intent_routing=True；环境变量 LINGXI_APPROVED_INTENT_ROUTING 仅接受true/false，不合法值失败。部署默认为开启；关闭用于诊断/模型对照。
4. metric-execution-v2 给出服务端批准的逻辑投影、别名、CTE、关联、过滤、分组、排序结构，并与规则、schema和编译SQL共同哈希绑定。原校验要求特定批准结构，现把该要求明确提供给生成器，避免要求模型盲猜。只有逻辑编译结构，无执行结果、物理映射、凭据或评测答案。快照纳入规则仍需真实Owner审批。
5. 离线率公式统一为已允许的SUM(CASE...)，生成提示明确不使用COUNT_IF；白名单未放宽。纠错反馈明确对齐结构，不只泛称“语义错误”。仍最多一次修复、共享45秒模型预算，权限/网络/未知基础设施错误不重试。

## 验证

298项相关离线回归通过（包含旧模型路径对照，新增模型路径 fixture 显式关闭意图路由）；另完成53题真实本地隔离StarRocks核对：批准原句37/37，完整意图识别的等价问法8/8，澄清边界8/8；模型调用0次。后者参考为批准编译SQL，属于工程一致性核对，非独立业务真值。执行身份和控制面仍为合成测试替身，不是完整SSO/API/worker验收。

原始脚本的历史case.kind=model表示来自原8题模型开发集，本轮实际generation_mode全部为approved_template，不能把原始summary.model=8/8引用为模型准确率。delivery.json已用validated_intent_paraphrases单独标注。

未命中注册计划的查询仍进入local-chat，受新增结构契约约束；新增生成约束仅完成离线协议和安全回归，服务器关闭期间未验证真实27B输出改善。旧8题自由生成0/8结果保留，旧380题基线不变。

## 下一步

下次GPU可用先用 LINGXI_APPROVED_INTENT_ROUTING=false 或未登记但已批准的年月问题复测模型路径。分别统计模板路由、首答、一次纠错后、追问和超时；不能将路由收益当作模型或微调收益。真实BGE、模型文件指纹、独立业务预期值和完整企业身份链路仍待补齐。

本次不需要GPU、未微调、未Git提交或推送。权限校验没有放宽，也没有强制执行被拒绝的旧生成SQL。

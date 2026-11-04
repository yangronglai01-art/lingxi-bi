# 问数30天指标有限等价修复（2026-10-31）

## 修复内容

新增enterprise.cohort_equivalence，在原批准规范化结构比对失败后，仅对服务端编译参考产生最多2个额外结构，不重写或自动执行任意用户SQL：

- 仅activation30/service30，且支付日和事件日元数据均为DATE；固定30天DAY/DAYS单位归一化，避免SQLGlot对日期常量折叠与类型包裹出现不一致。没有全局删除CAST，不接受DATETIME/TIMESTAMP/字符串类型的同类假设。
- 仅编译参考中单列device_id的outcomes CTE、唯一LEFT JOIN消费者、外层全部聚合为COUNT(DISTINCT)，且没有其他敏感消费者/HAVING/QUALIFY/窗口时，允许事件关系不在CTE内部提前DISTINCT。最终候选SQL仍须与完整参考替代结构一致。
- 原SQL只读/权限、批准关联、成熟观察窗、0至30天事件窗口、分母保护、资源预算和结果检查全部保留。不删除结构校验，不按当前数据结果相同直接放行任意SQL。
- 校验结果增加limited_equivalence标签，仅在有限等价匹配时返回，便于审计。

## 验证

328项相关回归通过，含30项新测试：保存4题首答、DAY日期表达式、丢失外层去重、COUNT/SUM替换、29/31天窗口、排除第0天、内连接缩小分母、月份错误、未成熟设备、零分母规则变更、百分数单位错误、非DATE类型和多消费者限制。

使用原16题真实模型的已保存首答，经过修复后的正式Query编排和真实隔离StarRocks执行，16/16全部通过，均只读取第一份保存响应，无纠错。4条此前误拦的首答另已独立Python原始行计算核对一致。

这属于保存输出的离线回放，不是重新调用模型，不是新的16题真实模型准确率。历史端到端12/16和原始超时/修复耗尽证据保持不变。本次GPU调用0，回放耗时不能当作真实模型推理延迟。

## 文件与限制

代码：src/enterprise/cohort_equivalence.py、src/enterprise/intent.py。
测试：operator_tests/test_cohort_equivalence.py及fixtures/cohort_saved_sql_20261031.json。
证据：reports/enterprise/20261031/cohort-equivalence-fix，包含完整328项测试XML、16题回放JSON、修复前后代码哈希和代码快照。

原16题是开发集，且模型接收批准SQL结构契约，不能外推总体准确率。新一轮真实模型验证应固定修复后代码版本，使用独立问题族及原始数据真值；不得把参与修复的16题当作盲测集。

本次没有修改数据政策、训练模型、Git提交或推送。

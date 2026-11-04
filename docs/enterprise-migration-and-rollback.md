# 迁移、受控发布与回滚

保留旧数据与报告，先并行验证，不对原petkit500删库重建。旧一次性DuckDB导出已取消DROP DATABASE并限制全新loopback *_migration_qa库，不能当正式同步程序。

## 源契约

企业适配器提供table/key/op/source_version/event_time/完整after。source_version必须是可比较的源位点序号，event_time带时区；schema按批准列精确匹配，缺列/新列阻断。批次带batch_id、source_version、event_time，实际发布额外记录ingested_at和source_kind。

Batches.apply在PG事务中保存批次指纹和最新行：同批同载荷复用，同批不同内容冲突；旧版本不覆盖新版本，删除留墓碑。此表只保存最新状态，不保存历史after-image：**只能以冻结的当前源快照物化，不能声称可从任意过去位点恢复历史**。持续位点回放依赖上游Flink/checkpoint或另建版本事件日志。

订单/订单行/退款/发货、采购行/收货、库存快照及设备cohort的Owner、主键、外键、事件窗口、币种和时区需签收。现有7指标定义是技术测试口径，不是PETKIT内部实际口径。由真实Owner确认后登记新catalog发行。

## 发行流程

1. 运维使用独立账号执行schema迁移并发放DML/只读scope账号。
2. 从同一冻结源位点制作批准表快照，校验业务键、父键、类型、控制总额；原始经营明细不外发模型。
3. Loader在新发行物理表装载、逐行hash和Decimal对账后才登记staged发行。失败保留私有表与failed账本；不切指针，不删旧数据，换新release重建。当前Loader全局与区域快照有界支持，区域结果由独立Python血缘oracle核对；持续CDC仍由上游提供。
4. `python -m src.enterprise.ops stage --file MANIFEST.json`用于已经由上游物化并独立核对的视图/表映射；仅受信运维调用，不能来自模型或浏览器。
5. `python -m src.enterprise.ops index --release RELEASE`建立固定BGE-M3 revision索引，确认完整表集合和内容hash。
6. `python -m src.enterprise.ops publish --release RELEASE --expected PREVIOUS --file RECONCILIATION.json --approval APPROVAL_ID`执行CAS切换。首发不传--expected。对账文件包含各scope源计数和独立指标对账结果；不以同SQL自证。
7. 旧任务继续使用已固定旧发行；旧发行物理对象保持不可变且保留至任务/审计保留期结束。不能修改同名底表来伪装新的发行。

发布权限只有运维账号。底层数据库管理员仍是受信边界，需生产变更控制，不能宣称应用能够防止DBA篡改物理表。PG与SR跨系统无共同事务，以private/staged/published+审核/对账恢复。

## 回滚与备份

以已批准旧StarRocks发行CAS发布回滚；不回退DuckDB。撤权优先于旧结果读取，授权版本变更后的历史结果需重新查询。

本机已对独立PG执行pg_dump -Fc→新*_qa库pg_restore→任务计数对比；未恢复覆盖原库。生产备份需加密、独立故障域、定期恢复；StarRocks FE元数据/BE对象、Flink源位点和索引快照的一致恢复尚未验收。

生产RPO/RTO、清理保留期、失败中间表回收及同发行恢复流程需平台与数据Owner签收。未知状态先停止发布并对账，不以重跑DROP/覆盖修复。

模型/SQL/检索成功耗时计数与任务完成处于同一PG事务，Prometheus读取持久累积桶而非扫描全部历史。历史值不反算回填，失败任务单列错误计数。


## P1/P2版本接入

先显式迁移控制面schema3并应用runtime-grants.sql，再使用匹配API/worker/前端版本；增量表回滚时保留。生产新增独立LINGXI_METRICS_TOKEN，仅允许读取聚合指标，业务API拒绝该身份；不得复用网关或SSO Secret。LINGXI_REVIEW_MODE保持disabled，真实消融及审批后才考虑shadow/enforce。恢复工具的SR操作账户与运行reader分离，不能改写运行scope账号。详细命令、保留/回滚策略、静态资源多版本路由及待接入条件见[交付记录](enterprise-p1-p2-delivery.md)。当前未进行生产升级。

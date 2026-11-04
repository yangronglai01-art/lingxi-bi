# 灵犀企业一期实施清单（2026-10-23）

依据统一目录12号交接任务书逐项执行。当前36项达到实现/隔离验证层，13项仍有企业输入或外部验收条件；不是49项全部上线验收。

`[x]`只表示表中明确范围已实现并有相应隔离证据；生产TLS/真实IdP/GPU/源系统/集群签收不能由本机结果代替。测试和历史成绩分别统计。

| ID | 状态 | 代码/证据 | 已完成与明确缺口 |
|---|---|---|---|
| A1 | [x] 实现/隔离通过 | docs/enterprise-*；reports/enterprise/20261023 | 保留DeepSeek未提交修改；git只读核查，原数据/秘密文件未读出 |
| A2 | [x] 实现/隔离通过 | docs/enterprise-*；reports/enterprise/20261023 | 已核验现有容器、端口和资源；新QA控制库，SR独立命名空间 |
| A3 | [x] 实现/隔离通过 | docs/enterprise-*；reports/enterprise/20261023 | 本表与current-status/verification维护任务ID及证据 |
| A4 | [x] 实现/隔离通过 | docs/enterprise-*；reports/enterprise/20261023 | 新正式入口与旧HTTP/worker隔离；迁移和CAS回滚手册 |
| B1 | [x] 实现/隔离通过 | src/enterprise/execution.py; deploy/enterprise; scripts/seed_enterprise_qa.py；reports/enterprise/20261023 | 固定SR3.5.21/PG17配置；本机SR为既有开发容器中独立QA库，并非生产集群 |
| B2 | [x] 实现/隔离通过 | src/enterprise/execution.py; deploy/enterprise; scripts/seed_enterprise_qa.py；reports/enterprise/20261023 | 流式/生命周期/TLS校验配置/超时/Decimal；会话512MiB、pipeline_dop=1实测。生产TLS握手待平台 |
| B3 | [x] 实现/隔离通过 | src/enterprise/execution.py; deploy/enterprise; scripts/seed_enterprise_qa.py；reports/enterprise/20261023 | 500表目录/源资产，7指标37模板StarRocks数值核对通过 |
| B4 | [x] 实现/隔离通过 | src/enterprise/execution.py; deploy/enterprise; scripts/seed_enterprise_qa.py；reports/enterprise/20261023 | 197105行确定性源资产；独立Python，金额Decimal控制总额 |
| B5 | [x] 实现/隔离通过 | src/enterprise/execution.py; deploy/enterprise; scripts/seed_enterprise_qa.py；reports/enterprise/20261023 | 真实last_query_id及KILL QUERY；检查取消后原连接无活动查询 |
| B6 | [x] 实现/隔离通过 | src/enterprise/execution.py; deploy/enterprise; scripts/seed_enterprise_qa.py；reports/enterprise/20261023 | 生产依赖无DuckDB/requests旧客户端；目标镜像导入边界断言通过 |
| B7 | [ ] 实现或准备完成，外部待验收 | src/enterprise/execution.py; deploy/enterprise; scripts/seed_enterprise_qa.py；reports/enterprise/20261023 | CI已写，真实引擎本机等价验证通过；未推送，GitHub远端未运行 |
| C1 | [x] 实现/隔离通过 | src/petkit/guard.py; src/enterprise/{identity,regional,execution}.py；reports/enterprise/20261023 | sqlglot StarRocks方言，未知/不可安全绑定语法拒绝 |
| C2 | [x] 实现/隔离通过 | src/petkit/guard.py; src/enterprise/{identity,regional,execution}.py；reports/enterprise/20261023 | AST只读/表列/函数/关联白名单；禁止系统变量和SET_VAR提示绕过资源上限 |
| C3 | [x] 实现/隔离通过 | src/petkit/guard.py; src/enterprise/{identity,regional,execution}.py；reports/enterprise/20261023 | R1专属只读物理快照和账号；拒读原始/其他scope/原petkit500以及写入 |
| C4 | [x] 实现/隔离通过 | src/petkit/guard.py; src/enterprise/{identity,regional,execution}.py；reports/enterprise/20261023 | OIDC声明与PG当前授权求交，scope/regions精确绑定；请求extra角色字段422 |
| C5 | [x] 实现/隔离通过 | src/petkit/guard.py; src/enterprise/{identity,regional,execution}.py；reports/enterprise/20261023 | 提交/执行/读取/历史/取消/最终结果复核授权和版本；撤权与对象归属负例 |
| C6 | [x] 实现/隔离通过 | src/petkit/guard.py; src/enterprise/{identity,regional,execution}.py；reports/enterprise/20261023 | 明确应用授权/数据库只读scope物理隔离；没有声称原生RLS |
| D1 | [x] 实现/隔离通过 | src/enterprise/{store,identity,worker}.py；reports/enterprise/20261023 | PG迁移及独立DML账号；运行账号不能改授权/发行或迁移schema |
| D2 | [x] 实现/隔离通过 | src/enterprise/{store,identity,worker}.py；reports/enterprise/20261023 | PG领取/续租/fencing；两个独立进程竞争、过期中断验证 |
| D3 | [x] 实现/隔离通过 | src/enterprise/{store,identity,worker}.py；reports/enterprise/20261023 | 请求指纹及幂等；同键不同载荷409，事件和状态同事务 |
| D4 | [x] 实现/隔离通过 | src/enterprise/{store,identity,worker}.py；reports/enterprise/20261023 | 取消/租约/旧worker不能覆盖；不自动重放模型调用，重启读持久状态 |
| D5 | [ ] 实现或准备完成，外部待验收 | src/enterprise/{store,identity,worker}.py；reports/enterprise/20261023 | 内省fail-closed和PKCE标准客户端实现/协议通过；真实企业IdP/门户未联调 |
| D6 | [x] 实现/隔离通过 | src/enterprise/{store,identity,worker}.py；reports/enterprise/20261023 | 不自动读取旧.env，无正式匿名身份/默认mock/云模型回退 |
| E1 | [ ] 实现或准备完成，外部待验收 | src/enterprise/{query,vectors,contract}.py; docs/approved-metric-contracts.json；reports/enterprise/20261023 | 技术口径登记并阻止无Owner批准的生产发布；真实Owner/维度/币种/时区需确认 |
| E2 | [x] 实现/隔离通过 | src/enterprise/{query,vectors,contract}.py; docs/approved-metric-contracts.json；reports/enterprise/20261023 | 授权范围内500目录，最多14表及批准依赖，不把500DDL发给模型 |
| E3 | [ ] 实现或准备完成，外部待验收 | src/enterprise/{query,vectors,contract}.py; docs/approved-metric-contracts.json；reports/enterprise/20261023 | BGE-M3/PG版本索引、hash/revision/1024D协议已验证；真实BGE服务与召回质量未验 |
| E4 | [ ] 实现或准备完成，外部待验收 | src/enterprise/{query,vectors,contract}.py; docs/approved-metric-contracts.json；reports/enterprise/20261023 | 内网local-chat、StarRocks结构化计划、输入字符/输出token预算；真实27B未调用 |
| E5 | [x] 实现/隔离通过 | src/enterprise/{query,vectors,contract}.py; docs/approved-metric-contracts.json；reports/enterprise/20261023 | 模板/模型generation_mode显式分开；数值必须来自数据库，不虚构解释 |
| E6 | [x] 实现/隔离通过 | src/enterprise/{query,vectors,contract}.py; docs/approved-metric-contracts.json；reports/enterprise/20261023 | 已登记成熟队列等模板通过；未定义同比/累计/分层先澄清，未扩二期 |
| E7 | [x] 实现/隔离通过 | src/enterprise/{query,vectors,contract}.py; docs/approved-metric-contracts.json；reports/enterprise/20261023 | 有界结果/截断/Decimal/NULL/空集/百分数及水位契约；ECharts近似展示不改精确表 |
| F1 | [ ] 实现或准备完成，外部待验收 | src/enterprise/{loader,regional,ingestion,ops}.py；reports/enterprise/20261023 | 源批次契约和通用适配层已实现；真实ERP/OMS/WMS映射及连接器待输入 |
| F2 | [x] 实现/隔离通过 | src/enterprise/{loader,regional,ingestion,ops}.py；reports/enterprise/20261023 | 业务键/父键/类型、独立行hash与Decimal核对；37模板覆盖退款/库存/成熟队列等已定义域 |
| F3 | [ ] 实现或准备完成，外部待验收 | src/enterprise/{loader,regional,ingestion,ops}.py；reports/enterprise/20261023 | 重复/迟到/乱序/删除/schema漂移合成验证；真实Flink CDC与源checkpoint未接 |
| F4 | [x] 实现/隔离通过 | src/enterprise/{loader,regional,ingestion,ops}.py；reports/enterprise/20261023 | 不可变SR版本表，全部核对后才登记staged，不依赖跨表原子替换 |
| F5 | [x] 实现/隔离通过 | src/enterprise/{loader,regional,ingestion,ops}.py；reports/enterprise/20261023 | 真实半装载异常注入后私有表不可查询/不可发布；新发行重建，PG发布CAS回滚通过 |
| F6 | [ ] 实现或准备完成，外部待验收 | src/enterprise/{loader,regional,ingestion,ops}.py；reports/enterprise/20261023 | QA PG备份恢复及发行回滚通过；真实源位点、SR灾备和RPO/RTO待平台验收 |
| G1 | [x] 实现/隔离通过 | src/enterprise/api.py; web; deploy/enterprise；reports/enterprise/20261023 | 202版本化任务提交/状态/结果/取消及实际OpenAPI；/ask不作为正式入口 |
| G2 | [x] 实现/隔离通过 | src/enterprise/api.py; web; deploy/enterprise；reports/enterprise/20261023 | React/TS/ECharts，任务轮询/取消/历史/口径水位/SQL/精确结果；30项前端测试 |
| G3 | [ ] 实现或准备完成，外部待验收 | src/enterprise/api.py; web; deploy/enterprise；reports/enterprise/20261023 | 同源运行时配置、PKCE及授权码端点已实现；企业IdP/CORS/门户真实联调待接 |
| G4 | [x] 实现/隔离通过 | src/enterprise/api.py; web; deploy/enterprise；reports/enterprise/20261023 | Python3.12非root只读根目标镜像，基础镜像digest/20运行依赖锁定，Secret/探针/声明 |
| G5 | [ ] 实现或准备完成，外部待验收 | src/enterprise/api.py; web; deploy/enterprise；reports/enterprise/20261023 | 持久成功耗时直方图/队列/错误取消/水位及告警模板；企业采集、资源饱和/审计故障报警待平台 |
| G6 | [ ] 实现或准备完成，外部待验收 | src/enterprise/api.py; web; deploy/enterprise；reports/enterprise/20261023 | 双API/双worker声明已写；实际HA、资源组、跨故障域、SLO及签收未验收 |
| H1 | [x] 实现/隔离通过 | enterprise_tests; operator_tests; reports/enterprise/20261023；reports/enterprise/20261023 | 正式后端75、前端30、评测器3；历史兼容406在独立legacy QA通过，分别统计 |
| H2 | [x] 实现/隔离通过 | enterprise_tests; operator_tests; reports/enterprise/20261023；reports/enterprise/20261023 | 真实SR/PG及目标镜像API闭环；身份/生成/Embedding协议桩明确标注 |
| H3 | [ ] 实现或准备完成，外部待验收 | enterprise_tests; operator_tests; reports/enterprise/20261023；reports/enterprise/20261023 | 冻结评测工具及判分3项通过；真实27B三档盲测和独立样本缺失，不报准确率 |
| H4 | [x] 实现/隔离通过 | enterprise_tests; operator_tests; reports/enterprise/20261023；reports/enterprise/20261023 | 源raw-record独立oracle，金额Decimal；不是同SQL自证 |
| H5 | [ ] 实现或准备完成，外部待验收 | enterprise_tests; operator_tests; reports/enterprise/20261023；reports/enterprise/20261023 | 物理取消、双worker、故障注入、PG恢复已测；真实模型/并发/节点故障/生产容量未测 |
| H6 | [x] 实现/隔离通过 | enterprise_tests; operator_tests; reports/enterprise/20261023；reports/enterprise/20261023 | README、依赖、正式入口、OpenAPI、部署/迁移/证据和统一03/08/11同步 |
| H7 | [x] 实现/隔离通过 | enterprise_tests; operator_tests; reports/enterprise/20261023；reports/enterprise/20261023 | 20号交付记录、变更/状态矩阵、版本hash、命令和外部输入清单 |

## 继续执行所需输入（通过本机环境/Secret，不在聊天粘贴密钥）

1. 企业SSO issuer/audience/内省与PKCE客户端、真实应用角色/tenant声明及授权同步规则。
2. 内网底座local-chat与embedding-default服务、对应不可变27B/BGE revision、服务账号允许别名及GPU容量。
3. ERP/OMS/WMS/工单源类型、业务主键/CDC位点、批准视图、指标Owner与合同化刷新口径。
4. 预生产/生产PG与SR集群、CA/Secret/私网地址、资源组/并发目标、Prometheus/告警与备份恢复签收。
5. GitHub提交/推送及远端CI安排；本轮未自动提交、推送或发布。

二期语义复核模型、多轮追问和微调未扩展。


## 2026-10-23：P0可独立落实部分

用户确认暂无企业环境，先完成可独立部分。新增生产指标口径/字段血缘检查、生成模型revision配置、生产发布证据绑定门禁、冻结题集与分路径质量门槛、真实HTTPS双身份冒烟、有界负载及离线token预算工具；配套未批准输入模板和 `docs/p0/README.md`。新增交付不等于真实P0签收，原13项外部条件保持未完成。最新P0记录见统一目录22号文档。


## P1/P2后续实施（2026-10-23）

已按授权继续独立工程部分：服务端结构化意图/批准SQL结构与7指标输出校验、同发行快照对账恢复/隔离/未applied禁止发布、低基数阶段观测与独立监控身份、前端图表懒加载、授权与发行绑定追问、持久反馈人工审核、默认关闭的内网独立语义复核、检索/复核对照与候选版本清单。

最新正式后端89项、工具92项、前端31项分别通过；完整Linux报告181项含重叠工具子集，不与独立工具报告累加。真实StarRocks/PG已验证，真实模型/IdP/ERP/平台仍未接；没有准确率、容量或微调收益结论。P1复杂口径/真实检索与平台浏览器验收、P2真实消融/训练数据与实际微调仍待条件。见23号交付记录及仓库docs/enterprise-p1-p2-delivery.md。P0原13项外部验收状态保持不变。

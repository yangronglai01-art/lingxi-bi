# 灵犀问数P1/P2独立实施与验收记录

日期：2026-10-23。依据21号优化计划；承接22号P0交付。用户授权继续P1/P2，并已确认暂无企业环境。

**当前结论：P1/P2可独立落实的主要工程能力已实现并通过隔离验证；真实业务口径扩展、模型收益和生产签收仍待条件。P0真实环境验收没有被提前勾选完成。** StarRocks/PG及内网模型路线保持原定方案，没有新增云端问数回退或启动微调。

## 一、逐项状态与边界

`[x]`仅表示以下明确实现/验证范围；“待”表示真实环境或批准输入缺失，不能声称整项生产签收。

| 计划项 | 本次完成与代码 | 尚未完成/启用的部分 |
|---|---|---|
| P1-01 结构化意图/口径 | [x] `intent.py`由服务端提取指标、分组、明确年月、截止日、指标版本；未知条件澄清；SQL先过授权AST，再与批准规范结构比较。退款预聚合、库存末次快照、时间/分组/关联等偏差不能直接执行；输出计划来自服务端，不把未验证的模型计划当事实。`contract.py`扩展7指标别名、数值、整数、百分数、零分母和列形状检查 | 只支持当前批准形状的7指标。不是通用SQL等价证明；正确但结构不同的SQL也可能被拒绝。真实口径仍待Owner签收，不能据此报准确率 |
| P1-02 检索/复杂口径 | [x] 结果增加授权范围内selected_tables；`score_enterprise_retrieval.py`分别统计依赖表上下文召回、BM25 Recall@8和指标路由，区分“补齐依赖”与真正的语义召回 | 真实BGE对照和检索收益未测；同比/累计/分层等没有Owner定义及独立参考，不扩展执行。没有增加Reranker/Qdrant |
| P1-03 发行恢复 | [x] `loader.py/recovery.py`装载与恢复共用发行锁；完整但账本未知的快照可按原冻结输入重新逐行hash/Decimal对账，恢复同一发行；不完整则隔离、新发行重建。quarantine不删表；账本非applied时，即使staged也禁止publish | 不覆写半表、不自动删除历史表；真实源checkpoint/历史日志和生产SR/PG灾备仍属于P0。保留天数与实际物理清理由Owner/运维批准 |
| P1-04 容量/观测 | [x] 成功/失败/澄清分阶段直方图与总耗时，固定低基数标签，一次有界PG批事务写入；失败日志带request_id。指标接口增加批准SLA/技术默认标记及告警声明。生产使用独立监控Secret，不能访问业务接口，不让采集器持有全局分析员token | Prometheus/OTel、GPU/SR/PG资源、告警送达和目标SLO未接企业平台；无压力证据不引入连接池、增大运行槽或加缓存。不同业务域的真实水位定义待接入确认 |
| P1-05 前端 | [x] 图表懒加载；首屏JS由约703.84KB降到159.23KB（gzip52.81KB），图表548.10KB按需下载；精确金额表不变。批准SLA优先，未签收显示技术默认；区分取消受理与最终任务状态；查询输入增加可访问名称，示例与实际支持范围一致 | 31项为JS DOM测试；真实浏览器、企业SSO、网络/首屏性能与完整无障碍验收待做。图表chunk仍有500KB构建提示，没有隐藏该提示 |
| P1-06 发布/复盘 | [x] `build_enterprise_release.py`生成源文件、前端、运行依赖/权限与模型/指标/发行的候选hash清单；反馈绑定用户/任务/权限版本/scope/发行并人工审核，问题可分类复盘；重复反馈不篡改既有内容 | 候选清单无签名、未批准；真实灰度/回滚签收和故障复盘待企业环境。静态资源多版本保留/同版本路由要在真实发布链路验收 |
| P2-01 独立语义复核 | [x] `review.py`支持disabled/shadow/enforce；只在生成SQL已通过确定性门禁后复核，批准模板不额外调用。只走同一私有网关的独立别名/revision，不发结果行或物理表名；最多500输出token、1–20秒预算。影子模式不改结果，强制模式非consistent则澄清；`compare_enterprise_runs.py`报告改善/退化/误拦与耗时差 | 默认disabled。没有实际第二模型/别名注册、冻结消融和收益批准；不能因为多一个模型就说更准确。enforce需配置真实审批引用，不自动启用 |
| P2-02 多轮追问 | [x] API新增previous_job_id；服务端只复用结构化意图，不拼旧SQL。每次绑定当前subject/tenant/policy/scope、父任务、同一发行、指标版本和1小时有效期；仅支持明确改分组或年月，前端显式勾选继续上一问题 | 不支持任意条件、SQL片段或无限对话记忆；权限/发行变化、过期和歧义需重新完整提问。真实员工话术覆盖及可用性待试点 |
| P2-03 微调评估 | [x] PG持久反馈；运行账号可提交但不能审核，独立运维账号记录审核人与审批；评估按真实发行元数据排除合成QA，再汇总已确认问题。输出优先治理/提示/检索消融及训练授权=false，没有自动导出样本或训练 | 没有真实失败样本、数据来源/脱敏/授权审查、独立训练/开发/盲测集或训练收益实验。此阶段是“判断是否值得微调”，不是必须交付权重；没有称已完成微调 |

本轮没有选择新的数据库、扩大到任意500表查询、扩大复杂指标或迁移其他四项目。

## 二、真实验证

证据目录：`D:\projects\lingxi\reports\enterprise\20261023`。

- `p12-linux312.xml`：181项通过，89项正式后端＋92项工具；真实独立StarRocks/PG，Linux/Python3.12、非root、只读根。身份/生成/Embedding/复核仍明确使用协议桩，不是模型准确率实验。
- `p12-operator-final-linux312.xml`：操作工具最终92项无网络Linux通过，包含后补的恢复操作账号与运行只读账号分离检查；它覆盖了完整集中的工具子集，不能把两份报告相加。
- `p12-runtime-feedback.xml`：同一反馈回归用例补验运行最小权限账号真实写入反馈、拒绝该账号审核；不另外累计成独立能力数量。
- `p12-frontend.xml`：31项通过；TypeScript、Vite构建通过。图表chunk体积及运行时config.js的构建提示保留，没有宣称真实浏览器或SSO通过。
- `p12-first-linux312.xml`：首轮157通过/10失败，原因为守卫日期化简后规范形状不一致；现两侧统一经相同守卫规范化，并保留max_rows+1截断探测契约、拒绝模型偷偷降低LIMIT/OFFSET。
- `p12-collection-error.xml`：最终工具检查曾发现SQL字符串引号错误，已改为参数化过滤并复测；保留过程记录，不冒充从未失败。
- `p12-image-proof.json`、`p12-delivery-summary.json`：本地镜像、实际源码hash、最终测试类别及状态。
- `p12-release-final-candidate.json`：实际生成的候选清单，明确QA未批准、模型未接入，不能作为生产放行材料。

最新应表述为“正式后端89项、最终工具92项、前端31项分别通过”。不叠加重复运行，不与旧历史406项混算；历史路径本轮未修改或重跑。

本地镜像：`lingxi-enterprise:20261023-p12`，实际inspect ID：`sha256:dbb86cd2fd0d27afb0dd703b0f3a5d2d0b81f508678acbbfc61be8a527675dbf`。它不是企业registry发布digest；没有推送GitHub、发布镜像或切流生产。

## 三、迁移与启动顺序

1. 先保留当前镜像、配置引用、PG备份和已验收发行，选择维护窗口；真实生产前仍须完成P0全部输入和验收。
2. 使用PG迁移owner执行 `python -m src.enterprise.ops migrate`，再应用 `deploy/enterprise/runtime-grants.sql`。本轮只在独立QA执行。控制面schema升级到3：反馈、低基数阶段观测、装载账本由显式迁移建立，API/worker不自动迁移。
3. 运行账号仍不能改授权、发布或审核反馈。新增反馈只授列级INSERT和SELECT，不授审核UPDATE；阶段观测授指定表DML。
4. 预生产/生产必须新增独立随机 `LINGXI_METRICS_TOKEN`（32–512个ASCII可打印字符，拒绝占位或复用网关/SSO密钥）。采集器仅用该Secret请求 `/internal/metrics`；该token在业务接口被明确拒绝。Secret变更后按平台流程滚动重载，不把令牌写进URL或命令行。
5. `LINGXI_REVIEW_MODE=disabled`保持默认。网关别名注册、真实revision/预算、消融题集和审批就绪后先shadow，再评估enforce；不新增云端路线。
6. API和worker使用相同不可变镜像。静态文件要保留多版本或保证同版本路由，避免滚动时新JS懒加载chunk被旧pod返回404。具体内部静态存储/路由由平台接入验收，本轮不部署生产资源。
7. 回滚时恢复匹配的旧镜像/前端与已验收发行，按CAS回滚发布指针；schema3是增量表，保留反馈/审计，不DROP新表。未确认完成的模型任务不自动重放；运行中的租约和取消仍遵循一期规则。

## 四、使用与运维入口

### 追问和反馈

```json
POST /api/v1/analytics/queries
{"question":"按渠道看","previous_job_id":"上一次已完成任务ID"}
```

仍须Bearer与Idempotency-Key；未知字段/角色不接受。也可追问“改为2026年9月”；“只看华东”等未批准条件返回澄清。父任务过期、跨用户或发行变化不会沿用旧上下文。

```json
POST /api/v1/analytics/queries/任务ID/feedback
{"vote":"negative","category":"metric","comment":"请描述具体口径问题，避免个人信息"}
```

分类为correct/metric/retrieval/generation/data/permission/performance/other。当前前端对已完成答案收集反馈；失败任务的诊断仍从状态、审计与阶段观测进入，后续如需统一失败反馈界面可单独扩展。评论留在受控PG，不自动进入训练集。

```powershell
python scripts/p12_operator.py review-feedback --id FEEDBACK_ID --reviewer REVIEWER --approval APPROVAL_REF --decision confirmed_issue --out review-proof.json
python scripts/p12_operator.py tuning-assessment --out tuning-assessment.json
```

这些命令运行在独立受控运维环境，使用运维PG权限及安全注入配置；API运行账号不能审核。真实源标记来自不可变发行，而非模型生成结果；声明的来源仍需企业审阅，统计不证明微调收益。禁止将QA记录改名为真实样本。

### 恢复和隔离

```powershell
python scripts/p12_operator.py inspect-load --id RELEASE_ID --out load-state.json
python scripts/p12_operator.py reconcile-load --id RELEASE_ID --file frozen-snapshot.json --approval APPROVAL_REF --out recovery-proof.json
python scripts/p12_operator.py quarantine-load --id RELEASE_ID --manifest EXPECTED_MANIFEST_HASH --approval APPROVAL_REF --out quarantine-proof.json
```

冻结输入包含catalog/rows/keys/watermark，操作输入限制5MB，不替代大规模CDC。reconcile只恢复完整且逐行/总额一致的pending/failed快照；不同manifest、不完整表、已发布或隔离状态拒绝。源快照及checkpoint应保留到恢复验收完成。

恢复操作另注入 `LINGXI_SNAPSHOT_OPERATOR_USER/PASSWORD`，权限只覆盖同一SR集群中的指定数据库及必要GRANT；生产禁止root/admin及复用运行reader。`STARROCKS_SCOPES_JSON`继续保留原运行reader，不能为了操作改成管理员，否则会授错账号。操作账户不得放入API/worker的Secret。

隔离不删除物理表。清理策略：批准保留天数后，列出隔离对象，检查没有已登记/已发布发行和历史任务引用、冻结输入有备份、审计可追溯，再独立审批清理。保留期限未确定前不自动清表，不能把删除当恢复。

### 检索、复核对照与版本清单

```powershell
python scripts/score_enterprise_retrieval.py --cases holdout.jsonl --results per-case-results.jsonl --out retrieval-report.json
python scripts/compare_enterprise_runs.py --baseline baseline-results.jsonl --candidate reviewed-results.jsonl --out review-ablation.json
python scripts/build_enterprise_release.py --catalog approved-catalog.json --data-release RELEASE_ID --model-revision GENERATOR_REVISION --embedding-revision BGE_REVISION --out release-candidate.json
```

检索题需有expected_tables/expected_metric，先冻结并人工确认。复核对照要求相同冻结题集、参考、数据发行和生成模型，注意随机性及执行顺序；工具不自动启用复核。候选清单没有签名，也不替代P0发布证据；实际发布仍需独立对账、完整索引、审批及验收bundle。

## 五、不能提前勾选的工作

- P0：真实Owner、企业IdP/门户、27B/BGE、ERP/CDC/Airflow、生产平台/HA/灾备、远端CI及上线签收。
- P1：真实混合检索对照、复杂口径扩展、实际负载/连接压力与缓存收益、平台告警/OTel和真实浏览器验收。当前统一发行水位应在真实接入时确认各域定义，不能用一张表的新时间代表全部业务刷新。
- P2：真实第二模型消融与误拦/延迟成本、员工多轮使用反馈、足量经审核的真实失败样本、训练数据授权/脱敏/分组隔离、微调训练及收益实验。

以上已在计划中保留“待真实条件/条件启用”，不因代码或合成QA通过改写成小佩实际采用、真实准确率或已完成模型微调。

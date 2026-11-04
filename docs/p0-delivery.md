# 灵犀问数P0独立实施与待接入记录

日期：2026-10-23。依据21号优化计划。用户已授权执行P0，随后明确“暂时没有企业环境，先完成可独立落实的部分”。

**结论：本次可独立落实的校验、工具、模板、发布控制与本地验证已交付；P0真实企业联调和生产签收尚未完成。** 不把原13项外部验收改为完成，不把本轮交付称为已经在小佩内部上线。

## 一、逐包状态

| 工作包 | 本次已完成 | 仍待真实条件，不能勾选整体完成 |
|---|---|---|
| P0-01 指标口径 | 7指标技术登记转换为待签收输入模板；启用指标须有Owner/审批、粒度/单位/时区/币种/维度、源包含/删除/时间归属/空值规则、SLA及完整字段血缘；预生产/生产发布强制检查 | 真实业务Owner、源字典、口径和首批范围签收；默认4指标也未获企业批准 |
| P0-02 SSO/门户 | 保留已有OIDC/PKCE协议实现；新增HTTPS双身份冒烟，检查匿名拒绝、真实身份、对象归属及跨用户读取/取消；不导出认证声明或业务结果 | 企业IdP、角色/tenant映射、门户；真实浏览器PKCE、登出/过期、撤权、同源配置验收 |
| P0-03 模型 | 预生产/生产同时要求生成及Embedding revision，拒绝占位；查询登记配置的生成模型revision；新增不下载/不执行remote code的本地tokenizer计数工具，包含chat template及输出预留 | 实际27B/BGE及不可变权重、tokenizer和上下文配置；真实维度/召回/索引一致性、服务不可用/超时/超长输入验证。离线计数不等于逐请求运行时token强制预算 |
| P0-04 真实同步 | 保留现有幂等/版本/删除/漂移校验和不可变加载；新增源版本/字段/CDC边界、checkpoint、日志保留、层级映射、对账及恢复输入契约与步骤 | 真实ERP/OMS/WMS类型/版本/权限/映射；Flink CDC/StarRocks connector与Airflow实际部署、更新删除重放和checkpoint恢复。没有假造可运行连接器或DAG |
| P0-05 质量 | 开发/盲测id、语义分组及规范化问题去重；独立参考元数据检查；预批准门槛及冻结hash；enterprise评测强制冻结；模板和模型分开计分，保留失败/超时分母，分档P95/P99、错误回答与零容忍放行检查 | 真实独立题集、Owner参考/质量门槛签收、真实模型盲测。350条是目标，未生成伪业务题凑数，未宣称准确率 |
| P0-06 性能/灾备 | 有界真实HTTPS负载工具，最多200次/8并发/单任务300秒；失败保留分母，异常后请求取消；联合恢复、平台HA/TLS/资源组、监控告警与签收步骤 | 代表性负载和目标SLO、GPU/SR/PG资源采集、企业告警送达/响应人、生产HA故障及源位点+PG+SR恢复演练，实测RPO/RTO |
| P0-07 发布/交接 | 生产发布必须绑定tenant/release/两类模型revision、7包真实环境审阅记录、文件存在及SHA256、审批引用；新增门禁入口测试、待填证据模板、运行手册；新本地镜像构建及打包验证 | 远端GitHub CI、registry不可变digest、真实发布审批、生产前回滚演练、生产灰度观察和最终签收。本轮未提交推送、未应用集群清单或切流 |

本次新增文件校验保证内容/版本绑定及未满足条件时阻断，不提供数字签名，也不会自动辨别伪造报告。审阅人、企业审批系统和真实环境记录是信任边界，不能把协议测试桩报告填写成real_environment。

## 二、代码与操作入口

- `src/enterprise/acceptance.py`：业务治理和受限目录证据hash/版本检查。
- `src/enterprise/quality.py`：首次结果、分档及模型/模板路径分别统计、错误回答/样本/延迟门槛和零容忍检查。
- `src/enterprise/settings.py`、`query.py`：生产模型revision配置及结果溯源。别名仍是内网local-chat/embedding-default，没有云端回退；配置溯源不能代替真实网关权重确认。
- `src/enterprise/ops.py`：预生产/生产口径检查；生产publish额外强制 `--acceptance`，保留独立对账、完整索引与CAS。预生产不强求生产证据包，以便先取得真实评测材料。
- `scripts/p0_enterprise.py`：初始化待验收记录、离线检查；待填或篡改材料退出2，不能覆盖既有报告。
- `scripts/freeze_enterprise_eval.py`、`evaluate_enterprise.py`：冻结与真实评测；拒绝enterprise未冻结输入及hash变化，不混算模板与模型。
- `scripts/smoke_enterprise.py`：HTTPS真实双身份接口冒烟，令牌从安全环境注入。
- `scripts/check_enterprise_tokenizer.py`：离线完整chat token预算，操作环境需已有transformers/本地tokenizer；不增加生产镜像依赖。
- `scripts/load_enterprise.py`：有界端到端负载、全尝试/成功延迟和失败分母。单问题闭环实验不等于容量、准确率或数据库终止证明。
- `operator_tests/test_p0.py`、`test_publication.py`、`test_load.py`及扩展的`test_evaluator.py`：反例、真实发布入口阻断及工具协议测试，明确不当作真实模型/SSO成绩。
- `deploy/enterprise/env.example`：新增 `MODEL_REVISION` 待替换占位，未写企业密钥。

全部命令、先后顺序、数据格式、输入位置和验收边界见仓库 **`D:\projects\lingxi\docs\p0\README.md`**。

## 三、可直接交接的待填输入

目录 `D:\projects\lingxi\docs\p0`：

1. `business-catalog.pending.json`：指标Owner/审批、源规则、字段血缘、SLA；保留synthetic/technical状态，不能直接发布。
2. `source-delivery.pending.json`：源类型/版本、主键、CDC与checkpoint、数仓层级、独立对账和灾备输入；不放Secret或客户明细。
3. `quality-criteria.pending.json`：题量目标、待签收的正确率/错误回答上限/P95；没有编造阈值或企业承诺。
4. `case.template.json`：独立题的JSONL字段范式；参考值仍待填写，不是验收题。
5. `acceptance.pending.json`：7包均pending/not_connected，企业审阅时间/人/审批/文件hash待填。

真实输入到位后按“口径→身份与模型→一个源业务域→冻结质量/性能灾备→远端CI与受控灰度”执行。现有本地QA继续用于隔离验证，不可通过改名变成生产环境。

## 四、实际验证和证据

证据目录 `D:\projects\lingxi\reports\enterprise\20261023`：

- `p0-linux312.xml`：**104通过**，包含正式后端75项及当时工具29项；真实StarRocks/PG，Linux/Python3.12、UID10001、只读根。SSO/生成/Embedding仍明确为协议桩。一个既有Starlette测试客户端弃用提示，没有新增失败。
- `p0-operator-linux312.xml`：最终工具集 **33通过**，含随后增加的生产发布入口与模板路径隔离测试；无网络Linux/Python3.12运行。它替代工具29项的旧快照，不把两次重复测试相加。
- `p0-operator-windows.xml`：中间阶段32项本地通过，保留过程证据，以最终Linux33项为工具终态。
- `p0-pending-check.json`：实际对待填目录执行门禁检查，结论blocked、退出2，Owner/字段及真实验收缺失均未放行。
- `p0-delivery-summary.json`：本次文件hash、镜像ID及证据摘要。

因此最新结论是“正式后端75项、最终工具33项分别通过”，而不是把104和33再累计。前端本次没有改动，沿用此前30项及构建验证，本轮没有声称重新跑过浏览器验收或生产SSO。

本地新镜像 `lingxi-enterprise:20261023-p0` 已实际构建，image ID `sha256:368752376b8f35b2b262910f003f98d4b444c2d048ff8d64306111598c7f4996`；Python3.12.15、UID10001、无网络只读运行导入新增模块通过，acceptance源hash与工作区一致。该ID是本地镜像ID，不冒充企业registry发布digest。本次未新增运行时依赖，前端构建层复用缓存。

原一期36/49实现与13项外部待验收状态保留；未再运行历史406项，因为本轮没有修改历史引擎链路。P1/P2不在本轮范围。

## 五、企业输入到位后需要补做的验收

- 业务Owner批准首批口径、映射、授权、水位和质量/性能门槛。
- 真实IdP/门户、内网27B/BGE、对应权重/tokenizer/上下文、服务账号与GPU配额。
- 一个真实业务域的源访问、连接器兼容性、CDC与Airflow执行环境、checkpoint/日志留存和数仓支持。
- 预生产/生产PG和SR、TLS/Secret、HA及资源组、观测告警、独立恢复目标与平台签收。
- GitHub远端CI、发布审批及真实灰度观察。生产灰度后的最终签收须追加，不能由生产前门禁一次替代。

以上输入通过受控配置或Secret渠道提供，聊天只确认存放位置，不发送密钥。没有这些条件时，P0整体保持“工程配套已交付、真实接入与生产验收待完成”。

# 灵犀问数P0接入与验收运行手册

2026-10-23。本手册配套21号计划。当前没有企业环境，用户已明确先完成可独立落实部分；以下真实环境步骤尚未执行。命令从 `D:\projects\lingxi` 运行，Python使用对应环境；配置经Secret注入，不复制历史.env，不在终端回显密钥。

## 1. 准备业务口径和源映射（P0-01/04）

`docs/p0/business-catalog.pending.json` 保留7个技术指标及依赖表列，所有Owner/审批、字段来源、源策略和SLA仍未确认。复制为工作文件，由业务与数据Owner填写后才能变为 `synthetic=false`、治理及各启用指标 `approval_status=approved`。不要把模板名称、技术假设或QA通过改写成企业批准。

每个启用指标必须有定义、粒度、单位、IANA时区、币种（非金额可null）、批准维度、正数刷新SLA，以及包含/删除/时间归属/空值规则；每张依赖表必须登记源对象、主键和所有提供给模型的列的字段血缘。当前默认启用净销售额、工单、库存、采购入库数量，仍需逐项批准；另3项保持关闭，除非明确批准扩围。

`docs/p0/source-delivery.pending.json` 列明源数据库类型/版本、源键、快照边界、Flink/连接器版本、checkpoint与日志保留、更新删除映射、ODS到ADS对象、独立对账、RPO/RTO和恢复证据。它是接入契约，**没有创建Flink作业或Airflow DAG**；没有源类型、版本和映射时不生成看似可部署的连接器配置。

验收至少覆盖：初始快照与CDC边界一致；新增、修改、删除、重复、乱序、重启重放；停源后恢复；schema漂移阻断与人工批准；Decimal总额与逐键/逐行校验。上游保留日志或冻结输入，不能用只保留最新状态的通用账本重建任意历史时点。持续大规模数据同步由数仓链路承担，现有有界快照工具不替代它。

## 2. 身份与真实接口冒烟（P0-02）

完成 `deploy/enterprise/env.example` 中OIDC资源端、浏览器PKCE和私网TLS配置，使用真实两个用户、不同subject且均具备已登记应用角色和授权。令牌通过 `LINGXI_SMOKE_TOKEN`、`LINGXI_SMOKE_OTHER_TOKEN` 注入。

```powershell
python scripts/smoke_enterprise.py --api https://ANALYTICS_INTERNAL_ORIGIN --question "已批准的模板问题" --timeout 120 --out smoke.json
```

工具检查匿名拒绝、身份有效、依赖ready、查询完成、其他用户读取和取消被拒绝、非mock和enterprise源；生成的是有界真实查询，结束或失败时请求取消。它不替代真实浏览器授权码/PKCE、登出/过期、撤权同步及数据库终止确认。返回相同subject的两个token会使验收失败。输出不包含身份声明、原始结果、令牌或SQL。

## 3. 模型版本和token预算（P0-03）

生产/预生产配置现同时要求 `MODEL_REVISION` 与 `EMBEDDING_REVISION`；占位值不能通过检查。别名仍为 `local-chat` 与 `embedding-default`，不可变revision必须由底座实际部署清单确认。应用结果登记生成模型revision只是配置溯源，**不能证明网关实际使用该权重**；网关映射和权重文件另附证据。

准备与部署完全相同的本地tokenizer目录、chat template及上下文限制。工具在具备transformers的隔离操作环境运行，不增加应用镜像依赖、不下载模型、不允许remote code。

```powershell
python scripts/check_enterprise_tokenizer.py --tokenizer-dir D:\MODEL_SNAPSHOT\tokenizer --messages representative-messages.json --model-revision CONFIRMED_REVISION --context-tokens CONFIRMED_LIMIT --output-tokens 3000 --out tokenizer-check.json
```

`messages` 是完整system/user/assistant消息数组，使用批准的代表性短/长元数据和超长负例，文件不得含个人明细。计数包含chat template及预留输出，附tokenizer文件hash。上述命令参数中的全大写值必须替换。该检查是离线预算验证，**当前应用仍有字符预算，未实现逐请求本地tokenizer强制计数**；真实网关上下文与输出上限、超限报错及超时拒绝须联调验证后才能完成P0-03。BGE真实1024维、索引hash/revision和依赖召回也须单独验收。

## 4. 冻结题集与首次结果评测（P0-05）

以 `docs/p0/case.template.json` 形成JSONL；每条有唯一id、语义group_id、档位、预期路径requires_model、enterprise来源、独立参考Owner/来源、expected_status。数值题必须预先有列与行；金额用精确十进制字符串。拒绝题必须 `zero_tolerance=true`。不要将SQL运行结果当独立参考，禁止把开发题换词后放进盲测。

由Owner预先填写 `docs/p0/quality-criteria.pending.json` 的审批、各档正确率/错误回答上限、最小样本与P95预算。模板未给准确率阈值，不能边测试边调整标准。冻结工具检查开发/盲测id、group和规范化问题不重叠，人工仍需检查语义分组及参考答案独立性。

```powershell
python scripts/freeze_enterprise_eval.py --cases holdout.jsonl --dev-cases development.jsonl --criteria criteria.approved.json --out frozen.json
python scripts/evaluate_enterprise.py --api https://ANALYTICS_INTERNAL_ORIGIN --cases holdout.jsonl --model-revision CONFIRMED_REVISION --frozen-manifest frozen.json --criteria criteria.approved.json --out reports/enterprise/REAL_RUN
```

评测令牌通过 `LINGXI_EVAL_TOKEN` 注入。enterprise题集没有冻结manifest/criteria将被拒绝，文件hash变化也被拒绝。报告保留失败/超时分母、首次结果、按档和预期模型/模板路径分开统计，样本P95/P99，零容忍失败及质量放行结论；不导出认证用户声明。JSONL结果可含受控业务数据，应存受限验收目录，不自动提交GitHub。工具退出2表示未满足门槛；隔离工具测试不是企业模型准确率。

## 5. 性能、监控与灾备（P0-06）

预生产先限定用户/指标、确认平台允许负载，使用 `LINGXI_LOAD_TOKEN` 注入身份。工具最多200次、8并发、单任务300秒；默认10次/1并发，禁止以一次短跑推出企业容量。

```powershell
python scripts/load_enterprise.py --api https://ANALYTICS_INTERNAL_ORIGIN --question "已批准的代表性问题" --requests 20 --concurrency 2 --timeout 120 --out load.json
```

输出成功数、所有尝试P95/P99、成功P95、拒绝/失败/超时数、实际路径与源类型，不输出问题答案。单问题闭环压测不能代替真实到达分布、混合复杂度、持续负载和多用户测试；模板路径不代表模型吞吐。有超时/失败返回退出2，不把HTTP202当成功。采集API/worker、队列、GPU/模型、SR、PG、连接数和资源组指标，统一时间窗口分析。实际目标并发、延迟、刷新SLA由Owner/平台签收。

灾备联合演练记录：源checkpoint/日志恢复点、冻结发行、PG任务/授权/发布指针、SR表/副本/备份、模型与索引版本；记录恢复开始/结束、丢失窗口、独立对账和RPO/RTO实测。恢复到独立验收目标，核对后审批切换，不能把QA的PG恢复当生产全链路灾备。

平台签收单至少包含：实际3FE/不少于3BE及副本/故障域、PG HA、API/worker实例及中断预算、TLS握手与CA、Secret轮换、资源组和配额、告警接收人、报警触发/恢复送达证据、审计故障处置、备份保留和一次恢复报告。现有部署/告警声明仍需渲染和实测，不在本轮应用到集群。

## 6. 生产发布证据与回滚（P0-07）

```powershell
python scripts/p0_enterprise.py init --out acceptance.json
python scripts/p0_enterprise.py check --catalog business-catalog.approved.json --bundle acceptance.json --tenant petkit --release CONFIRMED_RELEASE --model-revision CONFIRMED_REVISION --embedding-revision CONFIRMED_BGE_REVISION --enabled-metrics net_sales,tickets,inventory,received_units --out p0-check.json
python -m src.enterprise.ops publish --file independent-counts.json --release CONFIRMED_RELEASE --expected PREVIOUS_RELEASE --approval ENTERPRISE_APPROVAL_REFERENCE --acceptance acceptance.json
```

三个命令均不应照抄占位参数。生产publish现在要求：业务口径/血缘检查通过、P0-01至07逐项真人审阅、real_environment来源、报告文件存在且SHA256匹配、tenant/release/生成及Embedding revision一致；另保留独立对账、完整索引及CAS要求。**证据记录不是密码学签名，也不会自动识别伪造报告**；企业审批系统与审阅人仍是信任边界。不得把本地协议桩报告登记为real_environment。

证据文件必须放bundle目录内，以相对路径引用；禁止外部路径/符号链接逃逸、缺失/篡改文件、未来审阅时间。P0-07的生产前报告包括远端CI、不可变镜像、灰度方案、运行/回滚演练和发布审批；发布后的灰度观察、回滚结果及最终签收作为追加证据，不能以发布前门禁替代它们。预生产publish要求业务口径签收，但不强求生产放行bundle，允许先在预生产取得真实质量和性能证据，避免验收依赖循环。

真实发布前：远端CI通过→登记registry镜像digest与Secret引用→批准用户/范围及观察窗口→CAS发布→观察错误/水位/权限/队列→批准扩大或回滚到已验收发行。回滚版本也必须匹配模型、schema、口径和索引证据；不能强制覆盖发布指针。

## 7. 当前可交付与保留项

已提供可运行校验、冻结评测、HTTPS真实冒烟、有界负载、离线token预算工具、未签收输入模板和生产发布阻断。没有真实源时不生成350条伪业务盲测，没有真实IdP/模型时不声称联调通过；未接通Flink/Airflow、企业告警、GPU、生产HA或灾备，未推送GitHub或生产切流。原一期49项清单的13项外部验收不因工具交付自动勾选。

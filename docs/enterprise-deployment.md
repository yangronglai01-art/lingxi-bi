# 正式部署与启动

## 唯一正式入口

目标Python3.12/Linux，四环境正式分析数据库统一StarRocks 3.5.21；控制面PG17。历史依赖为显式`[legacy]`，不属于生产安装。

```sh
pip install -r requirements-production.txt
python -m src.enterprise.ops migrate
python -m uvicorn src.enterprise.api:create_app --factory --host 0.0.0.0 --port 8080 --no-access-log --limit-concurrency 64
python -m src.enterprise.worker
```

迁移、grant、stage、index、publish使用独立运维账号；API/worker使用`deploy/enterprise/runtime-grants.sql`的DML账号，不能修改授权或发布。角色和密码由Secret系统发放，SQL不内置生产凭据。

`env.example`只列变量名与占位值。生产PG必须verify-full，SR必须CA校验，SSO/底座必须HTTPS；MODEL_GATEWAY_URL应为既有底座的`/v1`，固定调用local-chat和embedding-default，不配置cloud-chat。

每个scope显式提供专属只读用户、数据库、regions数组及password_env；区域与PG授权逐项一致。数据库角色只可读批准发行的物理快照/经验证的视图，不能读取原始表。不得对未经验证的视图语义声称原生RLS。

Embedding由底座托管BGE-M3，不在应用镜像加载Torch或模型权重。先stage发行，再以固定EMBEDDING_REVISION执行index；revision必须对应底座权重/归一化快照。QA无真实BGE时显式BM25技术测试，生产不允许省略revision。

浏览器使用独立public PKCE client；配置OIDC_AUTHORIZATION_ENDPOINT和OIDC_TOKEN_ENDPOINT，注册同源/callback及CORS。应用角色analytics_reader与tenant_id、内省返回结构见identity.py。前端自报角色/地区不授予访问权；生产实际IdP未联调。

## 制品

```sh
docker build -f deploy/enterprise/Dockerfile -t REGISTRY/lingxi:RELEASE .
```

生产采用不可变镜像digest。Kubernetes文件提供双API/双worker、非root、只读根、探针、资源限制、Secret和PDB；平台需填镜像、CA、网关、SSO地址并配置Ingress/TLS。NetworkPolicy只允许DNS及RFC1918中443/5432/9030，需平台收紧到实际服务IP/端口。

健康入口`/health/live`、`/health/ready`；正式接口`/api/v1/analytics/queries`。提交需Bearer和Idempotency-Key，相同键换载荷409；GET状态/结果，POST /{id}/cancel。/ask不再是正式入口。/internal/metrics需全局analyst身份并置于内网，勿经公共Ingress暴露。

当前应用DML连接为逐事务建立/关闭、connect_timeout=5秒，未宣称配置连接池；API进程64并发门槛和3个执行槽不代表生产容量。平台应按PG max_connections与API副本压测后选连接池/PgBouncer。

## 本地/CI隔离验证

`deploy/enterprise/compose.qa.yml`提供全新PG与SR卷和回环端口。现有本机验证复用了SR开发容器中的独立lingxi_enterprise_qa库，而非另建SR集群；未改原petkit500。不要启动同端口两份PG；使用新的Compose项目/端口或停用确认属于自己的QA实例。

```powershell
$env:LINGXI_QA_CONFIRM='lingxi_enterprise_qa'
$env:LINGXI_QA_SR_PORT='59030' # 新QA compose；当前本机既有SR测试命名空间为9030
python scripts/seed_enterprise_qa.py
pytest enterprise_tests -q
```

种子程序拒绝覆盖已有QA库。再次运行直接复用验证，不DROP重建。测试使用公开、限定QA的凭据；不得将QA账号搬入生产。旧tests下破坏性fixture同时要求loopback、*_qa库名和LINGXI_TEST_ALLOW_RESET精确匹配。

GitHub工作流首次需要推送后运行；本轮未推送。开发/CI使用同一产品/发行/SQL语义，不以DuckDB替身验证。

StarRocks FE需启用SSL并设`ssl_force_secure_transport=true`，CA/主机名校验不关闭；参考[官方SSL说明](https://docs.starrocks.io/docs/administration/user_privs/ssl_authentication/)。本机QA未伪称完成生产TLS握手。

冻结评测仅在取得真实服务/Owner参考后执行：`python scripts/evaluate_enterprise.py --cases PRIVATE_CASES.jsonl --out PRIVATE_NEW_OUTPUT --api INTERNAL_APP_URL --model-revision CONFIRMED_REVISION`；Bearer从本机LINGXI_EVAL_TOKEN注入，不记录到结果。禁止把模板题统计为盲测模型成绩。


## P1/P2版本接入

先显式迁移控制面schema3并应用runtime-grants.sql，再使用匹配API/worker/前端版本；增量表回滚时保留。生产新增独立LINGXI_METRICS_TOKEN，仅允许读取聚合指标，业务API拒绝该身份；不得复用网关或SSO Secret。LINGXI_REVIEW_MODE保持disabled，真实消融及审批后才考虑shadow/enforce。恢复工具的SR操作账户与运行reader分离，不能改写运行scope账号。详细命令、保留/回滚策略、静态资源多版本路由及待接入条件见[交付记录](enterprise-p1-p2-delivery.md)。当前未进行生产升级。

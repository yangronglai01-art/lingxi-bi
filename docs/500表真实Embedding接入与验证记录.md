# 500表真实Embedding接入与验证记录

日期：2026-10-17。数据版本：petkit-v1.3-2dc93c9e1c。本项目使用自建模拟数据，不是小佩内部系统或生产交付。

## 已实现

复用本机 `D:\projects\Zhixi\models\bge-m3` 权重，只读加载，未重复下载。CPU推理，1024维，最大序列256 token、批大小8、4线程。500份表概要（含字段说明）及6份指标文本共506条去重向量；字段文档继续参与BM25。真实模型表级召回与BM25通过RRF融合，再执行原有权限筛选、指标依赖和批准关系补全。

向量保存在 `data/petkit500/vector_cache` 的压缩NPZ文件，无pickle。缓存键包含模型目录文件大小/修改时间、路径、序列长度与完整语料哈希；文件锁及原子替换防止并发半写，读取检查形状、有限数值和归一化。模型指纹使用文件统计信息，不宣称是2GB权重逐字节完整性校验。模型切换及元数据变化会产生新缓存。

首次本机构建耗时 187.74 秒，包含模型加载和查询验证；这是本机单次记录，不是性能承诺。持久缓存避免重复编码500张表，进程首次查询仍需加载模型。元数据仍是版本JSON，向量以内存矩阵计算余弦相似度，没有部署pgvector或独立向量数据库。

## 验证结果

- 回归测试：152项通过，1条Starlette测试客户端弃用提示。
- BM25、BM25+字符向量、BGE、BM25+BGE：8道已见开发问题的Top8登记指标依赖完整率均为8/8。各路均含指标依赖文档和核心表权重。这不是独立最小表集标注，不能据此证明BGE带来准确率提升。
- BGE检索下32道批准模板问题全部通过独立Python原始记录聚合核对。属于模板功能验证，不是大模型准确率。
- 真实模型首次2题：1/2通过。净销售额正确；30天激活率分母被LEFT JOIN后事件日期WHERE过滤，示例型号期望分母44，实际35，激活率被高估。
- 增加分母保留规则：追踪简单CTE投影，拒绝对LEFT JOIN可空事件日期进行WHERE过滤，并在生成/修复提示中要求保留完整cohort。新加3项测试；H04单题修复复核通过1/1。首轮报告保留，不改写为首次2/2。
- 实际localhost API调用已验证使用语义检索，净销售额返回12行，临时凭据及子服务清理正常。不是并发或生产负载测试。

## 演示命令

```powershell
cd D:\projects\lingxi
.\.venv\Scripts\python.exe scripts/start_interview.py --mode demo
# 真实SQL生成
.\.venv\Scripts\python.exe scripts/start_interview.py --mode llm
```

启动器会从已成功构建的 `data/petkit500/embedding_runtime.json` 读取模型路径，原有.env密钥未修改。其他CLI入口需显式设置 `$env:PETKIT_EMBEDDING_MODEL='D:\projects\Zhixi\models\bge-m3'`。显式设置空字符串可使用词法基线。换机器时必须安装CPU PyTorch及embedding可选依赖，并使用自己的本地路径重新执行：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_petkit_embeddings.py --model '本地模型目录'
.\.venv\Scripts\python.exe scripts/compare_petkit_embeddings.py
.\.venv\Scripts\python.exe scripts/verify_petkit_embedding_e2e.py
```

`verify_petkit_embedding_e2e.py --llm --ids H04` 会产生模型API费用，属于定向开发复核。仅`start_interview.py`自动读取本地运行配置，基础检索库不隐式开启模型。不要把demo模式说成模型实时生成。

## 证据与限制

证据：`reports/petkit500_embedding_build.json`、`petkit500_embedding_comparison.json`、`petkit500_embedding_e2e.json`、`petkit500_embedding_llm_first.json`、`petkit500_embedding_llm_followup.json`、`petkit500_embedding_http.json`、`petkit500_tests.xml`。

500张物理表由40张核心表和460张扩展合成表组成，不代表支持500张表任意问题。指标语义规则仅覆盖已识别模式，复杂表达式或其他SQL结构仍可能出现错误；不是SQL等价性证明。尚无生产RLS、多实例并发压测或真实企业数据验收。历史12题留出首轮11/12保持原样，新检索开发对比没有替代该历史成绩。

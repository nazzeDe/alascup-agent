# rag-server

运维经验库。LLM 解决问题后将诊断路径与方案写入，下次同类问题直接检索复用。

## 职责

- 暴露知识检索 MCP 工具（语义搜索历史解决方案）
- 暴露知识写入 MCP 工具（存储诊断路径与方案）
- 管理 ChromaDB 向量数据库
- 离线文档预处理和注入

## 非目标

- 不执行运维操作
- 不参与 Agent 推理
- 不处理用户消息

## 技术栈

| 依赖 | 用途 |
|------|------|
| fastmcp | MCP Server 框架 |
| ChromaDB | 向量存储与语义检索 |
| Embedding API（LLM 侧 `/v1/embeddings`） | 嵌入向量生成 |

不内置 embedding 模型，不依赖 PyTorch/sentence-transformers。embedding 由 LLM 的 Embedding API 提供，保证跨架构（x86/loongarch64）可部署。

## 目录结构

```
rag-server/src/
  tools/
    retrieval/      # 检索工具（search_experience：语义搜索历史解决方案）
    writeback/      # 写入工具（save_experience：存储本次诊断路径与解决方案）
    quality/        # 质量控制（mark_experience_invalid：标记失效经验）
  vector_store/     # ChromaDB 管理
  embedding/        # 嵌入模型适配
  preprocessing/    # 文档清洗与分块
```

## 依赖注入

```
class RagServerConfig:
    chroma_client: ChromaClient      # 注入；生产持久化，测试内存模式
    embedding_fn: Callable[[str], list[float]]  # 注入；测试可替换为固定向量
    embedding_api_base: str          # LLM Embedding API 地址（默认 http://localhost:8080/v1）
    similarity_threshold: float      # 可配置；去重阈值
```

生产环境通过 `EMBEDDING_API_BASE` 环境变量指定 Embedding API 地址。

## 知识生命周期

```
问题发生 → LLM 诊断（调用 tool-server 感知 + rag-server 检索历史）
         → LLM 解决（调用 tool-server 操作）
         → LLM 调用 rag-server save_experience 写入方案（需用户审批）
         → 下次同类问题 → search_experience 命中 → 快速解决
         → 经验被证明无效 → LLM 调用 mark_experience_invalid 标记失效
```

**RAG 工具 paramsSchema**

同一症状可能由不同根因导致，采用症状+诊断指纹联合匹配确保对症下药。

```
search_experience {
  query: string           // 自然语言症状描述
  fingerprint: object     // 诊断指纹（key:value 快照），用于精准匹配；可选
  top_k: int (默认 5)     // 返回结果数
}

save_experience {
  symptom: string         // 观测到的症状
  fingerprint: object     // 诊断指纹，见下方标准字段表
  root_cause: string      // 根因
  solution: string        // 解决方案
}

mark_experience_invalid {
  record_id: string       // 要标记失效的经验 ID
  reason: string (可选)    // 失效原因
}
```

**诊断指纹（fingerprint）**

指纹是一组 `key:value` 形式的诊断快照，整组联合匹配才能精准区分"同一症状、不同根因"。预定义标准字段作为起点，LLM 可自行扩展：

| 字段 key | 说明 | 示例值 |
|----------|------|--------|
| `cpu_percent` | 用户态 CPU 占用 | `82` |
| `mem_percent` | 内存占用 | `91` |
| `disk_percent` | 磁盘占用 | `92` |
| `top_process` | CPU/内存 占用最高的进程名 | `mysqld`, `kworker` |
| `log_pattern` | 日志中的关键报错模式 | `Sorting result`, `OOM killer` |
| `service_name` | 受影响的 systemd 服务名 | `nginx`, `mysql` |
| `io_wait` | I/O 等待占比 | `high`, `low` |

LLM 诊断时首先尝试用标准字段填充指纹；当现有字段无法描述问题时，LLM 可创建新字段（如 `oom_score`、`conn_count`）。新字段随 `save_experience` 写入知识库，之后所有诊断复用。

**质量控制**

- **指纹驱动去重**：写入前 rag-server 对比 `symptom + fingerprint` 联合相似度。症状相似但指纹不同 → 视为不同根因，创建新记录；症状和指纹均相似 → 去重，追加引用计数。
- **人工确认写入**：`save_experience` 视为高风险工具，LLM 调用时进入审批流程。写入内容在审批弹窗中展示，用户可修改后批准。
- **失效标记**（高风险，需审批）：当检索到的经验在实践中无效时，LLM 调用 `mark_experience_invalid` 传入 `record_id`，rag-server 标记失效（不物理删除，保留审计追溯）。失效经验不再被 search_experience 返回。

# rag-server — 详细设计

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
| SiliconFlow Embedding API | 嵌入向量生成（`Qwen/Qwen3-Embedding-0.6B`） |
| httpx | HTTP 客户端，复用连接池 |
| tiktoken | token 计数（`o200k_base` 编码），用于文本分块 |
| loguru | 结构化日志 |

不内置 embedding 模型，不依赖 PyTorch / sentence-transformers。embedding 由 SiliconFlow API 提供，保证跨架构（x86 / loongarch64）可部署。

## 目录结构

```
rag-server/src/
  tools/
    retrieval/      # 检索工具（search_experience：语义搜索历史解决方案）
    writeback/      # 写入工具（save_experience：存储本次诊断路径与解决方案）
    quality/        # 质量控制（mark_experience_invalid：标记失效经验）
  vector_store/     # ChromaDB 管理
  embedding/        # SiliconFlow Embedding API 适配
  preprocessing/    # 文档清洗与分块
```

## 配置设计 —— RagServerConfig

### 硬编码常量

以下值不可通过环境变量覆盖：

| 字段 | 值 | 说明 |
|------|----|------|
| `embedding_model` | `Qwen/Qwen3-Embedding-0.6B` | SiliconFlow 模型名 |
| `embedding_dimensions` | `1024` | 向量维度；Qwen3-Embedding-0.6B 支持 32~1024 |
| `similarity_threshold` | `0.90` | 指纹去重余弦相似度阈值 |
| `collection_name` | `operations_knowledge` | ChromaDB collection 名 |
| `chroma_mode` | `persistent` | 持久化模式；测试注入 `memory` |
| `chroma_path` | `./chroma_data` | 持久化路径 |
| `top_k_default` | `5` | 检索默认返回数 |
| `chunk_size` | `8192` | 文档分块 token 上限 |
| `chunk_overlap` | `512` | 相邻 chunk 重叠 token 数 |
| `batch_size` | `64` | embedding API 单次最大输入条数 |
| `max_retries` | `3` | API 调用失败固定重试次数 |
| `request_timeout` | `10.0` | API 请求超时秒数 |

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `EMBEDDING_API_BASE` | 否 | Embedding API 地址，不设则无默认值，启动失败 |
| `EMBEDDING_API_KEY` | 是 | SiliconFlow API Key；缺失则 `sys.exit(1)` 退出 |

```python
@dataclass
class RagServerConfig:
    # 硬编码
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimensions: int = 1024
    similarity_threshold: float = 0.90
    collection_name: str = "operations_knowledge"
    chroma_mode: str = "persistent"
    chroma_path: str = "./chroma_data"
    top_k_default: int = 5
    chunk_size: int = 8192
    chunk_overlap: int = 512
    batch_size: int = 64

    # 环境变量注入
    embedding_api_key: str = ""
    embedding_api_base: str = ""

    # 依赖注入（测试可替换）
    chroma_client: ChromaClient | None = None
    embedding_fn: Callable | None = None
```

## Embedding 设计 —— APIEmbedder

### 接口

```
POST {EMBEDDING_API_BASE}/embeddings
Authorization: Bearer {EMBEDDING_API_KEY}
Content-Type: application/json

{
  "model": "Qwen/Qwen3-Embedding-0.6B",
  "input": "text" | ["text1", "text2", ...],
  "dimensions": 1024,
  "encoding_format": "float"
}
```

响应路径：`data[].embedding`。

### 设计要点

- 使用 `httpx.Client` 复用连接池，连接超时 10s
- `embed(text) -> list[float]` — 单条嵌入
- `embed_batch(texts: list[str]) -> list[list[float]]` — 批量嵌入，自动按 `batch_size=64` 分包
- `count_tokens(text: str) -> int` — 用 `tiktoken` (`o200k_base`) 估算 token 数
- 最大输入：32768 tokens（Qwen3-Embedding 系列上限）

### 重试策略

| 错误类型 | 重试 | 行为 |
|----------|------|------|
| 401 / 403 | 否 | 直接失败，记录 ERROR 日志 |
| 429（限流） | 否 | 直接失败 |
| 连接错误 / 超时 / 5xx | 是，固定 3 次 | 每次间隔 0s（立即重试） |
| 响应格式异常 | 是，固定 3 次 | 同上 |

3 次重试全部失败后抛出 `EmbeddingAPIError`。

### 日志

| 级别 | 场景 |
|------|------|
| ERROR | API 不可达（含重试耗尽）、401/403 认证失败 |
| WARNING | 每次重试（`Embedding API retry 2/3: ConnectionError`） |

正常调用、延迟统计不记录。

## 预处理设计

### 清洗 — `clean_text`

```
输入 → 去 null 字节 → 折叠连续空行为单空行 → 折叠连续空白为单空格 → strip
```

不做 Markdown/HTML 剥离（运维经验文本不包含富格式）。

### 分块 — `chunk_text`

基于 token 的滑动窗口分块，使用 `tiktoken` (`o200k_base`) 编码：

| 参数 | 值 |
|------|----|
| `chunk_size` | 8192 tokens |
| `chunk_overlap` | 512 tokens |

```
文本 → tokenize → 滑动窗口（窗口=8192，步长=8192-512=7680）→ detokenize → chunk 列表
```

单段文本不超过 `chunk_size` 时不拆分，直接返回原文本。

### 管线编排

```
PreprocessingPipeline.run(text) → clean_text → chunk_text → list[str]
```

chunk 后不加标题/上下文前缀。过短 chunk（<10 tokens）不丢弃。

## Vector Store 设计 —— ChromaVectorStore

### Collection Schema

```
name: "operations_knowledge"
metadata: {"hnsw:space": "cosine"}
维度: 1024（硬编码，启动时校验已有 collection 是否一致）
```

### 操作

| 方法 | 说明 |
|------|------|
| `add(embedding, metadata, text) -> str` | 写入向量 + 元数据 + 文档，返回 doc_id（UUID4） |
| `search(embedding, top_k) -> list[dict]` | 余弦相似度检索，返回 `{id, metadata, text, score}` |
| `mark_invalid(doc_id) -> bool` | 标记失效（`valid=False`），物理保留 |
| `get(doc_id) -> dict | None` | 按 ID 查询单条 |
| `count() -> int` | 返回 collection 文档总数 |

### HNSW 索引

保持 ChromaDB 默认值（`M=16`，`construction_ef=100`，`search_ef=10`）。运维经验数据量小（百条级别），默认参数已过度优化。

### 维度锁定

Collection 首次创建时维度由硬编码 `embedding_dimensions=1024` 决定。不支持在线维度迁移；如需变更维度，停服、删除 `chroma_data`、重启即可。

## MCP 工具设计

工具均为静态分级（注册时确定，不需要 classify 往返）：

| 工具 | 分级 | 审批 |
|------|------|------|
| `search_experience` | 只读 | 自动通过 |
| `save_experience` | 写操作 | 需用户确认 |
| `mark_experience_invalid` | 写操作 | 需用户确认 |
| `health` | 只读 | 自动通过 |

### search_experience

```
输入:
  query: string           # 自然语言症状描述
  fingerprint: object     # 诊断指纹 (key:value 快照)，用于精准匹配；可选
  top_k: int (默认 5)     # 返回结果数

输出:
  results: [
    {
      id: string          # 经验记录 ID
      text: string        # 解决方案文本
      metadata: object    # symptom, fingerprint, root_cause, valid
      score: float        # 余弦相似度 (0~1)
    }
  ]
```

内部流程：`query + fingerprint 拼接 → embed → ChromaDB.search → 过滤 valid=True`。

Embedding API 不可用时返回 `{"results": []}`，不阻塞 LLM 诊断流程。

### save_experience

```
输入:
  symptom: string         # 观测到的症状
  fingerprint: object     # 诊断指纹
  root_cause: string      # 根因
  solution: string        # 解决方案

输出:
  status: "saved" | "deduplicated" | "error"
  record_id: string
```

内部流程：`symptom + fingerprint 拼接 → embed → 查重（相似度 ≥ 0.90 则去重）→ 写入或返回已有 ID`。

Embedding API 不可用时返回 `{"status": "error", "reason": "..."}`。

### mark_experience_invalid

```
输入:
  record_id: string       # 要标记失效的经验 ID
  reason: string (可选)   # 失效原因

输出:
  status: "marked_invalid" | "not_found"
  record_id: string
```

设置 `metadata.valid = False`，物理保留记录。失效记录不再被 `search_experience` 返回。

### health

```
输出:
  status: "healthy"
  collection: string
  entry_count: int
  embedding_api_available: bool
```

`embedding_api_available` 通过启动时 ping 一次 Embedding API 获取并缓存，不每次实时探测。

## 知识生命周期

```
问题发生 → LLM 诊断（调用 tool-server 感知 + rag-server 检索历史）
         → LLM 解决（调用 tool-server 操作）
         → LLM 调用 rag-server save_experience 写入方案（需用户审批）
         → 下次同类问题 → search_experience 命中 → 快速解决
         → 经验被证明无效 → LLM 调用 mark_experience_invalid 标记失效
```

## 诊断指纹（fingerprint）

指纹是一组 `key:value` 形式的诊断快照，整组联合匹配才能精准区分"同一症状、不同根因"。预定义标准字段作为起点，LLM 可自行扩展：

| 字段 key | 说明 | 示例值 |
|----------|------|--------|
| `cpu_percent` | 用户态 CPU 占用 | `82` |
| `mem_percent` | 内存占用 | `91` |
| `disk_percent` | 磁盘占用 | `92` |
| `top_process` | CPU/内存占用最高的进程名 | `mysqld`, `kworker` |
| `log_pattern` | 日志中的关键报错模式 | `Sorting result`, `OOM killer` |
| `service_name` | 受影响的 systemd 服务名 | `nginx`, `mysql` |
| `io_wait` | I/O 等待占比 | `high`, `low` |

LLM 诊断时首先尝试用标准字段填充指纹；当现有字段无法描述问题时，LLM 可创建新字段（如 `oom_score`、`conn_count`）。新字段随 `save_experience` 写入知识库，之后所有诊断复用。

指纹序列化格式：`k1=v1 k2=v2 ...`（按 key 排序），同时存储原始 JSON 到 metadata。

### 指纹驱动的 Embedding 拼接

搜索和写入时，symptom 和 fingerprint 拼接为单一文本再嵌入：

```
search_text = "{symptom} {fingerprint_key=value pairs}"
```

这确保语义搜索同时考虑症状描述和诊断指纹。

## 质量控制

- **指纹驱动去重**：写入前对比 `symptom + fingerprint` 联合嵌入的余弦相似度。相似度 ≥ 0.90 → 去重，返回已有 `record_id`；<0.90 → 创建新记录。
  - 症状相似但指纹不同 → 视为不同根因，创建新记录
  - 症状和指纹均高度相似 → 去重
- **人工确认写入**：`save_experience` 需用户审批。写入内容在审批弹窗中展示，用户可修改后批准。
- **失效标记**（需审批）：`mark_experience_invalid` 标记 `valid=False`，物理保留确保审计追溯。失效经验不再被 `search_experience` 返回。

## 错误处理与降级

| 错误 | 行为 |
|------|------|
| `EMBEDDING_API_KEY` 缺失 | 启动时 `sys.exit(1)` + 明确错误信息 |
| Embedding API 401/403 | 不重试，记录 ERROR 日志，search 返回空结果，save 返回 error |
| Embedding API 连接超时/5xx | 固定 3 次重试；耗尽后同 401 处理 |
| chroma_data 维度不匹配 | ChromaDB 自身报错，服务启动失败 |
| ChromaDB 读写异常 | 捕获后记录 ERROR 日志，工具返回 error |

## 日志

使用 `loguru`。最小排错日志：

| 级别 | 场景 |
|------|------|
| ERROR | Embedding API 不可达（含重试耗尽）、401/403、ChromaDB 异常 |
| WARNING | 每次 API 重试（记录第 n 次） |

工具调用成功、API 正常调用不记录。

## 测试策略

### 单元测试

使用 `FakeVectorStore` 和 `FakeEmbedder`，确定性、无外部依赖：

- `FakeVectorStore` — 内存实现，朴素余弦相似度计算
- `FakeEmbedder` — 用 `hashlib.shake_256` 生成 1024 维确定性向量：`[b/255.0 for b in shake.digest(1024)]`

覆盖：

| 编号 | 用例 | 验收标准 |
|------|------|---------|
| RG-001 | 指纹精准检索 | 匹配指纹的结果排在最前 |
| RG-002 | 不同指纹→新建 | 相同症状、不同指纹创建独立记录 |
| RG-003 | 相似指纹→去重 | 相同症状+高度相似指纹触发去重 |
| RG-004 | 失效标记排除 | 标记失效后 search 不返回 |
| RG-005 | 端到端 round-trip | save→search 可检索 |
| RG-006 | batch embed 分包 | 超 batch_size 自动分多次 API 调用 |
| RG-007 | token 计数 | tiktoken 计数与 chunk 切分正确 |

### 集成测试

使用 ChromaDB 内存模式 + `FakeEmbedder`，测试 MCP 工具完整调用链：

- 工具注册（4 个工具全部存在）
- search → save → mark_invalid 完整生命周期
- MCP 通信协议（fastmcp Client 调用）

## 依赖 —— pyproject.toml

```toml
[project]
name = "rag-server"
version = "0.1.0"
description = "运维经验库 — 语义检索与知识写入"
requires-python = ">=3.13"
dependencies = [
    "chromadb>=0.5",
    "fastmcp>=3.2.4",
    "httpx>=0.27",
    "loguru>=0.7",
    "tiktoken>=0.7",
]

[project.optional-dependencies]
test = ["pytest>=7", "pytest-cov>=6"]

[build-system]
requires = ["hatchling>=1.27.0"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src"]
```

## 部署

### Dockerfile

```dockerfile
ARG BASE_IMAGE=ghcr.io/loong64/python:3.13.13-slim-trixie
FROM ${BASE_IMAGE}

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev

COPY src/ src/

EXPOSE 11452

CMD ["uv", "run", "python", "-m", "src.main"]
```

不再用 `pip install .`（缺少 lock 文件不可重现）。`uv sync --frozen --no-dev` 按 lock 文件精确安装生产依赖。

### 环境变量

```yaml
environment:
  - EMBEDDING_API_BASE=https://api.siliconflow.cn/v1
  - EMBEDDING_API_KEY=sk-xxxx
```

### Docker 启动

```
docker compose up rag-server
```

服务监听 `0.0.0.0:11452`，transport 为 `streamable-http`。

### 资源限制

| 资源 | 限制 |
|------|------|
| 内存 | 512m |
| CPU | 0.50 |
| PID | 128 |
| 文件系统 | read-only，仅 `/tmp` 可写 |

## 开发调试

```bash
cd rag-server
EMBEDDING_API_BASE=https://api.siliconflow.cn/v1 \
EMBEDDING_API_KEY=sk-xxxx \
uv run python -m src.main
```

# rag-server 测试

## 测试对象

| 模块 | 单元测试 | 集成测试 |
|------|---------|---------|
| ChromaDB CRUD | 内存模式 | 临时目录持久化 |
| 嵌入适配 | 固定向量 mock | 真实 embedding |
| 去重阈值判定 | 相似度计算 | — |
| 文档分块 | 分块逻辑 | — |
| MCP Server | — | fastmcp test server 完整链路 |

## 测试用例

### RG-001 指纹精准检索

| 前置 | 两条预置经验：exp-001（CPU 高+慢查询指纹）、exp-004（CPU 高+磁盘 I/O 指纹） |
| 输入 | search_experience(query="CPU 占用高", fingerprint={cpu_percent: 80, top_process: "mysqld", io_wait: "low"}) |
| 预期 | exp-001 排第一（指纹匹配）；exp-004 排后或不返回 |

### RG-002 同症状不同指纹不触发去重

| 前置 | 已有 exp-001（CPU 高 → 慢查询） |
| 输入 | save_experience(symptom="CPU 占用高", fingerprint={cpu_percent: 70, io_wait: "high"}, root_cause="磁盘 I/O 阻塞", ...) |
| 预期 | 创建新记录（指纹不同）；不触发去重 |

### RG-003 指纹相似触发去重

| 前置 | 已有 exp-001（CPU 高 + mysqld + Sorting result 指纹） |
| 输入 | save_experience 写入与 exp-001 症状+指纹均相似的内容 |
| 预期 | 不创建新记录；追加引用计数和案例 ID |

### RG-004 失效标记

| 前置 | 已有可检索经验 |
| 输入 | mark_experience_invalid(record_id)；审批通过 |
| 预期 | 标记失效；search_experience 不再返回该记录；物理保留 |

### RG-005 写入审批

| 前置 | save_experience 视为高风险工具 |
| 输入 | LLM 调用 save_experience |
| 预期 | 进入审批流程；通过后 ChromaDB 可查询到新记录 |

## Mock 配置

### 预置知识库

| record_id | 症状 | 指纹（key:value） | 根因 | 方案 | 分类 |
|-----------|------|-----------------|------|------|------|
| exp-001 | CPU 占用 >80% | cpu_percent=82, top_process=mysqld, log_pattern="Sorting result", io_wait=low | MySQL 查询缺少索引导致 filesort | 为 orders.created_at 添加索引 | cpu, mysql |
| exp-002 | 磁盘空间不足 | disk_percent=92, largest_dir=/tmp/logs | 日志文件堆积未轮转 | 清理 /tmp/logs，配置 logrotate | disk, log |
| exp-003 | Nginx 不可用 | service_name=nginx, log_pattern="unknown directive" | nginx.conf 语法错误 | 修正 nginx.conf 中拼写错误的指令 | nginx, service |
| exp-004 | CPU 占用 >80% | cpu_percent=70, top_process=kworker, io_wait=high | 磁盘 I/O 阻塞导致 kworker 内核线程自旋 | 更换故障磁盘，迁移数据 | cpu, disk |

注：exp-002 的 `largest_dir` 为标准字段无法覆盖时 LLM 自建的新字段。

### 去重验证

写入与 exp-001 症状+指纹均相似时触发去重。仅症状相似但指纹不同（如 exp-004 的 io_wait=high vs exp-001 的 io_wait=low）不触发去重。

### 失效验证

exp-001 标记失效后，"CPU 占用高 + mysqld + io_wait=low"查询不应返回 exp-001，但仍可能返回 exp-004。

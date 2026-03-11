# smart_trans：从 ingest_triplet 事件识别到可视化看板的端到端智能系统

`smart_trans` 是一个交通事故事件级智能分析系统：输入同一事件的三帧图片（t-3s / t-1s / t0），输出结构化事故记录（JSON），并进一步生成“事件归因报告”和“法规定性 + 条款引用”。数据可入库至 FastAPI + SQLite 后端，并通过 React 仪表盘进行可视化展示；同时支持蜂鸣器告警扩展。

本文档基于完整代码走读，围绕 `POST /api/ingest_triplet` 这条事件级主链路，说明端到端数据流、关键模块设计，以及可观测性与降级策略。

项目目录：`/Users/mekeypan/opencode/projects/smart_trans`

## 1. 设计亮点

### 亮点一：事件级闭环（triplet → 归因/法条 → 入库 → 看板回溯）

`smart_trans` **不只是单帧识别脚本，而是事件级闭环产品形态**：

- **输入侧**：一次上报三帧（`POST /api/ingest_triplet`），立即返回 `job_id`，后台异步处理
- **处理侧**：三帧并行帧级分析 → 事件级汇总（cause/report/key_facts）→ 法规检索定性（含引用校验）
- **输出侧**：结果入库 SQLite；前端详情页可回溯三帧与条款引用（并支持蜂鸣器扩展）

### 亮点二：LLM 分层协作 + 引用强约束（可复盘、可追溯、可降级）

**传统方案的痛点**：多帧场景直接让 LLM 输出“原因/责任/法条”，容易出现结论抖动、编造细节、条文引用不可信，最终导致难以复盘与工程化。

本项目的设计原则是“分层输出 + 强约束校验”：

- 帧级分析输出结构化字段（可对照三帧差异，便于排障）
- 事件级汇总输出严格 JSON（归因/报告/要点），允许不确定但禁止编造
- 法规引用必须来自检索片段原文子串；不满足会自动修复，并提供 fallback，保证链路不断流

## 2. 系统架构

### 2.1 数据流总览

| 阶段 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| **① 事件上报** | 三帧图片（t-3s/t-1s/t0） | FastAPI 接收 multipart：`POST /api/ingest_triplet` | 落盘至 `backend/uploads/`；返回 `job_id` 与 `frames[]` |
| **② 位置兜底** | 单帧图片 | EXIF GPS 解析 + 坐标水印 stamp（best-effort） | `stamp_lat/lng/text` 写入 job；为地图展示兜底 |
| **③ 帧级分析** | 三帧图片路径 | subprocess 调用 `traffic_issue_analyzer.py`（`task=rag/accident`） | 每帧 `analysis` JSON + stdout/stderr 日志 |
| **④ 事件汇总** | 三帧帧级 JSON | 调用 summary LLM 生成 cause/report/key_facts | summary JSON + raw 响应落盘 |
| **⑤ 法规定性** | summary + 关键字段 | `law_rag.py` 检索 `rag/law_kb.jsonl` + LLM 定性 + 引用校验 | `legal_qualitative` + `law_refs[]`（含 quote） |
| **⑥ 数据入库** | 事件级结构化记录 | SQLAlchemy 写入 SQLite（`accidents` 表） | SQLite 持久化（含 triplet 扩展字段） |
| **⑦ 可视化** | SQLite 数据 | React 页面读取 `/api/stats/*`、`/api/accidents/*` | Dashboard 大屏 + 详情页三帧回溯 |

<aside>
📌

**端到端流程：**

三帧 → 异步并行分析 → 事件归因 → 法规引用 → 入库 → 看板回溯（可选蜂鸣器告警）

</aside>

### 2.2 组件启动方式

根据需求选择启动相应组件：

| 需求场景 | 启动组件 |
| --- | --- |
| 仅获取帧级 JSON 输出 | `traffic_issue_analyzer.py` |
| 事件级 ingest_triplet（主线） | backend（FastAPI） |
| 入库 + 前端展示 | backend，frontend |
| 脚本上报（扩展） | `pipeline_rag.py` / `send_triplet_http.py` |
| 蜂鸣器告警（扩展） | `beep_mcp_server.py`（供脚本调用） |

## 3. 核心模块说明

### 3.1 ingest_triplet：事件级入口与任务编排（`backend/app/routes/ingest.py`）

该模块实现 `POST /api/ingest_triplet`：三帧落盘、创建 job、后台并行分析、事件汇总、法规检索与入库。

**接口字段：**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `frame_t3` | file | t-3s 帧 |
| `frame_t1` | file | t-1s 帧 |
| `frame_t0` | file | t0 帧（作为入库主帧） |
| `hint` | string? | 补充线索（可选） |
| `task` | string | `rag`（推荐）/ `accident` |
| `extract_runs` | int | RAG 抽取次数（默认 3） |

**任务编排与可观测性：**

| 存储位置 | 内容 |
| --- | --- |
| `incoming/jobs/<job_id>.json` | Job 元数据、状态、frames、result、error |
| `incoming/job_artifacts/<job_id>/frames/<key>/` | 每帧 `analyzer.stdout.txt`、`analyzer.stderr.txt` |
| `incoming/job_artifacts/<job_id>/summary.*.txt` | 事件汇总 prompt/response |
| `incoming/job_artifacts/<job_id>/law.*.txt` | 法规定性 prompt/response |

**事件级工作流程：**

1. 三帧保存到 `backend/uploads/`，生成 frames 元信息（按时间顺序 t-3s → t-1s → t0）
2. 对每帧尝试 EXIF GPS，并 best-effort stamp 坐标水印（用于地图兜底）
3. 创建 job 并返回 `job_id`（后台线程开始执行）
4. 三帧并行跑帧级分析器 `traffic_issue_analyzer.py`
5. 基于三帧结果做事件级汇总（cause/report/key_facts）
6. 基于汇总与检索片段做法规定性与引用校验，最后写入 SQLite（以 t0 为主帧）

<aside>
⚙️

**stamp 说明：** 当前实现中，若未设置 `SMART_TRANS_STAMP_COORDS`，默认会执行 stamp；只有显式设置为 falsy 才会跳过。

</aside>

### 3.2 事件级汇总：Triplet Summary（严格 JSON）

`ingest_triplet` 在拿到三帧的帧级 `analysis` 后，会调用 summary LLM 做事件级归纳。

**输出 JSON Schema（summary）：**

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `cause` | string | 1-3 句中文；不确定需说明不确定与原因 |
| `report` | string | 详细分析报告；建议包含过程还原/关键证据/不确定性/建议补充信息 |
| `key_facts` | string[] | 3-12 条，按重要性排序，尽量客观可验证 |

<aside>
⚠️

**重要约束：** 事件级汇总允许指出矛盾与不确定性，但不要编造图片中不存在的细节。

</aside>

**工程化特性：**

| 特性 | 说明 |
| --- | --- |
| 可配置模型 | `SMART_TRANS_SUMMARY_API_KEY/BASE_URL/MODEL`（未配置可复用 `SILICONFLOW_*`） |
| 输出清洗 | `key_facts` 去空、单条截断、总条数上限 |
| 可追溯 | 原始 response（raw）会落盘到 job artifacts，便于复盘 |

### 3.3 法规检索与定性（`backend/app/law_rag.py` + `rag/law_kb.jsonl`）

系统将 summary 的 cause/report/key_facts 以及帧级关键字段拆词扩展，检索法规 KB 的相关片段，再让模型输出“定性结论 + 引用”。

**KB 构建：**

- `tools/build_law_kb.py` 支持从 `rag/trans_doc/` 下的 pdf/doc/docx 抽取文本，按“章/节/条”切分，生成 `rag/law_kb.jsonl`
- 运行时可通过 `SMART_TRANS_LAW_KB` 指向自定义 KB 路径

**输出 JSON Schema（law）：**

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `legal_qualitative` | string | 文字定性结论；不确定需说明不确定，但给出最可能情形 |
| `law_refs` | array | 1-6 条，每条含 `snippet_id/source/title/quote/relevance` |

<aside>
⚠️

**重要约束：** `law_refs[].quote` 必须是输入 `law_snippets[].snippet` 的原文子串（直接复制句子），不要编造条文。

</aside>

**降级与修复：**

| 场景 | 行为 |
| --- | --- |
| KB 不存在/检索不到片段 | 生成兜底引用（提示需要生成 `rag/law_kb.jsonl` 或设置 `SMART_TRANS_LAW_KB`） |
| 模型输出引用不合法 | 自动将 `quote` 修复为 snippet 前缀，并在 relevance 标注 `(quote auto-adjusted)` |
| 模型调用失败 | 输出低确定性的 `legal_qualitative` + refs 兜底，保证事件记录仍可入库 |

### 3.4 帧级分析器：`traffic_issue_analyzer.py`

该模块提供帧级结构化能力，为事件汇总与法规定性提供稳定输入。

| 模式 | 说明 |
| --- | --- |
| `--task accident` | 直接让模型输出严格 JSON，本地做字段归一化 |
| `--task rag`（推荐） | 先抽取“可见事实”，多次运行聚合后由本地规则确定性落地 |
| `--task label` | 旧版单标签输出（演示用途） |

RAG 模式工作流程：

1. LLM 输出“可见事实 observations”（结构化字段）
2. 多次运行（`--extract-runs`）聚合降低抖动
3. 基于 `rag/rules.json` 做确定性决策（类型/严重程度/置信度）
4. 从 `rag/knowledge.md` 检索片段写入 trace（提升可解释性）

工程化特性：

| 特性 | 说明 |
| --- | --- |
| 结果缓存 | `.cache/smart_trans/accident_rag/`；key 与图片 hash + rules_version 相关 |
| Trace 持久化 | RAG trace 写入 `raw_model_output`；超长会截断（约 18000 字符） |

### 3.5 后端服务（backend/）

基于 FastAPI + SQLite 构建，提供事件写入与看板查询能力。

| 类型 | 接口 |
| --- | --- |
| 事件级上报 | `POST /api/ingest_triplet` |
| Job 查询 | `GET /api/jobs/{job_id}`、`GET /api/jobs` |
| 记录 | `POST /api/accidents`、`GET /api/accidents`、`GET /api/accidents/{id}` |
| 统计 | `GET /api/stats/*` |

关键字段：

- 事件级：`cause`、`legal_qualitative`、`law_refs_json`
- 回溯：`raw_model_output`（可能包含 `triplet_job_id=<job_id>`），并由后端读取 `incoming/jobs/<job_id>.json` 补充 `frames[]`
- 位置：`location_text/lat/lng/location_source/location_confidence`

Schema 迁移机制：

`ensure_sqlite_schema()` 在启动时检查表结构，缺失列时自动执行 `ALTER TABLE` 补充（包含 triplet 扩展字段）。

### 3.6 前端应用（frontend/）

基于 React + Vite 构建，核心页面如下：

| 页面 | 功能 |
| --- | --- |
| Dashboard | 统计概览、类型/严重度分布、时间线、地理聚合 |
| Accidents | 分页列表与筛选 |
| Detail | 展示事故详情、事件归因、法规引用；若为 triplet 事件则回溯三帧 frames |

### 3.7 蜂鸣器/物联网告警（扩展）

`beep_mcp_server.py` 将华为云 IoTDA 的蜂鸣器控制封装为 MCP 工具 `set_beep`（SSE）。

触发流程：

1. `pipeline_rag.py` 在入库成功且 `has_accident=true` 时
2. 通过 `llm_mcp_client.py` 的 `beep_n()` 连接蜂鸣器服务
3. 根据严重程度响铃（轻微 1 次 / 中等 2 次 / 严重 3 次）

#### 3.7.1 LLM 调用工具（演示）

`llm_mcp_client.py` 的 `AlarmAssistant` 演示了 **LLM function calling + MCP tool** 的模式：

- 通过 `list_tools` 获取可用工具列表
- 通过 `call_tool` 执行工具调用
- 让模型自主决定何时调用 `set_beep`

目前主流程仍以脚本规则触发 beep 为主，LLM 调用模式作为扩展演示。

#### 3.7.2 小结

当前推荐使用 HTTP 直接上报 triplet，蜂鸣器告警作为可选扩展保留。

## 4. 快速开始

### 4.1 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

### 4.2 配置环境变量

创建 `.env`（可参考 `.env.example`）：

```bash
SILICONFLOW_API_KEY=your_key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=Qwen/Qwen3-VL-32B-Instruct

# 可选：事件级汇总模型（不配则复用 SILICONFLOW_*）
# SMART_TRANS_SUMMARY_API_KEY=your_key
# SMART_TRANS_SUMMARY_BASE_URL=https://api.siliconflow.cn/v1
# SMART_TRANS_SUMMARY_MODEL=Pro/deepseek-ai/DeepSeek-V3.2

# 可选：法规 KB
# SMART_TRANS_LAW_KB=rag/law_kb.jsonl
```

### 4.3 启动后端

```bash
PYTHONPATH=backend uvicorn app.main:app --reload --port 28000
```

### 4.4 事件级三帧上报（ingest_triplet）

```bash
curl -sS -X POST "http://127.0.0.1:28000/api/ingest_triplet" \
  -F "frame_t3=@t-3s.jpg" \
  -F "frame_t1=@t-1s.jpg" \
  -F "frame_t0=@t0.jpg" \
  -F "task=rag" \
  -F "extract_runs=3" \
  -F "hint=固定机位路口抓拍，疑似变道冲突"
```

### 4.5 查询 job 状态与入库结果

轮询 job：

```bash
curl -sS "http://127.0.0.1:28000/api/jobs/<job_id>"
```

拿到 `accident_id` 后查询详情：

```bash
curl -sS "http://127.0.0.1:28000/api/accidents/<accident_id>"
```

（可选）启动前端（开发模式）：

```bash
cd frontend
npm install
npm run dev
```

（可选）启动蜂鸣器服务：

```bash
python3 beep_mcp_server.py
```

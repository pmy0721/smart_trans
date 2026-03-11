# smart_trans 源码解读

面向开发者的项目级源码导览：解释整体架构、关键模块、数据流、接口与配置点，帮助快速上手二次开发与排障。

## 目录

- [项目概览](#项目概览)
- [整体架构与数据流](#整体架构与数据流)
- [目录结构与模块职责](#目录结构与模块职责)
- [核心：traffic_issue_analyzer 分析器](#核心traffic_issue_analyzer-分析器)
- [端到端：pipeline_rag 流水线](#端到端pipeline_rag-流水线)
- [后端：FastAPI + SQLite](#后端fastapi--sqlite)
- [前端：React + Vite](#前端react--vite)
- [MCP：收图触发流水线 + 蜂鸣器](#mcp收图触发流水线--蜂鸣器)
- [配置与环境变量](#配置与环境变量)
- [API 速查](#api-速查)
- [落盘产物与排障路径](#落盘产物与排障路径)
- [已知约束与风险点](#已知约束与风险点)
- [建议的运行方式](#建议的运行方式)

---

## 项目概览

`smart_trans` 是一个“交通事故智能台（console）”项目：从图片生成结构化事故记录，使用多模态 LLM 做事实抽取，随后用本地规则（可追溯）输出事故类型/严重程度/置信度/位置字段，并支持入库到 FastAPI + SQLite，最后由前端仪表盘可视化展示。

核心目标：

- **结构化输出**：输出严格 JSON（事故与位置字段统一约束）。
- **确定性与可追溯**：RAG 模式下“最终分类”来自本地规则；模型主要负责“事实抽取”，同时把 trace 写入 `raw_model_output` 便于复盘。
- **可扩展管线**：支持 HTTP 收图异步分析入库、轻量脚本上报、以及 IoT 蜂鸣器报警。

---

## 整体架构与数据流

简图：

```
image
  ├─ (A) HTTP 异步入库：POST /api/ingest_triplet
  │       ├─ 保存 t-3s/t-1s/t0 到 uploads/
  │       ├─ 创建 job（落盘 incoming/jobs）
  │       ├─ 后台并行调用 traffic_issue_analyzer.py
  │       ├─ 事件级 summary + 法规检索
  │       └─ 入库 SQLite + 可选 beep
  │
  ├─ (B) 端到端：pipeline_rag.py
  │       └─ triplet CLI（提交到 /api/ingest_triplet，并可等待 job）
  │
  └─ (C) 脚本上报：pipeline_rag.py / send_triplet_http.py
          └─ 提交到 /api/ingest_triplet + 轮询 /api/jobs/{job_id}
```

---

## 目录结构与模块职责

项目根目录（关键文件/目录）：

- `traffic_issue_analyzer.py`：帧级分析器（由 triplet 流程调用）。
- `pipeline_rag.py`：triplet CLI 封装；提交三帧到 `/api/ingest_triplet`。
- `rag/`
  - `rag/rules.json`：RAG 模式的本地规则（accident_type/severity/confidence 权重）。
  - `rag/knowledge.md`：RAG 检索的知识片段，用于 trace 解释（不决定最终分类）。
- `backend/`：FastAPI + SQLite 后端
  - `backend/app/main.py`：应用入口、路由挂载、静态文件托管。
  - `backend/app/models.py`：SQLAlchemy 模型（Accident 表）。
  - `backend/app/schemas.py`：Pydantic schema（入参/出参）。
  - `backend/app/routes/*.py`：API 路由（uploads/accidents/stats/ingest/jobs）。
- `frontend/`：React + Vite 前端
  - `frontend/src/pages/*`：仪表盘/列表/详情
  - `frontend/src/api/*`：请求封装与类型
  - `frontend/src/map/*`：高德地图加载与渲染
  - `frontend/vite.config.ts`：dev 端口、代理、build 输出到后端 static
- 上报脚本与蜂鸣器
  - `send_triplet_http.py`：HTTP 客户端脚本，提交三帧并可轮询 job。
  - `beep_mcp_server.py`：IoTDA 蜂鸣器 MCP server（工具 `set_beep`）。
  - `llm_mcp_client.py`：封装 `beep_n()`，以及一个“LLM 调工具”示例 demo。
- 工具脚本
  - `backend/app/stamp_coords.py`：后端 ingest 用的“坐标水印”实现（杭州 bbox，deterministic）。
  - `tools/stamp_coords.py`：独立脚本，给图片批量打坐标水印（更通用的 anchor 列表）。

---

## 核心：traffic_issue_analyzer 分析器

文件：`traffic_issue_analyzer.py`

### 1) 三种 task 模式

- `--task label`：输出单个标签（历史/简化模式）。
- `--task accident`：让模型直接输出严格 JSON（包含事故判断与位置字段）。
- `--task rag`（推荐）：两段式
  1. **事实抽取**：模型输出“可见事实 JSON”（不直接给最终事故类型/严重程度结论）。
  2. **本地规则判定 + trace**：
     - 用 `rag/rules.json` 规则集对事实进行匹配，得到确定性的 `accident_type` 与 `severity`。
     - 用权重规则计算 `confidence`。
     - 从 `rag/knowledge.md` 里做轻量检索，把 top_k 片段摘要放到 trace（用于解释）。

### 2) 输出 JSON 的关键字段

RAG 模式最终输出（示例字段）：

- `has_accident`：bool（在 RAG 模式里更接近“是否存在碰撞/事故证据”）
- `accident_type`：规则输出（allowed 集合见 `rag/rules.json`）
- `severity`：规则输出（`轻微/中等/严重`）
- `description`：优先复用事实抽取的 `description_facts`，不足则用模板拼接
- `confidence`：规则计算（`base + weights`，并有保守上限）
- `location_text/lat/lng/location_source/location_confidence`：归一化后的位置信息
- `raw_model_output`：trace（包含 facts、命中规则、检索片段、部分原始抽取输出）

### 3) 确定性与可追溯的实现点

- 规则文件：`rag/rules.json`
  - `accident_type.rules[]`：按 `priority` 高到低匹配 `when` 条件，写入 `set`
  - `severity.rules[]`：同理
  - `confidence.weights`：按事实字段加权得到最终置信度（并做保守上限）
- 知识库：`rag/knowledge.md`
  - 以 `##/###` 为 chunk 边界
  - 通过“关键词计数”进行粗检索，将片段 `snippet` 附到 trace（用于解释，不决定最终分类）

### 4) 缓存

- RAG 模式默认写缓存到：`.cache/smart_trans/accident_rag/`
- cache key：`sha256(image) + rules_version + extractor_version`
- 参数：
  - `--no-cache`：禁用缓存
  - `--refresh-cache`：忽略缓存重新计算

---

## 端到端：pipeline_rag 流水线

文件：`pipeline_rag.py`

### 1) 管线步骤

1. 校验三帧参数：`--frame-t3/--frame-t1/--frame-t0`
2. 提交 multipart 到 `POST /api/ingest_triplet`
3. 可选 `--wait` 轮询 `/api/jobs/{job_id}` 到 `done/failed`

---

## 后端：FastAPI + SQLite

目录：`backend/app/`

### 1) 应用入口与静态托管

文件：`backend/app/main.py`

- 注册路由：uploads / accidents / stats / ingest / jobs
- CORS：`SMART_TRANS_CORS_ORIGIN`（默认 `http://localhost:25173`）
- 静态托管：
  - `/uploads` 挂载图片目录（默认 `backend/uploads/`）
  - 若存在 `backend/static/index.html`：
    - `/assets` 挂载构建产物
    - `/` 与 SPA fallback：非 `/api|/uploads|/assets` 的路径都返回 index.html

### 2) 数据库与迁移策略

文件：`backend/app/db.py`

- `SMART_TRANS_DB`：SQLite 路径（默认 `backend/data/accidents.db`）
- `ensure_sqlite_schema()`：对旧库做“增量加列”，避免手动迁移（添加 location 字段与 `raw_model_output`）

### 3) 数据模型

文件：`backend/app/models.py`

`Accident` 表关键字段：

- 业务字段：`has_accident/accident_type/severity/description/confidence`
- 来源字段：`source/hint/image_path`
- 位置字段：`location_text/lat/lng/location_source/location_confidence`
- 可追溯：`raw_model_output`

### 4) 路由与职责

- `backend/app/routes/uploads.py`
  - `POST /api/uploads`：保存图片并尝试解析 EXIF GPS，返回 `{image_path, image_url, exif}`
- `backend/app/routes/accidents.py`
  - `POST /api/accidents`：写入一条记录（severity 会规范化到 `轻微/中等/严重`）
  - `GET /api/accidents`：分页+过滤（`has_accident/severity/type/start/end`）
  - `GET /api/accidents/{id}`：详情
- `backend/app/routes/stats.py`
  - `GET /api/stats/summary`：总数/近7天/严重数/严重占比
  - `GET /api/stats/by_type`：按类型计数
  - `GET /api/stats/by_severity`：按严重程度计数（固定顺序）
  - `GET /api/stats/timeline`：按日时间线
  - `GET /api/stats/geo`：lat/lng round 分桶
- `backend/app/routes/ingest.py`
  - `POST /api/ingest_triplet`：收三帧 -> 创建 job -> 后台并行分析与事件汇总 -> 入库 -> 可选 beep
  - job 落盘：`incoming/jobs/<job_id>.json`；日志：`incoming/job_artifacts/<job_id>/analyzer.*.txt`
- `backend/app/routes/jobs.py`
  - `GET /api/jobs` / `GET /api/jobs/{job_id}`：查询 ingest job 状态（含 result/error）

### 5) 位置与时间工具

文件：`backend/app/utils.py`

- SQLite 存储用“北京时区 naive datetime”，对外返回用 BJT aware
- `try_extract_exif_gps()`：Pillow 解析 EXIF GPSInfo（DMS->deg）
- `image_url_for_path()`：约定 `image_path="uploads/<file>"` => `image_url="/uploads/<file>"`

---

## 前端：React + Vite

目录：`frontend/`

### 1) 页面与路由

文件：`frontend/src/App.tsx`

- `/`：`DashboardPage`
- `/accidents`：`AccidentsPage`
- `/accidents/:id`：`AccidentDetailPage`

### 2) API 客户端

文件：`frontend/src/api/client.ts`

- `requestJson()`：默认 `fetch` `${VITE_API_BASE}${path}`
- 主要接口：`listAccidents/getAccident/getSummary/getByType/getBySeverity/getTimeline/getGeoBuckets`

### 3) 仪表盘与可视化

文件：`frontend/src/pages/DashboardPage.tsx`

- 并发拉取 summary/type/severity/timeline/geo
- 用 ECharts 画柱状/饼图/折线
- 用 `AmapView` 画地理分布 CircleMarker（radius 由 count 的 log2 映射）

### 4) 高德地图封装

- `frontend/src/map/amap.ts`
  - `VITE_AMAP_KEY` 必填（构建时注入）
  - 可选 `VITE_AMAP_SECURITY_CODE`
  - 坐标模式：`VITE_AMAP_COORD_MODE=gps|gcj`，默认 gps；gps 模式会调用 Convertor 转 gcj
- `frontend/src/map/AmapView.tsx`
  - 支持 marker / circles
  - props 变更时清图并重绘 overlays
  - key 缺失时直接显示错误提示

### 5) Vite 配置与单端口部署

文件：`frontend/vite.config.ts`

- dev server：`25173`
- proxy：
  - `/api`、`/uploads` 代理到 `http://43.138.60.74:28000`
- build：
  - `outDir: '../backend/static'`
  - 用后端托管静态资源实现单端口部署（`http://localhost:28000`）

---

## 上报与蜂鸣器

### 1) 三帧 HTTP 上报脚本

文件：`send_triplet_http.py`

- 直接调用 `POST /api/ingest_triplet`（multipart）
- 可选 `--wait` 轮询 `GET /api/jobs/{job_id}`

### 2) 蜂鸣器 MCP Server

文件：`beep_mcp_server.py`

- 工具：`set_beep(state)`，通过华为云 IoTDA 下发设备命令
- Server：SSE（默认 `9010`），URL：`http://<host>:9010/sse`

### 3) beep 调用封装

文件：`llm_mcp_client.py`

- `beep_n(n, url, on_time, gap)`：通过 MCP SSE 调 `set_beep`，并校验返回文本（最佳努力确保 off）
- 同文件包含一个“LLM 调工具”的 demo（非主链路）

---

## 配置与环境变量

参考：`.env.example`

### 1) LLM（必需）

- `SILICONFLOW_API_KEY`（必需）
- `SILICONFLOW_BASE_URL`（默认 `https://api.siliconflow.cn/v1`）
- `SILICONFLOW_MODEL`（默认 `Qwen/Qwen3-VL-32B-Instruct`）

### 2) 后端（可选）

- `SMART_TRANS_DB`：SQLite 路径
- `SMART_TRANS_UPLOADS`：上传目录
- `SMART_TRANS_CORS_ORIGIN`：允许跨域来源

### 3) 作业队列（可选）

- `SMART_TRANS_INCOMING_DIR`
- `SMART_TRANS_PIPELINE_MAX_CONCURRENCY`

### 4) 蜂鸣器（可选）

- `SMART_TRANS_ENABLE_BEEP` / `SMART_TRANS_DISABLE_BEEP`
- `SMART_TRANS_BEEP_MCP_URL` / `SMART_TRANS_BEEP_MCP_PORT`
- `SMART_TRANS_BEEP_ON_TIME` / `SMART_TRANS_BEEP_GAP`
- IoTDA：`HUAWEICLOUD_AK/SK/ENDPOINT/REGION_ID/DEVICE_ID`（建议仅通过 env 配置）

### 5) 坐标水印（可选）

- `SMART_TRANS_STAMP_COORDS=1`：开启 ingest 收图后“坐标水印”
- `SMART_TRANS_STAMP_HZ_BBOX`：默认杭州 bbox
- `SMART_TRANS_STAMP_SEED`：保证按文件名确定性生成

---

## API 速查

后端统一前缀：`/api`

- `POST /api/uploads`：上传图片（返回 `image_path/image_url/exif`）
- `POST /api/accidents`：写事故记录
- `GET /api/accidents`：列表分页+筛选
- `GET /api/accidents/{id}`：详情
- `GET /api/stats/summary|by_type|by_severity|timeline|geo`：统计
- `POST /api/ingest_triplet`：HTTP 三帧异步分析入库（返回 `job_id`）
- `GET /api/jobs`：job 列表
- `GET /api/jobs/{job_id}`：job 状态（含 result/error）

---

## 落盘产物与排障路径

- DB：`backend/data/accidents.db`（默认）
- 上传图片：`backend/uploads/`（默认）
- 前端 build：`backend/static/`
- MCP/ingest 任务目录：`incoming/`
  - `incoming/jobs/<job_id>.json`
  - `incoming/job_artifacts/<job_id>/pipeline.stdout.txt`
  - `incoming/job_artifacts/<job_id>/pipeline.stderr.txt`
  - ingest analyzer 日志（HTTP ingest 场景）：`incoming/job_artifacts/<job_id>/analyzer.*.txt`

---

## 已知约束与风险点

- 坐标体系：前端默认将后端坐标按 GPS 转 GCJ；若存储坐标为 GCJ，应设置 `VITE_AMAP_COORD_MODE=gcj` 避免二次偏移。
- 密钥风险：`beep_mcp_server.py` 当前存在 AK/SK 默认值（高风险）；建议只从环境变量读取并移除默认硬编码。
- trace 体积：`raw_model_output` 有长度限制（后端 schema `max_length=20000`），代码中做了截断策略，但需要关注扩展时体积膨胀。

---

## 建议的运行方式

1) 入库 + 前端可视化

- 启动后端（28000）
- 前端 dev（25173）或 `npm run build` 后由后端托管静态资源（单端口 28000）

2) 脚本化三帧上报

- 用 `pipeline_rag.py` 或 `send_triplet_http.py` 提交三帧并可 `--wait` 轮询结果

3) 蜂鸣器报警

- 启动 `beep_mcp_server.py`（9010）
- triplet job 侧按后端配置触发蜂鸣器（满足“成功入库 + has_accident=true”）

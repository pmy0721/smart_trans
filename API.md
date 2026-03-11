# smart_trans 接口文档（Markdown 版）

> 适用项目：`/Users/mekeypan/opencode/projects/smart_trans`
> 
> 本文覆盖：后端 HTTP API（FastAPI）与 MCP SSE（工具协议）两部分。

---

## 0. 基础信息

- 后端默认地址：`http://127.0.0.1:28000`
- API 前缀：`/api`
- 数据库：SQLite（默认 `backend/data/accidents.db`）
- 图片静态访问：
  - 上传目录挂载：`GET /uploads/<filename>`（后端 `main.py` mount）
  - `image_path` 约定：`uploads/<filename>`
  - `image_url` 约定：`/uploads/<filename>`
- 单端口部署（可选）：
  - 若存在 `backend/static/index.html`，后端会：
    - `GET /` 返回前端 SPA
    - `GET /assets/*` 返回构建产物
    - 其余非 `/api|/uploads|/assets` 路径 fallback 到 `index.html`

---

## 1. 数据结构（Schemas）

### 1.1 UploadResponse

```json
{
  "image_path": "uploads/<filename>",
  "image_url": "/uploads/<filename>",
  "exif": {
    "lat": 30.1234,
    "lng": 120.1234,
    "location_confidence": 1.0,
    "location_source": "exif"
  }
}
```

- `exif`：可能为 `null`（无 EXIF 或解析失败）

### 1.2 AccidentCreate（写入）

```json
{
  "has_accident": true,
  "accident_type": "追尾",
  "severity": "中等",
  "description": "……",
  "confidence": 0.82,

  "source": "script",
  "image_path": "uploads/xxx.jpg",
  "hint": "可选提示",

  "location_text": null,
  "lat": 30.12,
  "lng": 120.12,
  "location_source": "exif",
  "location_confidence": 1.0,

  "raw_model_output": "{...}",

  "cause": "（可选，triplet）",
  "legal_qualitative": "（可选，triplet）",
  "law_refs": [
    {
      "snippet_id": "xxxx",
      "source": "道路交通安全法.doc",
      "title": "第X章 第Y条",
      "quote": "……",
      "relevance": "……"
    }
  ]
}
```

约束/行为（后端会做一定纠正）：
- `severity` 非 `轻微/中等/严重`：会被纠正（有事故默认 `中等`，无事故默认 `轻微`）
- `confidence`：会 clamp 到 `[0, 1]`
- `raw_model_output`：最大长度 20000（超出会截断或由上游保证）

### 1.3 AccidentRead（读取）

```json
{
  "id": 1,
  "created_at": "2026-02-12T10:11:12+08:00",

  "source": "script",
  "image_path": "uploads/xxx.jpg",
  "image_url": "/uploads/xxx.jpg",
  "hint": "可选",

  "has_accident": true,
  "accident_type": "追尾",
  "severity": "中等",
  "description": "……",
  "confidence": 0.82,

  "location_text": null,
  "lat": 30.12,
  "lng": 120.12,
  "location_source": "exif",
  "location_confidence": 1.0,

  "raw_model_output": "{...}",

  "cause": "（可选）",
  "legal_qualitative": "（可选）",
  "law_refs": [
    {"snippet_id":"...","source":"...","title":"...","quote":"...","relevance":"..."}
  ],

  "frames": [
    {"key":"t0","image_path":"uploads/...","image_url":"/uploads/..."},
    {"key":"t-1s","image_path":"uploads/...","image_url":"/uploads/..."},
    {"key":"t-3s","image_path":"uploads/...","image_url":"/uploads/..."}
  ]
}
```

说明：
- `frames`：仅当该记录由 triplet 流程产生（`raw_model_output` 含 `triplet_job_id=<job_id>`）时，后端会从 `incoming/jobs/<job_id>.json` 读取并回填；否则返回单帧 fallback（key=`t0`）。

### 1.4 AccidentListResponse（列表）

```json
{
  "items": [ /* AccidentRead[] */ ],
  "total": 123,
  "page": 1,
  "page_size": 20
}
```

### 1.5 Stats

- SummaryStats

```json
{
  "total": 1000,
  "last_7d": 88,
  "severe": 12,
  "severe_ratio": 0.012
}
```

- BucketCount

```json
{ "key": "追尾", "count": 10 }
```

- TimelinePoint

```json
{ "date": "2026-02-01", "count": 3 }
```

- GeoBucket

```json
{ "lat": 30.23, "lng": 120.18, "count": 5 }
```

---

## 2. Uploads API（图片上传）

### POST `/api/uploads`

上传图片并尝试解析 EXIF GPS。

- Content-Type：`multipart/form-data`
- Body：
  - `file`：图片文件（必填）

响应：
- 200：`UploadResponse`

---

## 3. Accidents API（事故记录增删查）

### POST `/api/accidents`

写入一条事故记录。

- Content-Type：`application/json`
- Body：`AccidentCreate`

响应：
- 200：`AccidentRead`

---

### GET `/api/accidents`

分页列表 + 过滤。

Query：
- `page`：int，默认 1，>=1
- `page_size`：int，默认 20，1..100
- `has_accident`：bool，可选
- `severity`：string，可选（`轻微|中等|严重`）
- `type`：string，可选（对应字段 `accident_type`）
- `start`：string，可选，ISO datetime
  - naive：按 Asia/Shanghai 本地时间解释
  - aware：会转到 Asia/Shanghai 再转 naive 存储口径比较
- `end`：同上

响应：
- 200：`AccidentListResponse`

---

### GET `/api/accidents/{accident_id}`

获取单条详情。

Path：
- `accident_id`：int

响应：
- 200：`AccidentRead`
- 404：`{"detail":"not found"}`

---

## 4. Stats API（统计）

### GET `/api/stats/summary`

响应：
- 200：`SummaryStats`

---

### GET `/api/stats/by_type`

响应：
- 200：`BucketCount[]`（按 count 降序）

---

### GET `/api/stats/by_severity`

响应：
- 200：`BucketCount[]`（顺序：轻微 → 中等 → 严重）

---

### GET `/api/stats/timeline`

Query：
- `days`：int，默认 30，1..365

响应：
- 200：`TimelinePoint[]`

---

### GET `/api/stats/geo`

Query：
- `precision`：int，默认 2，0..6（对 lat/lng round 的小数位）
- `limit`：int，默认 200，1..2000

响应：
- 200：`GeoBucket[]`

---

## 5. Ingest API（HTTP 收图异步分析入库）

> Ingest 会创建 triplet job，并在后台线程运行三帧流程；job 状态通过 Jobs API 查询。
> 
> Job 与日志文件落盘在 `incoming/`。

---

### POST `/api/ingest_triplet`

三帧 ingest：保存 t-3s / t-1s / t0（含 EXIF/水印坐标处理）→ 并行分析 →（best-effort）生成原因报告 →（best-effort）法律定性 + 引用 → 写入 accidents 表。

- Content-Type：`multipart/form-data`
- Body：
  - `frame_t0`：UploadFile（必填）
  - `frame_t1`：UploadFile（必填）
  - `frame_t3`：UploadFile（必填）
  - `hint`：string（可选）
  - `task`：string（可选，默认 `rag`，允许 `rag|accident`）
  - `extract_runs`：int（可选，默认 3；RAG 模式有效）

可选限流：
- 当配置 `SMART_TRANS_PIPELINE_MAX_INFLIGHT>0` 且当前 `queued+running` 已达上限时，接口返回 `429`。

响应：

```json
{
  "job_id": "<32hex>",
  "status": "queued",
  "created_at": "YYYY-MM-DDTHH:MM:SS",
  "frames": [
    {"key":"t-3s","image_path":"uploads/...","image_url":"/uploads/..."},
    {"key":"t-1s","image_path":"uploads/...","image_url":"/uploads/..."},
    {"key":"t0","image_path":"uploads/...","image_url":"/uploads/..."}
  ]
}
```

落库要点（triplet）：
- 事故主记录以 `t0` 帧为代表（image_path/location 等以 t0 为准）
- `cause` / `legal_qualitative` / `law_refs` 会写入 accidents 表（best-effort；失败有兜底输出）
- `raw_model_output` 会追加：`triplet_job_id=<job_id>`，供后端在事故详情中回查 `frames`

---

## 6. Jobs API（异步任务状态）

### GET `/api/jobs`

Query：
- `limit`：int，默认 50，1..200

响应：

```json
{
  "ok": true,
  "items": [
    {
      "id": "<job_id>",
      "created_at": "YYYY-MM-DDTHH:MM:SS",
      "status": "queued|running|done|failed",

      "image_path": "uploads/...",
      "saved_file": "/abs/path/to/backend/uploads/....jpg",
      "hint": null,
      "task": "rag",
      "extract_runs": 3,

      "frames": null,

      "started_at": "...",
      "finished_at": "...",
      "returncode": 0,
      "command": ["python", "..."],
      "stdout": "...(tail)...",
      "stderr": "...(tail)...",
      "result": { /* analyzer result 或 triplet 汇总 */ },
      "accident_id": 123,
      "error": null,

      "beep_attempted": false,
      "beep_ok": false,
      "beep_error": null,

      "stamp_ok": false,
      "stamp_lat": null,
      "stamp_lng": null,
      "stamp_text": null,
      "stamp_error": null
    }
  ]
}
```

---

### GET `/api/jobs/{job_id}`

响应：
- 存在：

```json
{ "ok": true, "status": "queued|running|done|failed", "job": { /* 同上 job 结构 */ } }
```

- 不存在：

```json
{ "ok": false, "error": "job not found" }
```

---

## 7. MCP SSE（蜂鸣器）

### 7.1 MCP 蜂鸣器服务（`beep_mcp_server.py`）

- 默认 SSE 地址：`http://<host>:9010/sse`
- 工具：

#### Tool: `set_beep`

参数：
- `state`: `"on" | "off"`

返回（文本为主）：
- 成功：例如 `"蜂鸣器已开启"` / `"蜂鸣器已关闭"`
- 失败：返回错误说明文本（华为云 API 错误、配置错误等）

#### Tool: `get_device_status`

返回：
- 文本：设备名称与在线/离线状态（best-effort）

---

## 8. 常用调用示例（可选）

### 8.1 上传图片并入库（脚本侧）

- 先 `POST /api/uploads` 得到 `image_path`
- 再 `POST /api/accidents` 写记录（带 `image_path`）

### 8.2 Triplet HTTP ingest

- `POST /api/ingest_triplet`（multipart，3 个 frame 字段）
- 轮询 job，完成后可去 `GET /api/accidents/{accident_id}` 查看 `frames/cause/legal_qualitative/law_refs`

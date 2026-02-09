# smart_trans

一个“交通事故智能台 (console)”项目：从图片生成结构化事故记录，可选先跑 YOLO 标注，再用多模态 LLM + RAG/规则输出事故类型/严重程度，并可入库到 FastAPI+SQLite，最后用前端仪表盘可视化。

核心组件：

- `traffic_issue_analyzer.py`: 单张图分析（输出严格 JSON；支持 `--task rag` 带可追溯 trace）。
- `pipeline_yolo_rag.py`: 端到端流水线（YOLO 标注 -> analyzer -> 可选 upload/post -> 可选 beep）。
- `backend/`: FastAPI + SQLite（事故记录/统计/图片上传）。
- `frontend/`: React + Vite（仪表盘 + 列表 + 详情；支持 build 到 `backend/static/` 单端口部署）。
- `mcp_image_server.py` + `send_images_mcp.py`: MCP(SSE) 局域网图片“发送 -> 接收 -> 立刻触发 pipeline”（每张图一个 job）。
- `beep_mcp_server.py`: 蜂鸣器 MCP Server（SSE，默认 `http://localhost:9010/sse`）。

数据流（简图）：

```
image -> (optional MCP receive) -> pipeline_yolo_rag.py -> (optional /api/uploads + /api/accidents) -> backend DB -> frontend
                               \-> (optional beep via MCP)
```

## 你需要启动哪些服务？（快速判断）

- 只想拿到“分析 JSON”（不入库、不前端）：不需要启动 backend/ frontend。
- 想“入库+前端可视化”：需要启动 backend（28000），可选启动 frontend dev（25173）或 build 单端口。
- 想“局域网发送图片自动跑 pipeline”：需要启动 `mcp_image_server.py`；若还要入库则再启动 backend。
- 想“蜂鸣器响”：pipeline 必须传 `--beep`，并且需要 `beep_mcp_server.py` 可用（或 `--beep-start-server`）。

## 环境准备

### 1) 安装 Python 依赖

```bash
python3 -m pip install -r requirements.txt
```

说明：

- MCP 图片接收/发送使用 `fastmcp` + `mcp`（已包含在 `requirements.txt`）。
- `pipeline_yolo_rag.py` 默认会尝试跑 YOLO 标注，如果你没装 YOLO 依赖（例如 `torch`、`opencv-python`），会报类似 `No module named 'torch'`。
  - 如果你只想跑“纯 LLM 分析”，用 `traffic_issue_analyzer.py` 即可。

### 2) 配置模型（必须）

本项目默认走 SiliconFlow 的 OpenAI 兼容接口，至少需要：

- `SILICONFLOW_API_KEY`

推荐写到 `.env`（文件已在 `.gitignore` 里，不要提交密钥）。你也可以复制 `.env.example`：

```bash
SILICONFLOW_API_KEY=your_key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=Qwen/Qwen3-VL-32B-Instruct
```

### 3) 常用可选环境变量（可不配）

- Backend
  - `SMART_TRANS_DB`：SQLite 路径（默认 `backend/data/accidents.db`）
  - `SMART_TRANS_UPLOADS`：上传目录（默认 `backend/uploads/`）
  - `SMART_TRANS_CORS_ORIGIN`：CORS 允许来源（默认 `http://localhost:25173`）
- MCP Image Receiver
  - `SMART_TRANS_IMAGE_MCP_HOST`：绑定地址（默认 `0.0.0.0`）
  - `SMART_TRANS_IMAGE_MCP_PORT`：端口（默认 `9011`）
  - `SMART_TRANS_IMAGE_MCP_ADVERTISE_HOST`：仅影响启动提示打印的 host（当 bind 为 `0.0.0.0` 时有用）
  - `SMART_TRANS_INCOMING_DIR`：接收落盘目录（默认 `incoming/`）
  - `SMART_TRANS_PIPELINE_MAX_CONCURRENCY`：pipeline 并发（默认 `1`）
  - `SMART_TRANS_PIPELINE_DEFAULT_CLI`：默认透传给 pipeline 的参数（JSON list 或空格分隔字符串）

## 使用方式 A：仅本地分析（不入库）

### 1) 直接用 analyzer 生成 JSON

```bash
python3 traffic_issue_analyzer.py -i input_image/image1.jpg --task rag
```

你会在终端看到一段 JSON 输出（包含 `has_accident/accident_type/severity/lat/lng/...`）。

### 2) 可选：给图片打坐标水印（提升坐标稳定性）

```bash
python3 tools/stamp_coords.py input_image/image4.jpg
```

批量给目录生成 `*_stamped` 文件（不覆盖原图）：

```bash
python3 tools/stamp_coords.py --dir input_image --write-map
```

## 使用方式 B：入库 + 前端可视化（backend + frontend）

### 1) 启动后端（28000）

```bash
PYTHONPATH=backend uvicorn app.main:app --reload --port 28000
```

预期现象：

- SQLite 自动创建在 `backend/data/accidents.db`
- 上传目录自动创建在 `backend/uploads/`

### 2) 启动前端（开发模式，25173）

```bash
cd frontend
npm install
npm run dev
```

打开：`http://localhost:25173`

### 3) 用脚本写入一条事故记录（upload + post）

```bash
python3 traffic_issue_analyzer.py \
  -i input_image/image1.jpg \
  --task rag \
  --upload http://localhost:28000/api/uploads \
  --post http://localhost:28000/api/accidents
```

验证：

- 列表：`GET http://localhost:28000/api/accidents`
- 统计：`GET http://localhost:28000/api/stats/summary`

### 4) 单端口部署（backend 直接托管前端）

```bash
cd frontend
npm run build
PYTHONPATH=backend uvicorn app.main:app --port 28000
```

打开：`http://localhost:28000`

## 使用方式 C：端到端 YOLO + RAG pipeline（可选入库）

说明：这个流程会先跑 YOLO 输出标注图（写入 `output/`），再对“标注图”进行分析。

如果你不想执行 YOLO（例如没装 `torch`/`opencv-python`，或只想直接分析原图），加 `--skip-yolo`：pipeline 会跳过标注步骤，直接对原图执行分析与（可选）入库。

```bash
python3 pipeline_yolo_rag.py \
  -i input_image/image1.jpg \
  --yolo-weights yolov11/best.pt \
  --task rag \
  --upload http://localhost:28000/api/uploads \
  --post http://localhost:28000/api/accidents

跳过 YOLO 的示例：

```bash
python3 pipeline_yolo_rag.py \
  -i input_image/image1.jpg \
  --skip-yolo \
  --task rag \
  --upload http://localhost:28000/api/uploads \
  --post http://localhost:28000/api/accidents
```
```

输出与落点：

- 标注图：`output/`
- 若入库成功：前端会显示 `image_path` 对应的标注图

## 蜂鸣器（beep）

触发条件（必须全部满足）：

- pipeline 调用时显式传 `--beep`
- 同时传 `--post ...` 且 POST 成功
- 后端返回 `has_accident=true`

另外：如果你使用 `POST /api/ingest`（HTTP 收图 + 异步入库），可以通过环境变量 `SMART_TRANS_ENABLE_BEEP=1` 在“入库成功且 has_accident=true”后触发蜂鸣器（best-effort）。

### 方式 1：手动启动蜂鸣器 MCP Server

安装蜂鸣器依赖（仅在需要蜂鸣器时安装）：

```bash
python3 -m pip install -r requirements-beep.txt
```

```bash
python3 beep_mcp_server.py
```

然后再跑 pipeline：

```bash
python3 pipeline_yolo_rag.py \
  -i input_image/image1.jpg \
  --task rag \
  --upload http://localhost:28000/api/uploads \
  --post http://localhost:28000/api/accidents \
  --beep
```

### 方式 2：pipeline 自动拉起（best-effort）

```bash
python3 pipeline_yolo_rag.py -i input_image/image1.jpg --task rag --upload http://localhost:28000/api/uploads --post http://localhost:28000/api/accidents --beep --beep-start-server
```

注意：蜂鸣器默认 MCP URL 是 `http://localhost:9010/sse`，如需修改用 `--beep-mcp-url` 或环境变量 `SMART_TRANS_BEEP_MCP_URL`。

## 使用方式 D：MCP 局域网收图触发 pipeline（每张图立刻触发）

这一模式适合：手机/摄像头端把图片发给一台“接收机”，接收机收到后立刻跑 `pipeline_yolo_rag.py`。

### 1) 在接收机上启动 MCP 图片接收服务（绑定 0.0.0.0）

```bash
python3 mcp_image_server.py --host 0.0.0.0 --port 9011
```

查接收机局域网 IP（macOS Wi-Fi 常见）：

```bash
ipconfig getifaddr en0
```

假设输出为 `<server-ip>`，则客户端连接地址为：

- `http://<server-ip>:9011/sse`

### 2) 在发送机上发送图片（并等待完成）

```bash
python3 send_images_mcp.py --server http://<server-ip>:9011/sse input_image/image1.jpg --wait
```

批量发送（目录）：

```bash
python3 send_images_mcp.py --server http://<server-ip>:9011/sse --dir input_image --wait
```

### 3) MCP 模式下“入库 + 蜂鸣器”的完整示例

先在接收机启动 backend（如果你要入库/前端展示）：

```bash
PYTHONPATH=backend uvicorn app.main:app --reload --port 28000
```

然后在发送机执行（透传给 pipeline 的参数必须显式传）：

```bash
python3 send_images_mcp.py \
  --server http://<server-ip>:9011/sse \
  --pipeline-cli=--task --pipeline-cli=rag \
  --pipeline-cli=--upload --pipeline-cli=http://localhost:28000/api/uploads \
  --pipeline-cli=--post --pipeline-cli=http://localhost:28000/api/accidents \
  --pipeline-cli=--beep \
  input_image/image1.jpg --wait

如果你想在 pipeline 中跳过 YOLO 或禁用蜂鸣器，可透传参数：

```bash
python3 send_images_mcp.py \
  --server http://<server-ip>:9011/sse \
  --pipeline-cli=--task --pipeline-cli=rag \
  --pipeline-cli=--skip-yolo \
  --pipeline-cli=--no-beep \
  --pipeline-cli=--upload --pipeline-cli=http://localhost:28000/api/uploads \
  --pipeline-cli=--post --pipeline-cli=http://localhost:28000/api/accidents \
  input_image/image1.jpg --wait
```
```

说明：

- `--beep` 不会默认开启，必须传。
- `--upload/--post` URL 是“接收机视角”的地址：如果 backend 也在接收机本机跑，`localhost:28000` 是正确的。

### 4) MCP 模式产物与排查

- Job 元数据：`incoming/jobs/<job_id>.json`
- pipeline 日志：`incoming/job_artifacts/<job_id>/pipeline.stdout.txt` / `incoming/job_artifacts/<job_id>/pipeline.stderr.txt`

## 常见问题（Troubleshooting）

### 1) MCP 客户端连接失败（Connection refused）

原因常见是服务端只绑定了 `127.0.0.1` 或端口未放通。

检查：

- 接收机启动命令是否包含 `--host 0.0.0.0`
- 客户端 URL 是否为 `http://<server-ip>:9011/sse`
- 防火墙是否允许 9011 入站

### 2) pipeline 报 `No module named 'torch'` / `cv2`

这是 YOLO 依赖缺失导致。两种解决路线：

- 安装 YOLO 依赖（torch/opencv 等）后再跑 `pipeline_yolo_rag.py`
- 不跑 YOLO，改用 `traffic_issue_analyzer.py` 直接分析

### 3) 蜂鸣器不响

请逐条对照：

- 是否在 pipeline 参数里显式传了 `--beep`
- 是否传了 `--post` 且成功入库
- 后端返回的 `has_accident` 是否为 `true`
- `beep_mcp_server.py` 是否可访问（默认 `http://localhost:9010/sse`）

排查日志：查看 `incoming/job_artifacts/<job_id>/pipeline.stderr.txt`。

## 目录与默认路径

- DB：`backend/data/accidents.db`（自动创建）
- 上传：`backend/uploads/`（自动创建）
- 前端 build：`backend/static/`
- pipeline 输出：`output/`
- MCP 收图与任务：`incoming/`

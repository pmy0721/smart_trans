# smart_trans

三帧事件级交通事故智能分析系统。输入同一事件的 `t-3s / t-1s / t0` 三帧图片，后端异步完成多帧分析、事故归因、法规检索和入库，前端提供仪表盘、记录列表与详情可视化。

## 1. 项目能力一览

- 三帧 ingest：`POST /api/ingest_triplet`
- 异步作业：`/api/jobs`、`/api/jobs/{job_id}`
- 事故数据：`/api/accidents`（列表/详情）
- 统计可视化：类型分布、严重程度、时间趋势、地理分桶
- 可选能力：法规 RAG、蜂鸣器 MCP、坐标水印

## 2. 项目结构

```text
smart_trans/
├── backend/                    # FastAPI + SQLite + 静态托管
│   ├── app/
│   │   ├── routes/             # ingest / accidents / stats / jobs / uploads
│   │   ├── models.py
│   │   └── main.py
│   ├── data/                   # SQLite 数据库（运行时）
│   ├── uploads/                # 上传图片（运行时）
│   └── static/                 # 前端构建产物
├── frontend/                   # React + Vite
├── pipeline_rag.py             # Triplet CLI（支持 --wait 轮询）
├── send_triplet_http.py        # 轻量 triplet HTTP 上报脚本
├── traffic_issue_analyzer.py   # 核心分析器
├── tools/build_law_kb.py       # 法规知识库构建工具
├── rag/trans_doc/              # 法规原始文档（可上传）
├── rag/law_kb.jsonl            # 生成后的知识库（可上传）
└── input_image/                # 示例输入图片（可上传）
```

## 3. 快速开始

### 3.1 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

### 3.2 配置环境变量

```bash
cp .env.example .env
```

至少需要配置（模型调用必填）：

```env
SILICONFLOW_API_KEY=your_key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=Qwen/Qwen3-VL-32B-Instruct
```

可选（蜂鸣器 MCP）：

- `HUAWEICLOUD_AK`
- `HUAWEICLOUD_SK`
- `HUAWEICLOUD_ENDPOINT`
- `HUAWEICLOUD_REGION_ID`
- `HUAWEICLOUD_DEVICE_ID`

### 3.3 启动后端

```bash
PYTHONPATH=backend uvicorn app.main:app --reload --port 28000
```

### 3.4 启动前端（开发模式）

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

访问：`http://localhost:25173`

## 4. 使用方式

### 4.1 方式 A：直接调用 Triplet API（推荐）

```bash
curl -sS -X POST "http://127.0.0.1:28000/api/ingest_triplet" \
  -F "frame_t3=@input_image/trans01.jpg" \
  -F "frame_t1=@input_image/trans02.jpg" \
  -F "frame_t0=@input_image/trans03.jpg" \
  -F "task=rag" \
  -F "extract_runs=3"
```

查询作业状态：

```bash
curl -sS "http://127.0.0.1:28000/api/jobs/<job_id>"
```

### 4.2 方式 B：使用 CLI（pipeline_rag.py）

```bash
python3 pipeline_rag.py \
  --frame-t3 input_image/trans01.jpg \
  --frame-t1 input_image/trans02.jpg \
  --frame-t0 input_image/trans03.jpg \
  --task rag \
  --wait
```

### 4.3 方式 C：轻量 HTTP 脚本

```bash
python3 send_triplet_http.py \
  --frame-t3 input_image/trans01.jpg \
  --frame-t1 input_image/trans02.jpg \
  --frame-t0 input_image/trans03.jpg \
  --wait
```

## 5. 端到端处理流程

```text
triplet frames
  -> /api/ingest_triplet
  -> create async job (queued/running/done/failed)
  -> per-frame analysis + triplet summary + law RAG
  -> write accidents record (t0 as representative image)
  -> frontend dashboard/list/detail
```

## 6. 前端说明

- 仪表盘页（`/`）：每 10 秒自动刷新
- 事故列表页（`/accidents`）：每 5 秒自动刷新
- 页面不可见时暂停轮询，回到页面后立即刷新

## 7. 可选组件

### 7.1 法规 RAG

- 原始文档目录：`rag/trans_doc/`
- 生成知识库：`rag/law_kb.jsonl`
- 生成命令：

```bash
python3 tools/build_law_kb.py --src rag/trans_doc --out rag/law_kb.jsonl
```

### 7.2 蜂鸣器 MCP

启动：

```bash
python3 beep_mcp_server.py
```

默认地址：`http://localhost:9010/sse`

## 8. 常见问题

### 8.1 轮询一直不结束怎么办？

- 先看 `GET /api/jobs/{job_id}` 是否已返回 `status=done/failed`
- 检查后端日志是否有模型调用失败或网络超时

### 8.2 前端地图不显示怎么办？

- 检查 `VITE_AMAP_KEY`
- 如需安全密钥，补充 `VITE_AMAP_SECURITY_CODE`
- 变更 `VITE_*` 后需要重新构建前端

### 8.3 前端构建有大包警告怎么办？

- 这是 `echarts` 带来的体积提示，不影响运行
- 可后续做分包（`manualChunks`）或按需加载

## 9. 相关文档

- 接口文档：`API.md`
- 源码导读：`SOURCE_CODE_GUIDE.md`

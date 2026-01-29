# smart_trans

Minimal smart-traffic accident console:

- `traffic_issue_analyzer.py`: analyze an image via a multimodal LLM and output a strict JSON accident analysis.
- `backend/`: FastAPI + SQLite API to store accidents and serve uploads.
- `frontend/`: React + Vite dashboard (builds into `backend/static/` for single-port serving).

## Quickstart (dev)

1) Install Python deps

```bash
python3 -m pip install -r requirements.txt
```

2) Start backend (port 8000)

```bash
PYTHONPATH=backend uvicorn app.main:app --reload --port 8000
```

3) Start frontend dev server (port 5173)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Build single-port (backend serves frontend)

```bash
cd frontend
npm run build
PYTHONPATH=backend uvicorn app.main:app --port 8000
```

Open `http://localhost:8000`.

## Ingest accidents from the script

1) Upload + analyze + store

```bash
python3 traffic_issue_analyzer.py \
  -i input_image/image1.jpg \
  --upload http://localhost:8000/api/uploads \
  --post http://localhost:8000/api/accidents
```

2) Verify

- List API: `GET http://localhost:8000/api/accidents`
- Dashboard stats: `GET http://localhost:8000/api/stats/summary`

## Notes

- DB path: `backend/data/accidents.db` (auto-created)
- Uploads path: `backend/uploads/` (auto-created)
- CORS default allows `http://localhost:5173`.

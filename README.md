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
  --task rag \
  --upload http://localhost:8000/api/uploads \
  --post http://localhost:8000/api/accidents
```

Notes:
- `--task rag` uses deterministic local rules for `accident_type/severity` and attaches a RAG trace in `raw_model_output`.

2) Verify

- List API: `GET http://localhost:8000/api/accidents`
- Dashboard stats: `GET http://localhost:8000/api/stats/summary`

## Add virtual coordinates to images

Use `tools/stamp_coords.py` to stamp a top-right watermark like `Lat: 23.162414, Lng: 113.241440`.

### Stamp a specific image (in-place overwrite)

```bash
python3 tools/stamp_coords.py input_image/image4.jpg
```

Multiple files:

```bash
python3 tools/stamp_coords.py input_image/image4.jpg input_image/image5.jpg
```

Write a coords map next to the images:

```bash
python3 tools/stamp_coords.py input_image/image4.jpg --write-map
```

Optional: backup before overwriting:

```bash
python3 tools/stamp_coords.py input_image/image4.jpg --backup
```

### Batch stamp a directory (creates suffixed copies)

This mode does NOT overwrite originals; it creates `*_stamped` files and skips ones that already exist.

```bash
python3 tools/stamp_coords.py --dir input_image --write-map
```

If you need to regenerate outputs, add `--overwrite`.

## Notes

- DB path: `backend/data/accidents.db` (auto-created)
- Uploads path: `backend/uploads/` (auto-created)
- CORS default allows `http://localhost:5173`.

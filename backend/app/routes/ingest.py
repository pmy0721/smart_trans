from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncio

from fastapi import APIRouter, File, Form, UploadFile

from app.db import SessionLocal
from app.models import Accident
from app.stamp_coords import stamp_inplace_hz
from app.utils import clamp01, image_url_for_path, now_bjt_naive, try_extract_exif_gps, uploads_dir


router = APIRouter(prefix="/api", tags=["ingest"])


def _repo_root() -> Path:
    # backend/app/routes/ingest.py -> backend/app/routes -> backend/app -> backend -> repo root
    return Path(__file__).resolve().parents[3]


def _incoming_root() -> Path:
    base = os.getenv("SMART_TRANS_INCOMING_DIR", "incoming")
    p = Path(base)
    if not p.is_absolute():
        p = (_repo_root() / p).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _jobs_root() -> Path:
    p = _incoming_root() / "jobs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _job_artifacts_root(job_id: str) -> Path:
    p = _incoming_root() / "job_artifacts" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _safe_image_suffix(filename: str | None) -> str:
    suf = Path(filename or "").suffix.lower()
    if suf in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
        return suf
    return ".jpg"


def _parse_result_from_stdout(stdout: str) -> dict[str, Any] | None:
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]

    # Analyzer usually prints a single JSON line. Be robust to extra logs.
    for ln in reversed(lines[-30:]):
        if ln.startswith("{") and ln.endswith("}"):
            try:
                obj = json.loads(ln)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue

    s = (stdout or "").strip()
    if s.startswith("{") and s.endswith("}"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None

    return None


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s)
        except Exception:
            return None
    return None


@dataclass
class Job:
    id: str
    created_at: str
    status: str
    image_path: str
    saved_file: str
    hint: str | None = None
    task: str = "rag"
    extract_runs: int = 3

    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None
    command: list[str] | None = None
    stdout: str | None = None
    stderr: str | None = None
    result: dict[str, Any] | None = None
    accident_id: int | None = None
    error: str | None = None

    beep_attempted: bool = False
    beep_ok: bool = False
    beep_error: str | None = None

    stamp_ok: bool = False
    stamp_lat: float | None = None
    stamp_lng: float | None = None
    stamp_text: str | None = None
    stamp_error: str | None = None


def _env_flag(name: str) -> bool:
    v = os.getenv(name, "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _severity_to_beeps(severity: str | None) -> int:
    s = (severity or "").strip()
    if s == "轻微":
        return 1
    if s == "中等":
        return 2
    if s == "严重":
        return 3
    return 1


async def _beep_n_via_mcp(*, n: int, url: str, on_time: float, gap: float) -> None:
    # Best-effort dependency: mcp is in requirements.txt, but keep error readable.
    from mcp import ClientSession  # type: ignore
    from mcp.client.sse import sse_client  # type: ignore

    count = max(0, int(n))
    if count <= 0:
        return

    on_time_s = max(0.0, float(on_time))
    gap_s = max(0.0, float(gap))

    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                for i in range(count):
                    await session.call_tool("set_beep", arguments={"state": "on"})
                    if on_time_s:
                        await asyncio.sleep(on_time_s)
                    await session.call_tool("set_beep", arguments={"state": "off"})
                    if gap_s and i != count - 1:
                        await asyncio.sleep(gap_s)
            finally:
                try:
                    await session.call_tool("set_beep", arguments={"state": "off"})
                except Exception:
                    pass


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def _load_from_disk(self, job_id: str) -> Job | None:
        p = _jobs_root() / f"{job_id}.json"
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return Job(**data)
        except Exception:
            return None

    def put(self, j: Job) -> None:
        with self._lock:
            self._jobs[j.id] = j
        self._persist(j)

    def get(self, job_id: str) -> Job | None:
        jid = (job_id or "").strip()
        if not jid:
            return None
        with self._lock:
            j = self._jobs.get(jid)
        if j is not None:
            return j

        return self._load_from_disk(jid)

    def update(self, job_id: str, **kwargs: Any) -> Job | None:
        jid = (job_id or "").strip()
        if not jid:
            return None

        j: Job | None
        with self._lock:
            j = self._jobs.get(jid)

        if j is None:
            j = self._load_from_disk(jid)
            if j is None:
                return None
            with self._lock:
                self._jobs[jid] = j

        with self._lock:
            j2 = self._jobs.get(jid)
            if j2 is None:
                return None
            for k, v in kwargs.items():
                if hasattr(j2, k):
                    setattr(j2, k, v)
            j = j2

        self._persist(j)
        return j

    def list(self, limit: int = 50) -> list[Job]:
        lim = max(1, int(limit))

        # Prefer reading from disk so list survives restarts.
        root = _jobs_root()
        items: list[Job] = []
        try:
            for p in root.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    items.append(Job(**data))
                except Exception:
                    continue
        except Exception:
            items = []

        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[:lim]

    def _persist(self, j: Job) -> None:
        p = _jobs_root() / f"{j.id}.json"
        try:
            p.write_text(json.dumps(j.__dict__, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception:
            return


class Runner:
    def __init__(self, max_concurrency: int) -> None:
        self._sem = threading.Semaphore(max(1, int(max_concurrency)))

    def submit(self, fn) -> None:
        t = threading.Thread(target=self._wrap_with_slot, args=(fn,), daemon=True)
        t.start()

    def _wrap_with_slot(self, fn) -> None:
        with self._sem:
            fn()


store = JobStore()
runner = Runner(max_concurrency=int(os.getenv("SMART_TRANS_PIPELINE_MAX_CONCURRENCY", "1")))


def _run_analyzer(job_id: str, *, exif: dict[str, Any] | None) -> None:
    j = store.get(job_id)
    if j is None:
        return

    store.update(job_id, status="running", started_at=_now_iso())
    j = store.get(job_id)
    if j is None:
        return

    repo = _repo_root()
    script = (repo / "traffic_issue_analyzer.py").resolve()
    if not script.is_file():
        store.update(job_id, status="failed", finished_at=_now_iso(), error=f"missing analyzer script: {script}")
        return

    task = (j.task or "rag").strip().lower()
    if task not in {"rag", "accident"}:
        task = "rag"

    extract_runs = int(j.extract_runs or 3)
    if extract_runs < 1:
        extract_runs = 1
    if extract_runs > 7:
        extract_runs = 7

    cmd: list[str] = [
        sys.executable,
        str(script),
        "-i",
        j.saved_file,
        "--task",
        task,
    ]

    if task == "rag":
        cmd += ["--extract-runs", str(extract_runs)]

    if j.hint:
        cmd += ["--hint", j.hint]

    # Keep it deterministic: rely on local rules, and avoid noisy verbose output.
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as e:
        store.update(job_id, status="failed", finished_at=_now_iso(), error=str(e), command=cmd)
        return

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    artifacts = _job_artifacts_root(job_id)
    try:
        (artifacts / "analyzer.stdout.txt").write_text(stdout[-200000:], encoding="utf-8")
        (artifacts / "analyzer.stderr.txt").write_text(stderr[-200000:], encoding="utf-8")
    except Exception:
        pass

    result_obj = _parse_result_from_stdout(stdout)

    if proc.returncode != 0:
        store.update(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            returncode=int(proc.returncode),
            command=cmd,
            stdout=stdout[-20000:],
            stderr=stderr[-20000:],
            result=result_obj,
            error=f"analyzer exit code {proc.returncode}",
        )
        return

    if not isinstance(result_obj, dict):
        store.update(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            returncode=int(proc.returncode),
            command=cmd,
            stdout=stdout[-20000:],
            stderr=stderr[-20000:],
            result=None,
            error="failed to parse analyzer JSON output",
        )
        return

    # Prefer our own stamped coords if analyzer didn't produce coords.
    try:
        if result_obj.get("lat") is None and result_obj.get("lng") is None:
            if j.stamp_lat is not None and j.stamp_lng is not None:
                result_obj["lat"] = j.stamp_lat
                result_obj["lng"] = j.stamp_lng
                result_obj["location_source"] = "watermark"
                result_obj["location_confidence"] = 1.0
    except Exception:
        pass

    # Merge EXIF location if analyzer didn't provide coords.
    if exif and isinstance(exif, dict):
        if result_obj.get("lat") is None and result_obj.get("lng") is None:
            if "lat" in exif and "lng" in exif:
                result_obj["lat"] = exif.get("lat")
                result_obj["lng"] = exif.get("lng")
                result_obj["location_source"] = exif.get("location_source") or "exif"
                result_obj["location_confidence"] = exif.get("location_confidence")

    # Auto-store into DB.
    accident_id: int | None = None
    err: str | None = None
    beep_attempted = False
    beep_ok = False
    beep_error: str | None = None
    try:
        has_accident = bool(result_obj.get("has_accident"))
        accident_type = str(result_obj.get("accident_type") or "其他").strip() or "其他"
        severity = str(result_obj.get("severity") or "").strip()
        if severity not in {"轻微", "中等", "严重"}:
            severity = "中等" if has_accident else "轻微"

        desc = str(result_obj.get("description") or "").strip()
        conf = clamp01(float(result_obj.get("confidence") or 0.0))

        lat = _to_float_or_none(result_obj.get("lat"))
        lng = _to_float_or_none(result_obj.get("lng"))
        if lat is not None and (lat < -90.0 or lat > 90.0):
            lat = None
        if lng is not None and (lng < -180.0 or lng > 180.0):
            lng = None

        loc_conf = _to_float_or_none(result_obj.get("location_confidence"))
        if loc_conf is not None:
            loc_conf = clamp01(loc_conf)

        raw_model_output = result_obj.get("raw_model_output")
        if raw_model_output is not None and not isinstance(raw_model_output, str):
            raw_model_output = json.dumps(raw_model_output, ensure_ascii=False)
        if isinstance(raw_model_output, str) and len(raw_model_output) > 20000:
            raw_model_output = raw_model_output[:20000]

        a = Accident(
            created_at=now_bjt_naive(),
            source="http_ingest",
            image_path=j.image_path,
            hint=j.hint,
            has_accident=has_accident,
            accident_type=accident_type,
            severity=severity,
            description=desc,
            confidence=conf,
            location_text=(str(result_obj.get("location_text")).strip() if isinstance(result_obj.get("location_text"), str) else None),
            lat=lat,
            lng=lng,
            location_source=(
                str(result_obj.get("location_source")).strip() if isinstance(result_obj.get("location_source"), str) else None
            ),
            location_confidence=loc_conf,
            raw_model_output=raw_model_output,
        )

        db = SessionLocal()
        try:
            db.add(a)
            db.commit()
            db.refresh(a)
            accident_id = int(a.id)
        finally:
            db.close()

        # Optional: beep after successful store.
        disable_beep = _env_flag("SMART_TRANS_DISABLE_BEEP")
        enable_beep = _env_flag("SMART_TRANS_ENABLE_BEEP")
        if enable_beep and not disable_beep and has_accident:
            beep_attempted = True
            url = os.getenv("SMART_TRANS_BEEP_MCP_URL", "http://localhost:9010/sse").strip() or "http://localhost:9010/sse"
            on_time = float(os.getenv("SMART_TRANS_BEEP_ON_TIME", "0.3"))
            gap = float(os.getenv("SMART_TRANS_BEEP_GAP", "0.3"))
            beeps = _severity_to_beeps(severity)
            try:
                asyncio.run(_beep_n_via_mcp(n=beeps, url=url, on_time=on_time, gap=gap))
                beep_ok = True
            except Exception as e_beep:
                beep_ok = False
                beep_error = str(e_beep)
    except Exception as e:
        err = f"db insert failed: {e}"

    store.update(
        job_id,
        status="done" if err is None else "failed",
        finished_at=_now_iso(),
        returncode=int(proc.returncode),
        command=cmd,
        stdout=stdout[-20000:],
        stderr=stderr[-20000:],
        result=result_obj,
        accident_id=accident_id,
        error=err,
        beep_attempted=beep_attempted,
        beep_ok=beep_ok,
        beep_error=beep_error,
    )


@router.post("/ingest")
async def ingest_image(
    file: UploadFile = File(...),
    hint: str | None = Form(default=None),
    task: str = Form(default="rag"),
    extract_runs: int = Form(default=3),
) -> dict[str, Any]:
    suffix = _safe_image_suffix(file.filename)
    name = f"{uuid.uuid4().hex}{suffix}"

    base = uploads_dir()
    dst = (base / name).resolve()
    data = await file.read()
    dst.write_bytes(data)

    image_path = f"uploads/{name}"
    image_url = image_url_for_path(image_path) or f"/uploads/{name}"

    # Extract EXIF before we stamp (stamping may drop EXIF metadata).
    exif = try_extract_exif_gps(dst)

    stamp_ok = False
    stamp_lat = None
    stamp_lng = None
    stamp_text = None
    stamp_error = None
    try:
        stamp = stamp_inplace_hz(dst)
        if isinstance(stamp, dict) and bool(stamp.get("ok")):
            stamp_ok = True
            stamp_lat = stamp.get("lat")
            stamp_lng = stamp.get("lng")
            stamp_text = stamp.get("text")
    except Exception as e:
        stamp_ok = False
        stamp_error = str(e)

    job_id = uuid.uuid4().hex
    j = Job(
        id=job_id,
        created_at=_now_iso(),
        status="queued",
        image_path=image_path,
        saved_file=str(dst),
        hint=(hint.strip() if isinstance(hint, str) and hint.strip() else None),
        task=(task or "rag").strip().lower() or "rag",
        extract_runs=int(extract_runs),
        stamp_ok=stamp_ok,
        stamp_lat=stamp_lat,
        stamp_lng=stamp_lng,
        stamp_text=stamp_text,
        stamp_error=stamp_error,
    )
    store.put(j)

    runner.submit(lambda: _run_analyzer(job_id, exif=exif))

    return {
        "job_id": job_id,
        "status": j.status,
        "created_at": j.created_at,
        "image_path": image_path,
        "image_url": image_url,
    }

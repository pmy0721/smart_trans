#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp import FastMCP


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _sanitize_filename(name: str) -> str:
    s = (name or "").strip().replace("\\", "/")
    s = s.split("/")[-1]
    s = _SAFE_NAME_RE.sub("_", s)
    s = s.strip("._-")
    return s or "image.jpg"


def _guess_ext(name: str) -> str:
    n = (name or "").lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
        if n.endswith(ext):
            return ext
    return ".jpg"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


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


def _new_batch_dir() -> Path:
    d = _incoming_root() / time.strftime("%Y%m%d_%H%M%S", time.localtime())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_default_pipeline_cli() -> list[str]:
    raw = os.getenv("SMART_TRANS_PIPELINE_DEFAULT_CLI", "")
    if not raw.strip():
        return []
    try:
        obj = json.loads(raw)
        if isinstance(obj, list) and all(isinstance(x, str) for x in obj):
            return list(obj)
    except Exception:
        pass
    # Fallback: split by whitespace (best-effort).
    return raw.strip().split()


@dataclass
class Job:
    id: str
    filename: str
    saved_path: str
    created_at: str
    status: str
    hint: str | None = None
    pipeline_cli: list[str] | None = None
    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None
    command: list[str] | None = None
    stdout: str | None = None
    stderr: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def put(self, j: Job) -> None:
        with self._lock:
            self._jobs[j.id] = j
        self._persist(j)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            j = self._jobs.get(job_id)
        if j is not None:
            return j
        p = _jobs_root() / f"{job_id}.json"
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return Job(**data)
        except Exception:
            return None

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            items = list(self._jobs.values())
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[: max(1, int(limit))]

    def update(self, job_id: str, **kwargs: Any) -> Job | None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                return None
            for k, v in kwargs.items():
                if hasattr(j, k):
                    setattr(j, k, v)
        self._persist(j)
        return j

    def _persist(self, j: Job) -> None:
        p = _jobs_root() / f"{j.id}.json"
        try:
            p.write_text(json.dumps(j.__dict__, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception:
            # Best-effort; keep in-memory state.
            return


class PipelineRunner:
    def __init__(self, max_concurrency: int) -> None:
        self._sem = threading.Semaphore(max(1, int(max_concurrency)))
        self._threads: list[threading.Thread] = []

    def submit(self, fn) -> None:
        t = threading.Thread(target=fn, daemon=True)
        self._threads.append(t)
        t.start()

    def run_with_slot(self, fn) -> None:
        with self._sem:
            fn()


store = JobStore()
runner = PipelineRunner(max_concurrency=int(os.getenv("SMART_TRANS_PIPELINE_MAX_CONCURRENCY", "1")))
mcp = FastMCP("SmartTrans Image Receiver")


def _run_pipeline_for_job(job_id: str) -> None:
    j = store.get(job_id)
    if j is None:
        return

    def _inner() -> None:
        store.update(job_id, status="running", started_at=_now_iso())
        j2 = store.get(job_id)
        if j2 is None:
            return

        pipeline_cli = list(j2.pipeline_cli or [])
        cmd = [
            "python3",
            str((_repo_root() / "pipeline_yolo_rag.py").resolve()),
            "-i",
            j2.saved_path,
        ] + pipeline_cli

        job_dir = _incoming_root() / "job_artifacts" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        out_file = job_dir / "pipeline.stdout.txt"
        err_file = job_dir / "pipeline.stderr.txt"

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_repo_root()),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            out_file.write_text(stdout[-200000:], encoding="utf-8")
            err_file.write_text(stderr[-200000:], encoding="utf-8")

            result_obj: dict[str, Any] | None = None
            try:
                # pipeline prints a JSON object to stdout; parse the last JSON-looking line first.
                lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
                for ln in reversed(lines[-20:]):
                    if ln.startswith("{") and ln.endswith("}"):
                        o = json.loads(ln)
                        if isinstance(o, dict):
                            result_obj = o
                            break
                if result_obj is None and stdout.strip().startswith("{"):
                    o = json.loads(stdout.strip())
                    if isinstance(o, dict):
                        result_obj = o
            except Exception:
                result_obj = None

            store.update(
                job_id,
                status="done" if proc.returncode == 0 else "failed",
                finished_at=_now_iso(),
                returncode=int(proc.returncode),
                command=cmd,
                stdout=stdout[-20000:],
                stderr=stderr[-20000:],
                result=result_obj,
                error=None if proc.returncode == 0 else f"pipeline exit code {proc.returncode}",
            )
        except Exception as e:
            store.update(
                job_id,
                status="failed",
                finished_at=_now_iso(),
                returncode=-1,
                command=cmd,
                stdout=None,
                stderr=None,
                result=None,
                error=str(e),
            )

    runner.run_with_slot(_inner)


@mcp.tool()
def upload_image(
    filename: str,
    content_b64: str,
    hint: str | None = None,
    run_pipeline: bool = True,
    pipeline_cli: list[str] | None = None,
) -> dict[str, Any]:
    """Receive one image and optionally trigger pipeline.

    Args:
      filename: Original file name.
      content_b64: Base64 encoded bytes of the image.
      hint: Optional hint text forwarded to pipeline (--hint).
      run_pipeline: Whether to trigger pipeline_yolo_rag.py immediately.
      pipeline_cli: Extra CLI args appended to pipeline invocation.
    """

    safe = _sanitize_filename(filename)
    ext = _guess_ext(safe)
    stem = Path(safe).stem or "image"
    uniq = uuid.uuid4().hex[:10]
    out_name = f"{stem}_{uniq}{ext}"

    batch_dir = _new_batch_dir()
    saved = (batch_dir / out_name).resolve()

    try:
        data = base64.b64decode(content_b64, validate=False)
    except Exception as e:
        raise ValueError(f"invalid base64: {e}")
    saved.write_bytes(data)

    job_id = uuid.uuid4().hex

    default_cli = _parse_default_pipeline_cli()
    cli = list(default_cli)

    if hint and "--hint" not in cli:
        cli += ["--hint", hint]

    if pipeline_cli:
        cli += [str(x) for x in pipeline_cli if str(x).strip()]

    j = Job(
        id=job_id,
        filename=safe,
        saved_path=str(saved),
        created_at=_now_iso(),
        status="queued" if run_pipeline else "received",
        hint=hint,
        pipeline_cli=cli,
    )
    store.put(j)

    if run_pipeline:
        runner.submit(lambda: _run_pipeline_for_job(job_id))

    return {
        "job_id": job_id,
        "status": j.status,
        "saved_path": j.saved_path,
        "batch_folder": batch_dir.name,
        "created_at": j.created_at,
    }


@mcp.tool()
def get_job(job_id: str) -> dict[str, Any]:
    """Get job status and (optional) result."""

    j = store.get((job_id or "").strip())
    if j is None:
        return {"ok": False, "error": "job not found"}
    return {"ok": True, "job": j.__dict__}


@mcp.tool()
def list_jobs(limit: int = 50) -> dict[str, Any]:
    """List recent jobs."""

    items = [j.__dict__ for j in store.list(limit=limit)]
    return {"ok": True, "items": items}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="MCP image receiver (SSE) that triggers pipeline_yolo_rag.py.")
    ap.add_argument(
        "--host",
        default=os.getenv("SMART_TRANS_IMAGE_MCP_HOST", "0.0.0.0"),
        help="Bind host/IP for SSE server (default: 0.0.0.0 or env SMART_TRANS_IMAGE_MCP_HOST).",
    )
    ap.add_argument("--port", type=int, default=int(os.getenv("SMART_TRANS_IMAGE_MCP_PORT", "9010")))
    args = ap.parse_args(argv)

    host = str(args.host).strip() or "0.0.0.0"
    port = int(args.port)
    adv_host = host
    if host == "0.0.0.0":
        # 0.0.0.0 means bind-all; show a friendlier URL hint.
        adv_host = os.getenv("SMART_TRANS_IMAGE_MCP_ADVERTISE_HOST", host)
    print(f"Starting MCP Image Server on http://{adv_host}:{port}/sse (bind {host}:{port})", flush=True)
    _incoming_root()
    _jobs_root()
    mcp.run(transport="sse", host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

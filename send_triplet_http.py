#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests


def _validate_image(path_raw: str) -> Path:
    p = Path(path_raw).expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f"error: file not found: {p}")
    if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
        raise SystemExit(f"error: not an image: {p}")
    return p


def _submit_triplet(
    *,
    ingest_url: str,
    frame_t0: Path,
    frame_t1: Path,
    frame_t3: Path,
    hint: str | None,
    task: str,
    extract_runs: int,
    timeout_s: float,
) -> dict[str, Any]:
    data: dict[str, str] = {
        "task": task,
        "extract_runs": str(int(extract_runs)),
    }
    if hint:
        data["hint"] = hint

    with (
        frame_t0.open("rb") as f0,
        frame_t1.open("rb") as f1,
        frame_t3.open("rb") as f3,
    ):
        files = {
            "frame_t0": (frame_t0.name, f0, "application/octet-stream"),
            "frame_t1": (frame_t1.name, f1, "application/octet-stream"),
            "frame_t3": (frame_t3.name, f3, "application/octet-stream"),
        }
        r = requests.post(ingest_url, data=data, files=files, timeout=timeout_s)
    r.raise_for_status()
    obj = r.json()
    if not isinstance(obj, dict):
        raise RuntimeError("ingest_triplet response is not a JSON object")
    return obj


def _jobs_base_url(ingest_url: str) -> str:
    x = ingest_url.rstrip("/")
    if x.endswith("/api/ingest_triplet"):
        return x[: -len("/api/ingest_triplet")] + "/api/jobs"
    return x + "/jobs"


def _poll_job(
    *, jobs_base: str, job_id: str, interval_s: float, timeout_s: float
) -> dict[str, Any]:
    def _extract_status(obj: Any) -> str | None:
        if not isinstance(obj, dict):
            return None
        s = obj.get("status")
        if isinstance(s, str) and s:
            return s
        j = obj.get("job")
        if isinstance(j, dict):
            js = j.get("status")
            if isinstance(js, str) and js:
                return js
        return None

    start = time.time()
    url = f"{jobs_base.rstrip('/')}/{job_id}"
    while True:
        r = requests.get(url, timeout=max(5.0, interval_s + 5.0))
        r.raise_for_status()
        obj = r.json()
        status = _extract_status(obj)
        if status in {"done", "failed"}:
            return obj
        if isinstance(obj, dict) and obj.get("ok") is False:
            raise RuntimeError(str(obj.get("error") or "job query failed"))
        if time.time() - start > timeout_s:
            raise TimeoutError(f"timeout waiting job {job_id}")
        time.sleep(max(0.2, float(interval_s)))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Upload triplet via HTTP /api/ingest_triplet and optionally wait."
    )
    ap.add_argument("--frame-t0", required=True)
    ap.add_argument("--frame-t1", required=True)
    ap.add_argument("--frame-t3", required=True)
    ap.add_argument("--ingest-url", default="http://127.0.0.1:28000/api/ingest_triplet")
    ap.add_argument("--hint", default=None)
    ap.add_argument("--task", choices=["rag", "accident"], default="rag")
    ap.add_argument("--extract-runs", type=int, default=3)
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--poll-interval", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args(argv)

    t0 = _validate_image(args.frame_t0)
    t1 = _validate_image(args.frame_t1)
    t3 = _validate_image(args.frame_t3)

    runs = int(args.extract_runs)
    if runs < 1:
        runs = 1
    if runs > 7:
        runs = 7

    try:
        queued = _submit_triplet(
            ingest_url=str(args.ingest_url),
            frame_t0=t0,
            frame_t1=t1,
            frame_t3=t3,
            hint=(
                str(args.hint).strip()
                if isinstance(args.hint, str) and args.hint.strip()
                else None
            ),
            task=str(args.task),
            extract_runs=runs,
            timeout_s=max(10.0, float(args.timeout)),
        )
    except Exception as e:
        print(f"error: submit failed: {e}", file=sys.stderr)
        return 2

    if not args.wait:
        print(json.dumps(queued, ensure_ascii=False))
        return 0

    job_id = queued.get("job_id") if isinstance(queued, dict) else None
    if not isinstance(job_id, str) or not job_id:
        print("error: response missing job_id", file=sys.stderr)
        print(json.dumps(queued, ensure_ascii=False))
        return 2

    try:
        final_obj = _poll_job(
            jobs_base=_jobs_base_url(str(args.ingest_url)),
            job_id=job_id,
            interval_s=float(args.poll_interval),
            timeout_s=float(args.timeout),
        )
    except Exception as e:
        print(f"error: poll failed: {e}", file=sys.stderr)
        return 2

    print(json.dumps(final_obj, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession  # type: ignore
from mcp.client.sse import sse_client  # type: ignore


def _is_image(p: Path) -> bool:
    return p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def _iter_inputs(paths: list[str], dir_path: str | None) -> list[Path]:
    out: list[Path] = []
    if dir_path:
        d = Path(dir_path).expanduser()
        if not d.is_dir():
            raise SystemExit(f"error: not a directory: {d}")
        for p in sorted(d.iterdir()):
            if p.is_file() and _is_image(p):
                out.append(p)
        return out

    if not paths:
        raise SystemExit("error: no input images")
    for s in paths:
        p = Path(s).expanduser()
        if not p.is_file():
            raise SystemExit(f"error: file not found: {p}")
        if not _is_image(p):
            raise SystemExit(f"error: not an image: {p}")
        out.append(p)
    return out


async def _call_upload(
    session: ClientSession,
    *,
    image_path: Path,
    hint: str | None,
    pipeline_cli: list[str],
) -> dict[str, Any]:
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    resp = await session.call_tool(
        "upload_image",
        arguments={
            "filename": image_path.name,
            "content_b64": data,
            "hint": hint,
            "run_pipeline": True,
            "pipeline_cli": pipeline_cli,
        },
    )

    # fastmcp typically returns tool output as text; mcp python client wraps it in a content list.
    if not resp.content:
        return {"ok": False, "error": "empty response"}
    text = getattr(resp.content[0], "text", "")
    if not text:
        return {"ok": False, "error": "empty response text"}
    try:
        import json

        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {"ok": True, "raw": text}


async def _poll_job(session: ClientSession, job_id: str, interval: float, timeout_s: float) -> dict[str, Any]:
    import json

    start = time.time()
    while True:
        resp = await session.call_tool("get_job", arguments={"job_id": job_id})
        text = getattr(resp.content[0], "text", "") if resp.content else ""
        try:
            obj = json.loads(text) if text else {}
        except Exception:
            obj = {"ok": True, "raw": text}

        job = obj.get("job") if isinstance(obj, dict) else None
        status = job.get("status") if isinstance(job, dict) else None
        if status in {"done", "failed"}:
            return obj

        if time.time() - start > timeout_s:
            return {"ok": False, "error": f"timeout waiting job {job_id}"}

        await asyncio.sleep(max(0.1, float(interval)))


async def main_async(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Send images to MCP receiver (SSE) and trigger pipeline.")
    ap.add_argument("images", nargs="*", help="Image paths.")
    ap.add_argument("--dir", default=None, help="Directory containing images.")
    ap.add_argument("--server", default=os.getenv("SMART_TRANS_IMAGE_MCP_URL", "http://localhost:9010/sse"))
    ap.add_argument("--hint", default=None, help="Optional hint forwarded to pipeline (--hint).")
    ap.add_argument(
        "--pipeline-cli",
        action="append",
        default=[],
        help="Extra pipeline args, repeatable. Example: --pipeline-cli=--task --pipeline-cli=rag",
    )
    ap.add_argument("--wait", action="store_true", help="Poll until pipeline job is finished.")
    ap.add_argument("--poll-interval", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args(argv)

    inputs = _iter_inputs(args.images, args.dir)

    async with sse_client(str(args.server)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            for p in inputs:
                r = await _call_upload(session, image_path=p, hint=args.hint, pipeline_cli=list(args.pipeline_cli))
                job_id = r.get("job_id") if isinstance(r, dict) else None
                if job_id:
                    print(f"queued: {p} -> job_id={job_id}")
                else:
                    print(f"queued: {p} -> {r}")

                if args.wait and job_id:
                    out = await _poll_job(session, str(job_id), interval=float(args.poll_interval), timeout_s=float(args.timeout))
                    job = out.get("job") if isinstance(out, dict) else None
                    status = job.get("status") if isinstance(job, dict) else None
                    print(f"done: job_id={job_id} status={status}")
                    if isinstance(job, dict) and isinstance(job.get("result"), dict):
                        import json

                        print(json.dumps(job["result"], ensure_ascii=False))
                    elif isinstance(job, dict) and job.get("error"):
                        print(f"error: {job.get('error')}")

    return 0


def main(argv: list[str]) -> int:
    try:
        return asyncio.run(main_async(argv))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

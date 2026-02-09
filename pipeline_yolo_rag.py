#!/usr/bin/env python3

import argparse
import json
import os
import sys
import subprocess
import time
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _load_dotenv_if_present() -> None:
    # Reuse logic from traffic_issue_analyzer if available, else best-effort.
    try:
        import traffic_issue_analyzer as tia  # type: ignore

        if hasattr(tia, "_load_dotenv_if_present"):
            tia._load_dotenv_if_present()  # type: ignore[attr-defined]
            return
    except Exception:
        pass

    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        return


def _ensure_local_ultralytics_on_path() -> Path:
    # Prefer the vendored Ultralytics implementation at ./yolov11/ultralytics
    root = _repo_root()
    yolo_dir = root / "yolov11"
    if not (yolo_dir / "ultralytics").is_dir():
        raise SystemExit(f"error: yolov11 ultralytics not found at {yolo_dir / 'ultralytics'}")
    sys.path.insert(0, str(yolo_dir))
    return yolo_dir


def _run_yolo_annotate(
    image_path: Path,
    weights: Path,
    out_dir: Path,
    conf: float,
    iou: float,
    imgsz: int,
    device: str,
) -> tuple[Path, dict[str, Any]]:
    _ensure_local_ultralytics_on_path()

    try:
        import cv2  # type: ignore
    except Exception as e:
        raise SystemExit(f"error: missing dependency cv2 (opencv-python): {e}")

    try:
        from ultralytics import YOLO  # type: ignore
    except Exception as e:
        raise SystemExit(f"error: failed to import local ultralytics from yolov11/: {e}")

    if not device:
        try:
            import torch  # type: ignore

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    out_dir.mkdir(parents=True, exist_ok=True)
    annotated_path = out_dir / f"{image_path.stem}.jpg"

    model = YOLO(str(weights))
    results = model.predict(source=str(image_path), conf=conf, iou=iou, imgsz=imgsz, device=device, verbose=False)
    if not results:
        # Still produce a copy of the input for downstream pipeline.
        annotated_path.write_bytes(image_path.read_bytes())
        return annotated_path, {"weights": str(weights), "boxes": [], "names": {}, "image": str(image_path)}

    r0 = results[0]
    annotated = r0.plot()
    ok = cv2.imwrite(str(annotated_path), annotated)
    if not ok:
        raise SystemExit(f"error: failed to write annotated image: {annotated_path}")

    names = getattr(r0, "names", None) or {}
    dets: list[dict[str, Any]] = []
    boxes = getattr(r0, "boxes", None)
    if boxes is not None and len(boxes) > 0:
        try:
            xyxy = boxes.xyxy.cpu().numpy().tolist()  # type: ignore[attr-defined]
            confs = boxes.conf.cpu().numpy().tolist()  # type: ignore[attr-defined]
            clss = boxes.cls.cpu().numpy().tolist()  # type: ignore[attr-defined]
        except Exception:
            xyxy, confs, clss = [], [], []

        for i in range(min(len(xyxy), len(confs), len(clss))):
            cid = int(clss[i])
            dets.append(
                {
                    "cls_id": cid,
                    "cls_name": names.get(cid) if isinstance(names, dict) else None,
                    "conf": float(confs[i]),
                    "xyxy": [float(x) for x in xyxy[i]],
                }
            )

    yolo_meta: dict[str, Any] = {
        "weights": str(weights),
        "conf": conf,
        "iou": iou,
        "imgsz": imgsz,
        "device": device,
        "image": str(image_path),
        "annotated": str(annotated_path),
        "detections": dets,
    }
    return annotated_path, yolo_meta


def _merge_trace(raw_model_output: str | None, yolo_meta: dict[str, Any]) -> str:
    base: dict[str, Any] = {}
    if raw_model_output:
        try:
            obj = json.loads(raw_model_output)
            if isinstance(obj, dict):
                base = obj
        except Exception:
            base = {"raw": raw_model_output}

    base["yolo"] = yolo_meta
    s = json.dumps(base, ensure_ascii=False)
    # Keep similar bounds as traffic_issue_analyzer.py (pydantic max_length=20000, internal truncation ~18000).
    if len(s) > 18000:
        # Best-effort shrink: drop per-box details first.
        if isinstance(base.get("yolo"), dict):
            y = dict(base["yolo"])
            if "detections" in y:
                y["detections"] = ["(truncated)"]
            base["yolo"] = y
        s = json.dumps(base, ensure_ascii=False)
        s = s[:18000]
    return s


def _severity_to_beeps(severity: str | None) -> int:
    s = (severity or "").strip()
    if s == "轻微":
        return 1
    if s == "中等":
        return 2
    if s == "严重":
        return 3
    return 1


def _try_beep(
    *,
    beeps: int,
    mcp_url: str,
    on_time: float,
    gap: float,
    start_server: bool,
    verbose: bool,
) -> None:
    if beeps <= 0:
        return

    def _run_once() -> None:
        import asyncio

        from llm_mcp_client import beep_n  # type: ignore

        asyncio.run(beep_n(beeps, url=mcp_url, on_time=on_time, gap=gap))

    try:
        if verbose:
            print(f"beep: severity beeps={beeps} url={mcp_url}", file=sys.stderr)
        _run_once()
        return
    except Exception as e1:
        if not start_server:
            print(f"warn: beep failed: {e1}", file=sys.stderr)
            return

    # Best-effort: auto start beep_mcp_server.py then retry.
    root = _repo_root()
    server_script = root / "beep_mcp_server.py"
    if not server_script.is_file():
        print(f"warn: beep server script not found: {server_script}", file=sys.stderr)
        return

    proc = None
    try:
        if verbose:
            print("beep: starting local beep_mcp_server.py", file=sys.stderr)
        proc = subprocess.Popen(
            [sys.executable, str(server_script)],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        _run_once()
    except Exception as e2:
        print(f"warn: beep failed after starting server: {e2}", file=sys.stderr)
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def main(argv: list[str]) -> int:
    _load_dotenv_if_present()

    ap = argparse.ArgumentParser(
        description=(
            "End-to-end pipeline: YOLOv11 annotate -> traffic_issue_analyzer RAG -> upload -> store. "
            "Stores the annotated image for frontend display."
        )
    )
    ap.add_argument("--image", "-i", required=True, help="Input image path (jpg/png/webp...).")

    ap.add_argument(
        "--skip-yolo",
        "--no-yolo",
        action="store_true",
        help="Skip YOLO annotation and analyze the original image directly.",
    )
    ap.add_argument("--yolo-weights", default="yolov11/best.pt", help="YOLO weights path (default: yolov11/best.pt).")
    ap.add_argument("--yolo-conf", type=float, default=0.25, help="YOLO confidence threshold.")
    ap.add_argument("--yolo-iou", type=float, default=0.45, help="YOLO IoU threshold.")
    ap.add_argument("--yolo-imgsz", type=int, default=640, help="YOLO inference image size.")
    ap.add_argument("--yolo-device", default="", help="YOLO device (e.g. cpu, cuda, cuda:0). Empty=auto.")
    ap.add_argument("--output-dir", default="output", help="Directory to write annotated image (default: ./output).")

    ap.add_argument("--task", choices=["rag", "accident"], default="rag", help="Analysis mode (default: rag).")
    ap.add_argument("--model", default=os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen3-VL-32B-Instruct"))
    ap.add_argument("--base-url", default=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"))
    ap.add_argument("--api-key", default=os.getenv("SILICONFLOW_API_KEY", ""))
    ap.add_argument("--hint", default=None, help="Optional hint text passed to the analyzer.")
    ap.add_argument("--extract-runs", type=int, default=3, help="RAG mode only: number of fact-extraction runs (1-7).")
    ap.add_argument("--no-cache", action="store_true", help="RAG mode only: disable on-disk result cache.")
    ap.add_argument("--refresh-cache", action="store_true", help="RAG mode only: ignore cache and recompute.")
    ap.add_argument("--upload", default=None, help="Upload URL (e.g. http://localhost:28000/api/uploads).")
    ap.add_argument("--post", default=None, help="Post URL (e.g. http://localhost:28000/api/accidents).")
    ap.add_argument("--source", default="script+yolo", help="Source label when posting (default: script+yolo).")

    ap.add_argument("--beep", action="store_true", help="Beep based on severity (requires local MCP server).")
    ap.add_argument(
        "--no-beep",
        action="store_true",
        help="Disable beep even if --beep is set (or if upstream passed it).",
    )
    ap.add_argument(
        "--beep-mcp-url",
        default=os.getenv("SMART_TRANS_BEEP_MCP_URL", "http://localhost:9010/sse"),
        help="MCP SSE URL for beeper.",
    )
    ap.add_argument("--beep-on-time", type=float, default=0.3, help="Single beep duration seconds.")
    ap.add_argument("--beep-gap", type=float, default=0.3, help="Gap between beeps seconds.")
    ap.add_argument(
        "--beep-start-server",
        action="store_true",
        help="If beep fails, auto-start beep_mcp_server.py and retry (best-effort).",
    )

    ap.add_argument("--verbose", action="store_true", help="Verbose output.")
    args = ap.parse_args(argv)

    root = _repo_root()
    image_path = Path(os.path.expanduser(args.image))
    if not image_path.is_file():
        print(f"error: image not found: {image_path}", file=sys.stderr)
        return 2

    annotated_path: Path
    yolo_meta: dict[str, Any]
    if bool(args.skip_yolo):
        annotated_path = image_path
        yolo_meta = {
            "skipped": True,
            "reason": "--skip-yolo",
            "image": str(image_path),
            "annotated": str(image_path),
        }
    else:
        weights = Path(args.yolo_weights)
        if not weights.is_absolute():
            weights = (root / weights).resolve()
        if not weights.is_file():
            print(f"error: yolo weights not found: {weights}", file=sys.stderr)
            return 2

        out_dir = Path(args.output_dir)
        if not out_dir.is_absolute():
            out_dir = (root / out_dir).resolve()

        device = str(args.yolo_device or "").strip()

        annotated_path, yolo_meta = _run_yolo_annotate(
            image_path=image_path,
            weights=weights,
            out_dir=out_dir,
            conf=float(args.yolo_conf),
            iou=float(args.yolo_iou),
            imgsz=int(args.yolo_imgsz),
            device=device,
        )

    if not args.api_key:
        print("error: missing SILICONFLOW_API_KEY (set it in .env or env var)", file=sys.stderr)
        return 2

    import traffic_issue_analyzer as tia  # type: ignore

    if args.task == "rag":
        result, _cache_meta = tia.analyze_accident_rag(
            image_path=str(annotated_path),
            model=str(args.model),
            base_url=str(args.base_url),
            api_key=str(args.api_key),
            hint=args.hint,
            verbose=bool(args.verbose),
            extract_runs=int(args.extract_runs),
            use_cache=not bool(args.no_cache),
            refresh_cache=bool(args.refresh_cache),
        )
    else:
        result = tia.analyze_accident(
            image_path=str(annotated_path),
            model=str(args.model),
            base_url=str(args.base_url),
            api_key=str(args.api_key),
            hint=args.hint,
            verbose=bool(args.verbose),
        )

    # Attach YOLO metadata into trace for later inspection.
    result = dict(result)
    result["raw_model_output"] = _merge_trace(result.get("raw_model_output"), yolo_meta)

    upload_info = None
    if args.upload:
        try:
            upload_info = tia._try_upload_image(str(args.upload), str(annotated_path))
        except Exception as e:
            print(f"warn: upload failed: {e}", file=sys.stderr)

    post_payload: dict[str, Any] = dict(result)
    post_payload["source"] = str(args.source)
    if args.hint:
        post_payload["hint"] = str(args.hint)

    if upload_info and isinstance(upload_info.get("image_path"), str):
        post_payload["image_path"] = upload_info["image_path"]
        exif = upload_info.get("exif")
        if isinstance(exif, dict) and "lat" in exif and "lng" in exif:
            post_payload["lat"] = exif.get("lat")
            post_payload["lng"] = exif.get("lng")
            post_payload["location_source"] = exif.get("location_source") or "exif"
            post_payload["location_confidence"] = exif.get("location_confidence")

    if args.post:
        post_resp = None
        try:
            post_resp = tia._try_post_accident(str(args.post), post_payload)
        except Exception as e:
            print(f"warn: post failed: {e}", file=sys.stderr)

        # Optional: beep after successful store (only if has_accident=true).
        # We prefer backend-returned fields as the source of truth.
        disable_beep = bool(args.no_beep) or str(os.getenv("SMART_TRANS_DISABLE_BEEP", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }

        if bool(args.beep) and not disable_beep and isinstance(post_resp, dict) and bool(post_resp.get("has_accident")):
            beeps = _severity_to_beeps(str(post_resp.get("severity") or ""))
            _try_beep(
                beeps=beeps,
                mcp_url=str(args.beep_mcp_url),
                on_time=float(args.beep_on_time),
                gap=float(args.beep_gap),
                start_server=bool(args.beep_start_server),
                verbose=bool(args.verbose),
            )

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

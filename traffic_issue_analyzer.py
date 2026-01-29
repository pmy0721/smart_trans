#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import base64
import json
import mimetypes
import os
import sys
from typing import Any


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        return


def _guess_mime_type(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("image/"):
        return mime
    # Default fallback that works well for most accident photos
    return "image/jpeg"


def _image_to_data_url(path: str) -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{_guess_mime_type(path)};base64,{b64}"


def _build_prompt(label_hint: str | None = None) -> str:
    labels = [
        "闯红灯",
        "车辆碰撞",
        "车辆追尾",
        "侧面碰撞",
        "对向相撞",
        "非机动车事故",
        "行人事故",
        "违法停车",
        "占用应急车道",
        "逆行",
        "超速",
        "压实线/变道不当",
        "路口未礼让",
        "道路障碍/抛洒物",
        "未知",
    ]

    hint = ""
    if label_hint:
        hint = f"\n补充线索（可能不准确，仅供参考）：{label_hint}\n"

    return (
        "你是交通事故图片语义分析助手。请仅根据图片内容判断‘主要交通问题类型’。"
        "\n\n要求：\n"
        "1) 只输出一个类型标签（只输出标签本身，不要解释、不要加标点、不要编号、不要换行）。\n"
        "2) 必须从下列标签中选择最贴切的一个；无法判断就输出：未知。\n\n"
        + "标签集合：\n- "
        + "\n- ".join(labels)
        + hint
    )


def _build_accident_prompt(hint: str | None = None) -> str:
    extra = ""
    if hint:
        extra = f"\n补充线索（可能不准确，仅供参考）：{hint}\n"

    return (
        "你是交通事故图片分析助手。请仅根据图片内容判断是否发生事故，并输出严格 JSON。\n\n"
        "要求：\n"
        "1) 只输出一个 JSON 对象，不要输出任何解释、前后缀、代码块标记或多余字符。\n"
        "2) JSON 必须包含且仅包含以下字段：\n"
        "   - has_accident: true/false\n"
        "   - accident_type: 字符串（例如：追尾/侧面碰撞/翻车/撞护栏/对向相撞/行人事故/非机动车事故/多车连环/单车事故/占道/逆行/其他）\n"
        "   - severity: 字符串（轻微/中等/严重）\n"
        "   - description: 字符串（1-3 句中文，描述可见事实：车辆相对位置、受损情况、占道情况等；不确定就说明不确定）\n"
        "   - confidence: 0 到 1 之间的数字\n"
        "3) 若无法确认是否事故：has_accident=false，accident_type=其他，severity=轻微，confidence 取较低值。\n"
        + extra
    )


def _clean_label(text: str) -> str:
    if not text:
        return "未知"
    s = text.strip()
    # Prefer the first non-empty line
    for line in s.splitlines():
        line = line.strip()
        if line:
            s = line
            break
    # Strip common wrappers
    s = s.strip().strip("\"'")
    s = s.strip().strip("。.,，:：；;!！")
    return s or "未知"


def _extract_json_object(text: str) -> dict:
    if not text:
        raise ValueError("empty model output")

    s = text.strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(s[start : end + 1])
        if isinstance(obj, dict):
            return obj

    raise ValueError("model output is not a JSON object")


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _normalize_accident_result(obj: dict) -> dict:
    allowed_severity = {"轻微", "中等", "严重"}
    allowed_types = {
        "追尾",
        "侧面碰撞",
        "翻车",
        "撞护栏",
        "对向相撞",
        "行人事故",
        "非机动车事故",
        "多车连环",
        "单车事故",
        "占道",
        "逆行",
        "其他",
    }

    has_accident = bool(obj.get("has_accident", False))
    accident_type = str(obj.get("accident_type", "其他") or "其他").strip()
    severity = str(obj.get("severity", "轻微") or "轻微").strip()
    description = str(obj.get("description", "") or "").strip()

    conf_raw = obj.get("confidence", 0.0)
    try:
        confidence = _clamp01(float(conf_raw))
    except Exception:
        confidence = 0.0

    if severity not in allowed_severity:
        severity = "中等" if has_accident else "轻微"

    if accident_type not in allowed_types:
        accident_type = "其他"

    if not has_accident:
        if accident_type != "其他":
            accident_type = "其他"
        if severity not in allowed_severity:
            severity = "轻微"
        if confidence > 0.6:
            confidence = 0.6

    return {
        "has_accident": has_accident,
        "accident_type": accident_type,
        "severity": severity,
        "description": description,
        "confidence": confidence,
    }


def analyze_image(image_path: str, model: str, base_url: str, api_key: str, label_hint: str | None, verbose: bool) -> str:
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=api_key, base_url=base_url)

    data_url = _image_to_data_url(image_path)
    prompt = _build_prompt(label_hint=label_hint)

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=64,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    content = resp.choices[0].message.content or ""
    label = _clean_label(content)

    if verbose:
        print("raw_model_output:")
        print(content)

    return label


def analyze_accident(
    image_path: str,
    model: str,
    base_url: str,
    api_key: str,
    hint: str | None,
    verbose: bool,
) -> dict:
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=api_key, base_url=base_url)

    data_url = _image_to_data_url(image_path)
    prompt = _build_accident_prompt(hint=hint)

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=384,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    content = resp.choices[0].message.content or ""
    if verbose:
        print("raw_model_output:", file=sys.stderr)
        print(content, file=sys.stderr)

    obj = _extract_json_object(content)
    return _normalize_accident_result(obj)


def _try_upload_image(upload_url: str, image_path: str) -> dict[str, Any] | None:
    try:
        import requests  # type: ignore
    except ModuleNotFoundError:
        raise ModuleNotFoundError("requests")

    with open(image_path, "rb") as f:
        resp = requests.post(upload_url, files={"file": f}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data
    return None


def _try_post_accident(post_url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        import requests  # type: ignore
    except ModuleNotFoundError:
        raise ModuleNotFoundError("requests")

    resp = requests.post(post_url, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data
    return None


def main(argv: list[str]) -> int:
    _load_dotenv_if_present()

    parser = argparse.ArgumentParser(
        description="Analyze a traffic photo. Default outputs a strict JSON accident analysis; use --task label for legacy label output."
    )
    parser.add_argument("--image", "-i", required=True, help="Path to the accident photo (jpg/png/webp...).")
    parser.add_argument("--model", default=os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen3-VL-32B-Instruct"))
    parser.add_argument("--base-url", default=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"))
    parser.add_argument("--api-key", default=os.getenv("SILICONFLOW_API_KEY", ""))
    parser.add_argument("--hint", default=None, help="Optional hint text (e.g., location/time) to help classification.")
    parser.add_argument(
        "--task",
        choices=["accident", "label"],
        default="accident",
        help="Output mode: accident (strict JSON, default) or label (single tag).",
    )
    parser.add_argument("--upload", default=None, help="Optional upload URL (e.g. http://localhost:8000/api/uploads).")
    parser.add_argument("--post", default=None, help="Optional post URL (e.g. http://localhost:8000/api/accidents).")
    parser.add_argument("--source", default="script", help="Source label when posting to backend (default: script).")
    parser.add_argument("--verbose", action="store_true", help="Print raw model output for debugging.")
    args = parser.parse_args(argv)

    image_path = os.path.expanduser(args.image)
    if not os.path.isfile(image_path):
        print(f"error: image not found: {image_path}", file=sys.stderr)
        return 2

    if not args.api_key:
        print("error: missing SILICONFLOW_API_KEY (set it in .env or env var)", file=sys.stderr)
        return 2

    try:
        if args.task == "label":
            label = analyze_image(
                image_path=image_path,
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                label_hint=args.hint,
                verbose=args.verbose,
            )
            print(label)
        else:
            result = analyze_accident(
                image_path=image_path,
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                hint=args.hint,
                verbose=args.verbose,
            )

            upload_info = None
            if args.upload:
                try:
                    upload_info = _try_upload_image(args.upload, image_path)
                except Exception as e:
                    print(f"warn: upload failed: {e}", file=sys.stderr)

            post_payload: dict[str, Any] = dict(result)
            post_payload["source"] = args.source
            if args.hint:
                post_payload["hint"] = args.hint

            if upload_info and isinstance(upload_info.get("image_path"), str):
                post_payload["image_path"] = upload_info["image_path"]
                exif = upload_info.get("exif")
                if isinstance(exif, dict):
                    if "lat" in exif and "lng" in exif:
                        post_payload["lat"] = exif.get("lat")
                        post_payload["lng"] = exif.get("lng")
                        post_payload["location_source"] = exif.get("location_source") or "exif"
                        post_payload["location_confidence"] = exif.get("location_confidence")

            if args.post:
                try:
                    _ = _try_post_accident(args.post, post_payload)
                except Exception as e:
                    print(f"warn: post failed: {e}", file=sys.stderr)

            print(json.dumps(result, ensure_ascii=False))
    except ModuleNotFoundError as e:
        if "openai" in str(e):
            print("error: missing dependency 'openai'. Install: pip install openai", file=sys.stderr)
            return 2
        if "dotenv" in str(e):
            print("error: missing dependency 'python-dotenv'. Install: pip install python-dotenv", file=sys.stderr)
            return 2
        if "requests" in str(e):
            print("error: missing dependency 'requests'. Install: pip install requests", file=sys.stderr)
            return 2
        raise
    except Exception as e:
        print(f"error: request failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, cast


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
        "车辆侧翻",
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
        "   - accident_type: 字符串（例如：追尾/侧面碰撞/翻车/行人事故/撞护栏/对向相撞/非机动车事故/多车连环/单车事故/占道/逆行/其他）\n"
        "   - severity: 字符串（轻微/中等/严重）\n"
        "   - description: 字符串（1-3 句中文，描述可见事实：车辆相对位置、受损情况、占道情况等；不确定就说明不确定）\n"
        "   - confidence: 0 到 1 之间的数字\n"
        "   - location_text: 字符串或 null（地点/道路/地标的简短描述；无法判断就 null）\n"
        "   - lat: 数字或 null（纬度，-90 到 90；无法判断就 null）\n"
        "   - lng: 数字或 null（经度，-180 到 180；无法判断就 null）\n"
        "   - location_source: 字符串或 null（exif/watermark/model/hint/unknown；无法判断就 null）\n"
        "   - location_confidence: 0 到 1 的数字或 null（无法判断就 null）\n"
        "3) 若无法确认是否事故：has_accident=false，accident_type=其他，severity=轻微，confidence 取较低值。\n"
        "4) 如果图片右上角包含类似 'Lat: <...>, Lng: <...>' 的文字水印，请优先抄写为 lat/lng，并将 location_source=watermark。\n"
        + extra
    )


def _build_observation_prompt(hint: str | None = None) -> str:
    extra = ""
    if hint:
        extra = f"\n补充线索（可能不准确，仅供参考）：{hint}\n"

    # Important: this prompt asks ONLY for observable facts. No final classification.
    return (
        "你是交通事故图片‘事实抽取’助手。请仅根据图片内容抽取可见事实，并输出严格 JSON。\n\n"
        "要求：\n"
        "1) 只输出一个 JSON 对象，不要输出任何解释、前后缀、代码块标记或多余字符。\n"
        "2) JSON 必须包含且仅包含以下字段（缺失就填默认值，不能省略字段）：\n"
        "   - collision_evidence: true/false（是否有明确碰撞/事故现场证据：车辆明显受损、相互接触、翻车、撞护栏等；不确定填 false）\n"
        "   - vehicles_involved: 整数（0-10，估计涉及车辆数量；看不清填 0）\n"
        "   - collision_mode: 字符串（rear_end/side/head_on/single_vehicle/unknown）\n"
        "   - rollover: true/false（是否翻车/侧翻/车辆四轮朝天）\n"
        "   - guardrail_collision: true/false（是否撞护栏/隔离墩/路侧设施）\n"
        "   - pedestrian_involved: true/false（是否涉及行人）\n"
        "   - non_motor_involved: true/false（是否涉及非机动车：自行车/电动车/摩托车等）\n"
        "   - fire_or_smoke: true/false（是否有明显火焰或浓烟）\n"
        "   - wrong_way: true/false（是否有明确逆行线索；不确定填 false）\n"
        "   - lane_blockage: 字符串（none/partial/full/unknown，是否占道：不占道/部分占道/完全堵塞/不确定）\n"
        "   - damage_level: 字符串（minor/moderate/severe/unknown，基于可见变形/碎片/受损程度；不确定 unknown）\n"
        "   - scene_context_confidence: 0 到 1 的数字（仅用于场景上下文判断，如逆行；不确定填 0）\n"
        "   - description_facts: 字符串（1-3 句中文，只描述可见事实；不确定就说明不确定）\n"
        "   - location_text: 字符串或 null（地点/道路/地标的简短描述；无法判断就 null）\n"
        "   - lat: 数字或 null（纬度，-90 到 90；无法判断就 null）\n"
        "   - lng: 数字或 null（经度，-180 到 180；无法判断就 null）\n"
        "   - location_source: 字符串或 null（exif/watermark/model/hint/unknown；无法判断就 null）\n"
        "   - location_confidence: 0 到 1 的数字或 null（无法判断就 null）\n"
        "3) 如果图片右上角包含类似 'Lat: <...>, Lng: <...>' 的文字水印，请优先抄写为 lat/lng，并将 location_source=watermark。\n"
        "4) 注意：不要输出 accident_type、severity、has_accident 等最终结论字段。\n"
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


_FLOAT_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _parse_float_like(v: Any) -> float | None:
    if v is None:
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
        m = _FLOAT_RE.search(s)
        if not m:
            return None
        try:
            return float(m.group(0))
        except Exception:
            return None
    return None


def _normalize_location(obj: dict) -> dict[str, Any]:
    location_text = obj.get("location_text")
    if isinstance(location_text, str):
        location_text = location_text.strip() or None
        if location_text and len(location_text) > 256:
            location_text = location_text[:256]
    else:
        location_text = None

    lat = _parse_float_like(obj.get("lat"))
    lng = _parse_float_like(obj.get("lng"))
    if lat is not None and (lat < -90.0 or lat > 90.0):
        lat = None
    if lng is not None and (lng < -180.0 or lng > 180.0):
        lng = None

    src = obj.get("location_source")
    if isinstance(src, str):
        src = src.strip().lower() or None
    else:
        src = None
    allowed_src = {"exif", "watermark", "model", "hint", "unknown"}
    if src not in allowed_src:
        src = None

    lc = obj.get("location_confidence")
    location_confidence: float | None
    try:
        location_confidence = _clamp01(float(lc)) if lc is not None else None
    except Exception:
        location_confidence = None

    return {
        "location_text": location_text,
        "lat": lat,
        "lng": lng,
        "location_source": src,
        "location_confidence": location_confidence,
    }


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_dir() -> Path:
    return Path(".cache") / "smart_trans" / "accident_rag"


def _cache_key(image_path: str, rules_version: str, extractor_version: str) -> str:
    return f"{_sha256_file(image_path)}.{rules_version}.{extractor_version}"


def _cache_get(key: str) -> dict[str, Any] | None:
    p = _cache_dir() / f"{key}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_set(key: str, obj: dict[str, Any]) -> None:
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}.json"
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_observations(obj: dict) -> dict[str, Any]:
    def _bool(v: Any) -> bool:
        return bool(v)

    def _int01(v: Any, lo: int, hi: int) -> int:
        try:
            n = int(v)
        except Exception:
            return lo
        return max(lo, min(hi, n))

    allowed_mode = {"rear_end", "side", "head_on", "single_vehicle", "unknown"}
    allowed_block = {"none", "partial", "full", "unknown"}
    allowed_damage = {"minor", "moderate", "severe", "unknown"}

    mode = obj.get("collision_mode")
    if isinstance(mode, str):
        mode = mode.strip().lower()
    if mode not in allowed_mode:
        mode = "unknown"

    lane = obj.get("lane_blockage")
    if isinstance(lane, str):
        lane = lane.strip().lower()
    if lane not in allowed_block:
        lane = "unknown"

    dmg = obj.get("damage_level")
    if isinstance(dmg, str):
        dmg = dmg.strip().lower()
    if dmg not in allowed_damage:
        dmg = "unknown"

    sc = _parse_float_like(obj.get("scene_context_confidence"))
    scc = _clamp01(sc) if sc is not None else 0.0

    desc = obj.get("description_facts")
    if isinstance(desc, str):
        desc = desc.strip()
        if len(desc) > 2000:
            desc = desc[:2000]
    else:
        desc = ""

    out: dict[str, Any] = {
        "collision_evidence": _bool(obj.get("collision_evidence")),
        "vehicles_involved": _int01(obj.get("vehicles_involved"), 0, 10),
        "collision_mode": mode,
        "rollover": _bool(obj.get("rollover")),
        "guardrail_collision": _bool(obj.get("guardrail_collision")),
        "pedestrian_involved": _bool(obj.get("pedestrian_involved")),
        "non_motor_involved": _bool(obj.get("non_motor_involved")),
        "fire_or_smoke": _bool(obj.get("fire_or_smoke")),
        "wrong_way": _bool(obj.get("wrong_way")),
        "lane_blockage": lane,
        "damage_level": dmg,
        "scene_context_confidence": scc,
        "description_facts": desc,
    }

    out.update(_normalize_location(obj))
    return out


def _aggregate_observations(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return _normalize_observations({})

    n = len(items)

    def maj_bool(key: str) -> bool:
        t = sum(1 for it in items if bool(it.get(key)))
        return t > (n // 2)

    def median_int(key: str, default: int = 0) -> int:
        vals = []
        for it in items:
            try:
                vals.append(int(it.get(key) or 0))
            except Exception:
                continue
        if not vals:
            return default
        vals.sort()
        return int(vals[len(vals) // 2])

    def mode_str(key: str, order: list[str], default: str) -> str:
        counts: dict[str, int] = {}
        for it in items:
            v = it.get(key)
            if isinstance(v, str) and v:
                counts[v] = counts.get(v, 0) + 1
        if not counts:
            return default
        best = None
        best_c = -1
        for v, c in counts.items():
            if c > best_c:
                best = v
                best_c = c
            elif c == best_c and best is not None:
                # tie-break by preferred order
                try:
                    if order.index(v) < order.index(best):
                        best = v
                except ValueError:
                    pass
        return best or default

    def median_float(key: str) -> float | None:
        vals: list[float] = []
        for it in items:
            v = it.get(key)
            if v is None:
                continue
            try:
                vals.append(float(v))
            except Exception:
                continue
        if not vals:
            return None
        vals.sort()
        return float(vals[len(vals) // 2])

    collision_mode = mode_str("collision_mode", ["head_on", "rear_end", "side", "single_vehicle", "unknown"], "unknown")
    lane_blockage = mode_str("lane_blockage", ["full", "partial", "none", "unknown"], "unknown")
    damage_level = mode_str("damage_level", ["severe", "moderate", "minor", "unknown"], "unknown")

    # Prefer watermark/exif sources if present.
    loc_src = mode_str("location_source", ["watermark", "exif", "hint", "model", "unknown"], "unknown")
    location_text = None
    for it in items:
        lt = it.get("location_text")
        if isinstance(lt, str) and lt.strip():
            location_text = lt.strip()
            break

    lat = median_float("lat")
    lng = median_float("lng")
    if lat is not None and (lat < -90.0 or lat > 90.0):
        lat = None
    if lng is not None and (lng < -180.0 or lng > 180.0):
        lng = None

    lc = median_float("location_confidence")
    if lc is not None:
        lc = _clamp01(float(lc))

    desc = ""
    for it in items:
        s = it.get("description_facts")
        if isinstance(s, str) and s.strip():
            desc = s.strip()
            break

    return {
        "collision_evidence": maj_bool("collision_evidence"),
        "vehicles_involved": max(0, min(10, median_int("vehicles_involved", 0))),
        "collision_mode": collision_mode,
        "rollover": maj_bool("rollover"),
        "guardrail_collision": maj_bool("guardrail_collision"),
        "pedestrian_involved": maj_bool("pedestrian_involved"),
        "non_motor_involved": maj_bool("non_motor_involved"),
        "fire_or_smoke": maj_bool("fire_or_smoke"),
        "wrong_way": maj_bool("wrong_way"),
        "lane_blockage": lane_blockage,
        "damage_level": damage_level,
        "scene_context_confidence": max(0.0, min(1.0, float(median_float("scene_context_confidence") or 0.0))),
        "description_facts": desc,
        "location_text": location_text,
        "lat": lat,
        "lng": lng,
        "location_source": loc_src if loc_src != "unknown" else None,
        "location_confidence": lc,
    }


def _load_rules() -> dict[str, Any]:
    base = Path(__file__).resolve().parent
    p = base / "rag" / "rules.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _eval_when(when: dict[str, Any], obs: dict[str, Any]) -> bool:
    for k, cond in when.items():
        v = obs.get(k)
        if isinstance(cond, dict):
            if ">=" in cond:
                try:
                    vv = _parse_float_like(v)
                    cc = _parse_float_like(cond.get(">="))
                    if vv is None or cc is None:
                        return False
                    if float(vv) < float(cc):
                        return False
                except Exception:
                    return False
            elif "<=" in cond:
                try:
                    vv = _parse_float_like(v)
                    cc = _parse_float_like(cond.get("<="))
                    if vv is None or cc is None:
                        return False
                    if float(vv) > float(cc):
                        return False
                except Exception:
                    return False
            elif "in" in cond:
                options = cond.get("in")
                if not isinstance(options, list):
                    return False
                if v not in options:
                    return False
            else:
                return False
        else:
            if v != cond:
                return False
    return True


def _pick_rule(rules: list[dict[str, Any]], obs: dict[str, Any]) -> dict[str, Any] | None:
    matched = [r for r in rules if isinstance(r, dict) and isinstance(r.get("when"), dict) and _eval_when(r["when"], obs)]
    if not matched:
        return None
    matched.sort(key=lambda r: int(r.get("priority", 0)), reverse=True)
    return matched[0]


def _compute_confidence(obs: dict[str, Any], accident_type: str, rules_conf: dict[str, Any]) -> float:
    base_if_accident = float(rules_conf.get("base_if_accident", 0.55))
    base_if_no_accident = float(rules_conf.get("base_if_no_accident", 0.35))
    weights = rules_conf.get("weights")
    if not isinstance(weights, dict):
        weights = {}

    has_accident = bool(obs.get("collision_evidence"))
    c = base_if_accident if has_accident else base_if_no_accident

    def w(name: str) -> float:
        try:
            return float(weights.get(name, 0.0))
        except Exception:
            return 0.0

    if has_accident:
        c += w("collision_evidence")
    if obs.get("rollover"):
        c += w("rollover")
    if obs.get("fire_or_smoke"):
        c += w("fire_or_smoke")
    if obs.get("pedestrian_involved"):
        c += w("pedestrian_involved")
    if obs.get("non_motor_involved"):
        c += w("non_motor_involved")
    if obs.get("guardrail_collision"):
        c += w("guardrail_collision")
    if obs.get("wrong_way"):
        c += w("wrong_way")

    mode = obs.get("collision_mode")
    if mode == "head_on":
        c += w("head_on")
    elif mode == "rear_end":
        c += w("rear_end")
    elif mode == "side":
        c += w("side")

    try:
        if int(obs.get("vehicles_involved") or 0) >= 3:
            c += w("vehicles_3_plus")
    except Exception:
        pass

    dmg = obs.get("damage_level")
    if dmg == "moderate":
        c += w("damage_moderate")
    elif dmg == "severe":
        c += w("damage_severe")

    lane = obs.get("lane_blockage")
    if lane == "partial":
        c += w("lane_block_partial")
    elif lane == "full":
        c += w("lane_block_full")

    # Conservative cap when type is unclear.
    if accident_type == "其他" and has_accident:
        c = min(c, 0.75)
    if not has_accident:
        c = min(c, 0.6)

    return _clamp01(c)


def _render_description(accident_type: str, severity: str, obs: dict[str, Any]) -> str:
    facts = obs.get("description_facts")
    if isinstance(facts, str) and facts.strip():
        # Keep model-provided facts as the primary description, but keep it short.
        s = facts.strip()
        if len(s) > 400:
            s = s[:400]
        return s

    parts: list[str] = []
    vi = obs.get("vehicles_involved")
    try:
        n = int(vi or 0)
    except Exception:
        n = 0

    if accident_type in {"追尾", "侧面碰撞", "对向相撞", "多车连环", "单车事故", "翻车", "撞护栏"}:
        if n >= 1:
            parts.append(f"疑似{accident_type}，涉及约{n}辆车。")
        else:
            parts.append(f"疑似{accident_type}。")
    elif accident_type in {"行人事故", "非机动车事故"}:
        parts.append(f"疑似{accident_type}，请结合现场进一步确认。")
    elif accident_type == "占道":
        parts.append("疑似车辆占道影响通行，碰撞证据不明确。")
    else:
        parts.append("现场疑似存在异常情况，但事故类型不明确。")

    if obs.get("lane_blockage") == "full":
        parts.append("道路疑似出现完全堵塞。")
    elif obs.get("lane_blockage") == "partial":
        parts.append("道路疑似出现部分占道。")

    if severity in {"严重", "中等", "轻微"}:
        parts.append(f"严重程度评估：{severity}。")

    return "".join(parts)[:500]


def _split_knowledge_chunks(text: str) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    cur_title = ""
    cur_lines: list[str] = []

    def flush():
        nonlocal cur_title, cur_lines
        body = "\n".join(cur_lines).strip()
        if body:
            cid = hashlib.sha256((cur_title + "\n" + body).encode("utf-8")).hexdigest()[:12]
            chunks.append({"id": cid, "title": cur_title.strip() or "(untitled)", "text": body})
        cur_title = ""
        cur_lines = []

    for line in text.splitlines():
        if line.startswith("### "):
            flush()
            cur_title = line[4:].strip()
            continue
        if line.startswith("## "):
            flush()
            cur_title = line[3:].strip()
            continue
        cur_lines.append(line)
    flush()
    return chunks


def _retrieve_notes(query_terms: list[str], top_k: int = 3) -> list[dict[str, Any]]:
    base = Path(__file__).resolve().parent
    p = base / "rag" / "knowledge.md"
    txt = p.read_text(encoding="utf-8")
    chunks = _split_knowledge_chunks(txt)

    terms = [t.strip() for t in query_terms if t and t.strip()]
    if not terms:
        return []

    scored: list[tuple[int, dict[str, str]]] = []
    for ch in chunks:
        hay = (ch.get("title", "") + "\n" + ch.get("text", "")).lower()
        s = 0
        for t in terms:
            tl = t.lower()
            if not tl:
                continue
            # substring match works better for short Chinese keywords.
            s += hay.count(tl)
        if s > 0:
            scored.append((s, ch))

    scored.sort(key=lambda x: (-x[0], x[1].get("title", "")))
    out: list[dict[str, Any]] = []
    for score, ch in scored[: max(0, int(top_k))]:
        snippet = ch.get("text", "")
        if len(snippet) > 400:
            snippet = snippet[:400]
        out.append({"id": ch.get("id"), "title": ch.get("title"), "score": score, "snippet": snippet})
    return out


def analyze_accident_rag(
    image_path: str,
    model: str,
    base_url: str,
    api_key: str,
    hint: str | None,
    verbose: bool,
    extract_runs: int,
    use_cache: bool,
    refresh_cache: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from openai import OpenAI  # type: ignore

    rules = _load_rules()
    rules_version = str(rules.get("version") or "0")
    extractor_version = "obs_prompt_v1"

    ck = _cache_key(image_path, rules_version=rules_version, extractor_version=extractor_version)
    if use_cache and not refresh_cache:
        cached = _cache_get(ck)
        if isinstance(cached, dict) and isinstance(cached.get("result"), dict):
            return cached["result"], cached

    client = OpenAI(api_key=api_key, base_url=base_url)
    data_url = _image_to_data_url(image_path)
    prompt = _build_observation_prompt(hint=hint)

    runs = max(1, min(7, int(extract_runs)))
    raw_outputs: list[str] = []
    obs_items: list[dict[str, Any]] = []
    for _i in range(runs):
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=512,
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
        raw_outputs.append(content)
        try:
            obj = _extract_json_object(content)
            obs_items.append(_normalize_observations(obj))
        except Exception:
            obs_items.append(_normalize_observations({}))
        # Small delay to reduce provider-side jitter bursts.
        if runs >= 2:
            time.sleep(0.15)

    obs = _aggregate_observations(obs_items)

    # Local deterministic classification via rules.
    ts = rules.get("accident_type")
    ss = rules.get("severity")
    type_spec: dict[str, Any] = cast(dict[str, Any], ts) if isinstance(ts, dict) else {}
    sev_spec: dict[str, Any] = cast(dict[str, Any], ss) if isinstance(ss, dict) else {}

    type_rule = _pick_rule(type_spec.get("rules", []) if isinstance(type_spec.get("rules"), list) else [], obs)
    accident_type = str((type_rule or {}).get("set") or type_spec.get("default") or "其他")

    sev_rule = _pick_rule(sev_spec.get("rules", []) if isinstance(sev_spec.get("rules"), list) else [], obs)
    severity = str((sev_rule or {}).get("set") or sev_spec.get("default") or "中等")

    allowed_types = set(type_spec.get("allowed") or [])
    if allowed_types and accident_type not in allowed_types:
        accident_type = "其他"

    allowed_sev = set(sev_spec.get("allowed") or [])
    if allowed_sev and severity not in allowed_sev:
        severity = "中等" if bool(obs.get("collision_evidence")) else "轻微"

    has_accident = bool(obs.get("collision_evidence"))
    if not has_accident:
        # We treat has_accident as "collision/accident evidence".
        # Non-collision traffic issues (e.g. lane blockage / wrong-way) should still be preserved.
        if accident_type not in {"占道", "逆行"}:
            accident_type = "其他"
        severity = "轻微"

    confidence = _compute_confidence(obs, accident_type=accident_type, rules_conf=rules.get("confidence") or {})
    description = _render_description(accident_type, severity, obs)

    query_terms = [
        accident_type,
        severity,
        "翻车" if obs.get("rollover") else "",
        "护栏" if obs.get("guardrail_collision") else "",
        "行人" if obs.get("pedestrian_involved") else "",
        "非机动车" if obs.get("non_motor_involved") else "",
        "对向" if obs.get("collision_mode") == "head_on" else "",
        "追尾" if obs.get("collision_mode") == "rear_end" else "",
        "侧面" if obs.get("collision_mode") == "side" else "",
        "连环" if (obs.get("vehicles_involved") or 0) >= 3 else "",
        "占道" if obs.get("lane_blockage") in {"partial", "full"} else "",
    ]
    retrieved = _retrieve_notes(query_terms, top_k=3)

    trace: dict[str, Any] = {
        "mode": "rag",
        "rules_version": rules_version,
        "extractor_version": extractor_version,
        "extract_runs": runs,
        "observations": obs,
        "matched_rules": {
            "accident_type": {"id": (type_rule or {}).get("id"), "note": (type_rule or {}).get("note")},
            "severity": {"id": (sev_rule or {}).get("id"), "note": (sev_rule or {}).get("note")},
        },
        "retrieved_notes": retrieved,
        "raw_extractor_outputs": [s[:2000] for s in raw_outputs[: min(3, len(raw_outputs))]],
    }
    raw_model_output = json.dumps(trace, ensure_ascii=False)
    if len(raw_model_output) > 18000:
        trace["raw_extractor_outputs"] = ["(truncated)"]
        trace["retrieved_notes"] = trace.get("retrieved_notes", [])[:1]
        raw_model_output = json.dumps(trace, ensure_ascii=False)
        raw_model_output = raw_model_output[:18000]

    result = {
        "has_accident": has_accident,
        "accident_type": accident_type,
        "severity": severity,
        "description": description,
        "confidence": confidence,
        "location_text": obs.get("location_text"),
        "lat": obs.get("lat"),
        "lng": obs.get("lng"),
        "location_source": obs.get("location_source"),
        "location_confidence": obs.get("location_confidence"),
        "raw_model_output": raw_model_output,
    }

    # Cache the final normalized result.
    cache_obj = {"result": result, "trace": trace}
    if use_cache:
        _cache_set(ck, cache_obj)
    return result, cache_obj


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

    out: dict[str, Any] = {
        "has_accident": has_accident,
        "accident_type": accident_type,
        "severity": severity,
        "description": description,
        "confidence": confidence,
    }

    out.update(_normalize_location(obj))
    return out


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
        description=(
            "Analyze a traffic photo. Default outputs a strict JSON accident analysis. "
            "Use --task rag for deterministic rules + RAG trace; use --task label for legacy label output."
        )
    )
    parser.add_argument("--image", "-i", required=True, help="Path to the accident photo (jpg/png/webp...).")
    parser.add_argument("--model", default=os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen3-VL-32B-Instruct"))
    parser.add_argument("--base-url", default=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"))
    parser.add_argument("--api-key", default=os.getenv("SILICONFLOW_API_KEY", ""))
    parser.add_argument("--hint", default=None, help="Optional hint text (e.g., location/time) to help classification.")
    parser.add_argument(
        "--task",
        choices=["accident", "rag", "label"],
        default="accident",
        help="Output mode: accident (model JSON), rag (rules + RAG trace), or label (single tag).",
    )
    parser.add_argument(
        "--extract-runs",
        type=int,
        default=3,
        help="RAG mode only: number of fact-extraction runs to aggregate (1-7).",
    )
    parser.add_argument("--no-cache", action="store_true", help="RAG mode only: disable on-disk result cache.")
    parser.add_argument("--refresh-cache", action="store_true", help="RAG mode only: ignore cache and recompute.")
    parser.add_argument("--upload", default=None, help="Optional upload URL (e.g. http://localhost:28000/api/uploads).")
    parser.add_argument("--post", default=None, help="Optional post URL (e.g. http://localhost:28000/api/accidents).")
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
        elif args.task == "rag":
            result, _cache_meta = analyze_accident_rag(
                image_path=image_path,
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                hint=args.hint,
                verbose=args.verbose,
                extract_runs=args.extract_runs,
                use_cache=not bool(args.no_cache),
                refresh_cache=bool(args.refresh_cache),
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

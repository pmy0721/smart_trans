from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import asyncio

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from openai import OpenAI

from app.law_rag import retrieve_law_snippets_resilient

from app.db import SessionLocal
from app.models import Accident
from app.stamp_coords import stamp_inplace_hz
from app.utils import (
    clamp01,
    image_url_for_path,
    now_bjt_naive,
    try_extract_exif_gps,
    uploads_dir,
)


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

    # Optional multi-frame ingest metadata. When present, this job was created
    # by /api/ingest_triplet and contains per-frame file info.
    frames: list[dict[str, Any]] | None = None

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


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        return


def _extract_json_obj(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if not s:
        raise ValueError("empty model output")

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


def _build_triplet_summary_prompt(
    *, frames: list[dict[str, Any]], hint: str | None
) -> str:
    # Keep input compact to reduce token use. Ensure order is preserved.
    items = []
    for f in frames:
        key = str(f.get("key") or "")
        a_any = f.get("analysis")
        a = cast(dict[str, Any], a_any) if isinstance(a_any, dict) else {}

        items.append(
            {
                "frame": key,
                "has_accident": a.get("has_accident"),
                "accident_type": a.get("accident_type"),
                "severity": a.get("severity"),
                "confidence": a.get("confidence"),
                "description": a.get("description"),
                "location_text": a.get("location_text"),
                "lat": a.get("lat"),
                "lng": a.get("lng"),
                "location_source": a.get("location_source"),
                "location_confidence": a.get("location_confidence"),
            }
        )

    payload = {
        "hint": hint,
        "frames_in_time_order": items,
    }

    return (
        "你是交通事故多帧图像分析专家。你会收到同一事件的三帧分析结果（按时间从早到晚：t-3s, t-1s, t0）。\n"
        "请你基于这些结果进行归纳与推理，输出一份详细、可读的事故分析报告，并给出‘事故原因’。\n\n"
        "要求：\n"
        "1) 只输出一个 JSON 对象，不要输出任何解释、前后缀、代码块标记或多余字符。\n"
        "2) JSON 必须包含以下字段：\n"
        "   - cause: 字符串（1-3 句中文，说明最可能的事故原因；不确定要说明不确定与原因）\n"
        "   - report: 字符串（详细事故分析报告，中文，建议包含：过程还原/关键证据/不确定性/建议补充信息）\n"
        "   - key_facts: 字符串数组（3-12 条，按重要性排序，尽量客观可验证）\n"
        "3) 允许指出各帧之间的矛盾与不确定性；不要编造图片中不存在的细节。\n\n"
        f"输入（JSON）：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _law_query_terms(*, out: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    try:
        cause = out.get("cause")
        if isinstance(cause, str):
            terms.append(cause)
        report = out.get("report")
        if isinstance(report, str):
            terms.append(report[:400])
        kf = out.get("key_facts")
        if isinstance(kf, list):
            for x in kf:
                if isinstance(x, str):
                    terms.append(x)
        frames = out.get("frames")
        if isinstance(frames, list):
            for f in frames:
                if not isinstance(f, dict):
                    continue
                a = f.get("analysis") if isinstance(f.get("analysis"), dict) else None
                if not isinstance(a, dict):
                    continue
                for k in ["accident_type", "severity", "description"]:
                    v = a.get(k)
                    if isinstance(v, str) and v.strip():
                        terms.append(v.strip())
    except Exception:
        return terms

    # Compact tokenization: split by common punct/whitespace.
    out_terms: list[str] = []
    for t in terms:
        s = str(t)
        for seg in re.split(r"[\s\n\r\t，,。.;；:：、】【\[\]()（）/\\|]+", s):
            seg = seg.strip()
            if seg:
                out_terms.append(seg)

    # Some high-recall expansions for common traffic concepts.
    expansions = {
        "追尾": ["安全距离", "同车道"],
        "侧面碰撞": ["变道", "交叉口"],
        "对向相撞": ["会车", "逆行"],
        "占道": ["违法停车", "临时停车"],
        "逆行": ["会车", "车道"],
        "路口": ["交叉路口", "信号灯"],
        "闯红灯": ["交通信号灯"],
        "变道": ["变更车道", "压实线"],
        "实线": ["标线"],
        "人行横道": ["斑马线", "让行"],
    }
    for k, vs in expansions.items():
        if any(k in x for x in out_terms):
            out_terms.extend(vs)

    # De-dup + trim.
    uniq: list[str] = []
    seen: set[str] = set()
    for x in out_terms:
        y = x.strip()
        if not y:
            continue
        if len(y) < 2 and not any(ch.isdigit() for ch in y):
            continue
        if y in seen:
            continue
        seen.add(y)
        uniq.append(y)
        if len(uniq) >= 64:
            break
    return uniq


def _build_law_prompt(
    *,
    hint: str | None,
    cause: str | None,
    report: str | None,
    key_facts: list[str] | None,
    law_snippets: list[dict[str, Any]],
) -> str:
    payload = {
        "hint": hint,
        "cause": cause,
        "report": (report[:3000] if isinstance(report, str) else report),
        "key_facts": key_facts or [],
        "law_snippets": [
            {
                "snippet_id": s.get("id"),
                "source": s.get("source"),
                "title": s.get("title"),
                "snippet": s.get("snippet"),
            }
            for s in law_snippets
        ],
    }

    return (
        "你是中国道路交通事故法律与交管规范分析助手。\n"
        "你会收到同一事故的分析结果（事故原因/报告/要点）以及从法规文件中检索出的条款片段。\n"
        "请基于检索片段进行‘定性’分析，并给出引用依据。\n\n"
        "要求：\n"
        "1) 只输出一个 JSON 对象，不要输出任何解释、前后缀、代码块标记或多余字符。\n"
        "2) JSON 必须包含字段：\n"
        "   - legal_qualitative: 字符串（文字定性结论，说明可能涉及的违法/过错类型；不确定要说明不确定，但也要给出最可能情形）\n"
        "   - law_refs: 数组（1-6 条），每条包含：snippet_id/source/title/quote/relevance\n"
        "3) snippet_id 必须来自输入 law_snippets 的 snippet_id。\n"
        "4) quote 必须是输入 law_snippets[].snippet 的原文子串（直接复制片段中的句子），不要编造条文。\n"
        "5) 即使证据不足也请给出低确定性的定性与引用（并在 relevance 里说明不确定性与需要补充的事实）。\n\n"
        f"输入（JSON）：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _fallback_legal_qualitative(*, out: dict[str, Any]) -> str:
    # Low-certainty, but always present.
    types: list[str] = []
    sevs: list[str] = []
    try:
        frames = out.get("frames")
        if isinstance(frames, list):
            for f in frames:
                if not isinstance(f, dict):
                    continue
                a = f.get("analysis") if isinstance(f.get("analysis"), dict) else None
                if not isinstance(a, dict):
                    continue
                t = a.get("accident_type")
                if isinstance(t, str) and t.strip():
                    types.append(t.strip())
                s = a.get("severity")
                if isinstance(s, str) and s.strip():
                    sevs.append(s.strip())
    except Exception:
        pass

    at = types[0] if types else "其他"
    sev = sevs[0] if sevs else "中等"
    cause = out.get("cause") if isinstance(out.get("cause"), str) else None
    head = f"基于三帧图片的自动分析结果，事故类型倾向为“{at}”，严重程度倾向为“{sev}”。"
    if cause and cause.strip():
        return head + f"事故原因推测：{cause.strip()}（仅供参考，以交管部门认定为准）。"
    return head + "由于证据有限，需结合现场/视频/当事人陈述进一步确认（仅供参考）。"


def _fallback_law_refs(*, law_snippets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if not law_snippets:
        return [
            {
                "snippet_id": None,
                "source": "(missing) law_kb",
                "title": "(no law snippets)",
                "quote": "未能加载或检索到法规片段，无法给出条文引用。",
                "relevance": "请确认已生成 rag/law_kb.jsonl 或设置 SMART_TRANS_LAW_KB。",
            }
        ]
    for s in law_snippets[:3]:
        if not isinstance(s, dict):
            continue
        snippet = str(s.get("snippet") or "").strip()
        if not snippet:
            continue
        quote = snippet
        if len(quote) > 240:
            quote = quote[:240]
        refs.append(
            {
                "snippet_id": s.get("id"),
                "source": str(s.get("source") or "")[:200],
                "title": str(s.get("title") or "")[:200],
                "quote": quote,
                "relevance": "自动检索到的可能相关条款片段（模型定性失败/缺少配置时的兜底引用）。",
            }
        )
    return refs


def _validate_law_refs(
    *, law_refs: list[dict[str, Any]], law_snippets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # Ensure each quote is a substring of its referenced snippet.
    by_id: dict[str, dict[str, Any]] = {}
    for s in law_snippets:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if sid:
            by_id[sid] = s

    out_refs: list[dict[str, Any]] = []
    for r in law_refs[:6]:
        if not isinstance(r, dict):
            continue
        sid = str(r.get("snippet_id") or "").strip()
        src = str(r.get("source") or "").strip()
        title = str(r.get("title") or "").strip()
        quote = str(r.get("quote") or "").strip()
        rel = str(r.get("relevance") or "").strip()
        if not quote:
            continue

        sn = by_id.get(sid) if sid else None
        sn_text = str(sn.get("snippet") or "") if isinstance(sn, dict) else ""
        if sn_text and quote not in sn_text:
            # Auto-adjust quote to a leading slice of the snippet.
            quote = sn_text[: min(240, len(sn_text))].strip()
            if rel:
                rel = rel + " (quote auto-adjusted)"
            else:
                rel = "(quote auto-adjusted)"

        out_refs.append(
            {
                "snippet_id": sid or None,
                "source": src[:200],
                "title": title[:200],
                "quote": quote[:800],
                "relevance": rel[:400],
            }
        )
    return out_refs


def _law_qualify_via_llm(*, prompt: str) -> dict[str, Any]:
    _load_dotenv_if_present()

    api_key = (
        os.getenv("SMART_TRANS_LAW_API_KEY")
        or os.getenv("SMART_TRANS_SUMMARY_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("SILICONFLOW_API_KEY")
        or ""
    ).strip()
    base_url = (
        os.getenv("SMART_TRANS_LAW_BASE_URL")
        or os.getenv("SMART_TRANS_SUMMARY_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or os.getenv("SILICONFLOW_BASE_URL")
        or ""
    ).strip()
    model = (
        os.getenv("SMART_TRANS_LAW_MODEL")
        or os.getenv("SMART_TRANS_SUMMARY_MODEL")
        or "Pro/deepseek-ai/DeepSeek-V3.2"
    ).strip()

    if not api_key or not base_url:
        raise RuntimeError(
            "missing law LLM config: set SMART_TRANS_LAW_API_KEY/SMART_TRANS_LAW_BASE_URL"
        )

    client = OpenAI(api_key=api_key, base_url=base_url)
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你只输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    took_ms = int((time.monotonic() - t0) * 1000)
    text = ""
    try:
        text = resp.choices[0].message.content or ""
    except Exception:
        text = ""

    obj = _extract_json_obj(text)
    legal_qualitative = str(obj.get("legal_qualitative") or "").strip()
    if len(legal_qualitative) > 4000:
        legal_qualitative = legal_qualitative[:4000]

    refs_any = obj.get("law_refs")
    refs: list[dict[str, Any]] = []
    if isinstance(refs_any, list):
        for r in refs_any:
            if not isinstance(r, dict):
                continue
            sid = str(r.get("snippet_id") or "").strip()
            src = str(r.get("source") or "").strip()
            title = str(r.get("title") or "").strip()
            quote = str(r.get("quote") or "").strip()
            rel = str(r.get("relevance") or "").strip()
            if not quote:
                continue
            refs.append(
                {
                    "snippet_id": sid[:40],
                    "source": src[:200],
                    "title": title[:200],
                    "quote": quote[:800],
                    "relevance": rel[:400],
                }
            )
            if len(refs) >= 6:
                break

    return {
        "ok": True,
        "model": model,
        "took_ms": took_ms,
        "legal_qualitative": legal_qualitative,
        "law_refs": refs,
        "raw": text[:20000],
    }


def _summarize_triplet_via_llm(
    *, frames: list[dict[str, Any]], hint: str | None
) -> dict[str, Any]:
    _load_dotenv_if_present()

    api_key = (
        os.getenv("SMART_TRANS_SUMMARY_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("SILICONFLOW_API_KEY")
        or ""
    ).strip()
    base_url = (
        os.getenv("SMART_TRANS_SUMMARY_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or os.getenv("SILICONFLOW_BASE_URL")
        or ""
    ).strip()
    model = (
        os.getenv("SMART_TRANS_SUMMARY_MODEL") or "Pro/deepseek-ai/DeepSeek-V3.2"
    ).strip()

    if not api_key:
        raise RuntimeError(
            "missing summary api key: set SMART_TRANS_SUMMARY_API_KEY (or DEEPSEEK_API_KEY/SILICONFLOW_API_KEY)"
        )
    if not base_url:
        raise RuntimeError(
            "missing summary base url: set SMART_TRANS_SUMMARY_BASE_URL (or DEEPSEEK_BASE_URL/SILICONFLOW_BASE_URL)"
        )

    prompt = _build_triplet_summary_prompt(frames=frames, hint=hint)

    client = OpenAI(api_key=api_key, base_url=base_url)
    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是严谨的交通事故分析与归因助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    took_ms = int((time.monotonic() - t0) * 1000)

    text = ""
    try:
        text = resp.choices[0].message.content or ""
    except Exception:
        text = ""

    obj = _extract_json_obj(text)
    cause = str(obj.get("cause") or "").strip()
    report = str(obj.get("report") or "").strip()
    key_facts = obj.get("key_facts")
    if not isinstance(key_facts, list):
        key_facts = []
    key_facts_clean: list[str] = []
    for x in key_facts:
        if not isinstance(x, str):
            continue
        s = x.strip()
        if s:
            key_facts_clean.append(s[:200])
        if len(key_facts_clean) >= 12:
            break

    if len(cause) > 800:
        cause = cause[:800]
    if len(report) > 12000:
        report = report[:12000]

    return {
        "ok": True,
        "model": model,
        "base_url": base_url,
        "took_ms": took_ms,
        "cause": cause,
        "report": report,
        "key_facts": key_facts_clean,
        "raw": text[:20000],
    }


def _severity_to_beeps(severity: str | None) -> int:
    s = (severity or "").strip()
    if s == "轻微":
        return 1
    if s == "中等":
        return 2
    if s == "严重":
        return 3
    return 1


def _early_beep_enabled() -> bool:
    """Whether to beep as soon as Qwen-VL analysis is available.

    Defaults to enabled when SMART_TRANS_EARLY_BEEP is not set.
    """

    if os.getenv("SMART_TRANS_EARLY_BEEP") is None:
        return True
    return _env_flag("SMART_TRANS_EARLY_BEEP")


def _maybe_beep_early(
    *,
    job_id: str | None,
    frame_key: str | None,
    has_accident: bool,
    severity: str | None,
) -> tuple[bool, bool, str | None]:
    """Best-effort beeping right after analyzer result is ready."""

    if not _early_beep_enabled():
        return False, False, None

    disable_beep = _env_flag("SMART_TRANS_DISABLE_BEEP")
    enable_beep = _env_flag("SMART_TRANS_ENABLE_BEEP")
    if not enable_beep or disable_beep or not bool(has_accident):
        return False, False, None

    url = (
        os.getenv("SMART_TRANS_BEEP_MCP_URL", "http://localhost:9010/sse").strip()
        or "http://localhost:9010/sse"
    )
    on_time = float(os.getenv("SMART_TRANS_BEEP_ON_TIME", "0.3"))
    gap = float(os.getenv("SMART_TRANS_BEEP_GAP", "0.3"))
    beeps = _severity_to_beeps(severity)

    def _write_artifact(text: str) -> None:
        if not job_id:
            return
        try:
            artifacts = _job_artifacts_root(job_id)
            name = "beep.early.txt" if not frame_key else f"beep.early.{frame_key}.txt"
            (artifacts / name).write_text(text, encoding="utf-8")
        except Exception:
            return

    try:
        asyncio.run(_beep_n_via_mcp(n=beeps, url=url, on_time=on_time, gap=gap))
        _write_artifact(
            f"ok=1\nframe={frame_key or ''}\nseverity={severity or ''}\nbeeps={beeps}\ntime={_now_iso()}\n"
        )
        return True, True, None
    except Exception as e:
        _write_artifact(
            f"ok=0\nframe={frame_key or ''}\nseverity={severity or ''}\nbeeps={beeps}\ntime={_now_iso()}\nerror={e}\n"
        )
        return True, False, str(e)


def _maybe_beep_after_store(
    *, has_accident: bool, severity: str | None
) -> tuple[bool, bool, str | None]:
    """Best-effort beeping after a successful DB write."""

    # Behavior change: the project now beeps as soon as Qwen-VL output is ready.
    # After-store beeping is intentionally disabled to avoid duplicate alarms.
    return False, False, None


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
            p.write_text(
                json.dumps(j.__dict__, ensure_ascii=True, indent=2), encoding="utf-8"
            )
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
runner = Runner(
    max_concurrency=int(os.getenv("SMART_TRANS_PIPELINE_MAX_CONCURRENCY", "1"))
)


def _inflight_jobs_count(max_scan: int = 2000) -> int:
    jobs = store.list(limit=max(1, int(max_scan)))
    return sum(1 for j in jobs if j.status in {"queued", "running"})


def _run_analyzer_triplet(job_id: str) -> None:
    j = store.get(job_id)
    if j is None:
        return

    frames = j.frames if isinstance(getattr(j, "frames", None), list) else None
    if not frames:
        store.update(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            error="missing frames for triplet job",
        )
        return

    store.update(job_id, status="running", started_at=_now_iso())
    j = store.get(job_id)
    if j is None:
        return

    repo = _repo_root()
    script = (repo / "traffic_issue_analyzer.py").resolve()
    if not script.is_file():
        store.update(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            error=f"missing analyzer script: {script}",
        )
        return

    task = (j.task or "rag").strip().lower()
    if task not in {"rag", "accident"}:
        task = "rag"

    extract_runs = int(j.extract_runs or 3)
    if extract_runs < 1:
        extract_runs = 1
    if extract_runs > 7:
        extract_runs = 7

    hint = j.hint

    # Early beep state: only trigger once (t0 frame).
    early_beep_lock = threading.Lock()
    early_beep_attempted = False
    early_beep_ok = False
    early_beep_error: str | None = None

    def _run_one(frame: dict[str, Any]) -> dict[str, Any]:
        nonlocal early_beep_attempted, early_beep_ok, early_beep_error

        key = str(frame.get("key") or "").strip() or "frame"
        saved_file = str(frame.get("saved_file") or "").strip()
        image_path = frame.get("image_path")
        image_url = frame.get("image_url")
        exif = frame.get("exif") if isinstance(frame.get("exif"), dict) else None
        stamp = frame.get("stamp") if isinstance(frame.get("stamp"), dict) else None

        if not saved_file:
            return {
                "key": key,
                "image_path": image_path,
                "image_url": image_url,
                "ok": False,
                "error": "missing saved_file",
            }

        # Provide per-frame hint to encourage richer pre-accident descriptions.
        frame_ctx = ""
        if key == "t-3s":
            frame_ctx = (
                "[frame=t-3s 事故前3秒 固定机位] 请详细描述事故前态势：道路/车道/标线/信号灯（如可见）、"
                "各车辆相对位置与运动趋势、是否接近路口/停止线、潜在风险线索；即使未见碰撞也必须写清楚。"
            )
        elif key == "t-1s":
            frame_ctx = (
                "[frame=t-1s 事故前1秒 固定机位] 请详细描述事故前态势与变化：车辆距离变化/并行或交汇/"
                "疑似变道或转向、路口要素（如可见）；即使未见碰撞也必须写清楚。"
            )
        elif key == "t0":
            frame_ctx = (
                "[frame=t0 事故发生时 固定机位] 请重点描述碰撞/倒地/受损/碎片/占道等证据点与位置关系，"
                "并说明不确定之处。"
            )

        hint2 = hint
        if frame_ctx:
            base = (hint or "").strip()
            if len(base) > 200:
                base = base[:200]
            hint2 = (base + "\n" + frame_ctx).strip() if base else frame_ctx

        cmd: list[str] = [
            sys.executable,
            str(script),
            "-i",
            saved_file,
            "--task",
            task,
        ]
        if task == "rag":
            cmd += ["--extract-runs", str(extract_runs)]
        if hint2:
            cmd += ["--hint", hint2]

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(repo),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as e:
            return {
                "key": key,
                "image_path": image_path,
                "image_url": image_url,
                "ok": False,
                "error": str(e),
                "command": cmd,
            }

        took_ms = int((time.monotonic() - t0) * 1000)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Persist artifacts per frame.
        artifacts = _job_artifacts_root(job_id) / "frames" / key
        try:
            artifacts.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            (artifacts / "analyzer.stdout.txt").write_text(
                stdout[-200000:], encoding="utf-8"
            )
            (artifacts / "analyzer.stderr.txt").write_text(
                stderr[-200000:], encoding="utf-8"
            )
        except Exception:
            pass

        result_obj = _parse_result_from_stdout(stdout)
        if proc.returncode != 0:
            return {
                "key": key,
                "image_path": image_path,
                "image_url": image_url,
                "ok": False,
                "error": f"analyzer exit code {proc.returncode}",
                "returncode": int(proc.returncode),
                "command": cmd,
                "stdout": stdout[-20000:],
                "stderr": stderr[-20000:],
                "analysis": result_obj,
                "took_ms": took_ms,
            }

        if not isinstance(result_obj, dict):
            return {
                "key": key,
                "image_path": image_path,
                "image_url": image_url,
                "ok": False,
                "error": "failed to parse analyzer JSON output",
                "returncode": int(proc.returncode),
                "command": cmd,
                "stdout": stdout[-20000:],
                "stderr": stderr[-20000:],
                "analysis": None,
                "took_ms": took_ms,
            }

        # Prefer stamped coords if analyzer didn't produce coords.
        try:
            if result_obj.get("lat") is None and result_obj.get("lng") is None:
                if isinstance(stamp, dict) and bool(stamp.get("ok")):
                    if stamp.get("lat") is not None and stamp.get("lng") is not None:
                        result_obj["lat"] = stamp.get("lat")
                        result_obj["lng"] = stamp.get("lng")
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
                    result_obj["location_source"] = (
                        exif.get("location_source") or "exif"
                    )
                    result_obj["location_confidence"] = exif.get("location_confidence")

        # Keep job file size bounded.
        try:
            raw_model_output = result_obj.get("raw_model_output")
            if raw_model_output is not None and not isinstance(raw_model_output, str):
                raw_model_output = json.dumps(raw_model_output, ensure_ascii=False)
                result_obj["raw_model_output"] = raw_model_output
            if isinstance(raw_model_output, str) and len(raw_model_output) > 20000:
                result_obj["raw_model_output"] = raw_model_output[:20000]
        except Exception:
            pass

        # Early beep: only for the t0 frame.
        if key == "t0":
            try:
                has_accident = bool(result_obj.get("has_accident"))
                sev = str(result_obj.get("severity") or "").strip() or None
            except Exception:
                has_accident = False
                sev = None

            if has_accident:
                with early_beep_lock:
                    if not early_beep_attempted:
                        early_beep_attempted, early_beep_ok, early_beep_error = (
                            _maybe_beep_early(
                                job_id=job_id,
                                frame_key="t0",
                                has_accident=has_accident,
                                severity=sev,
                            )
                        )
                        if early_beep_attempted:
                            store.update(
                                job_id,
                                beep_attempted=early_beep_attempted,
                                beep_ok=early_beep_ok,
                                beep_error=early_beep_error,
                            )

        return {
            "key": key,
            "image_path": image_path,
            "image_url": image_url,
            "ok": True,
            "returncode": int(proc.returncode),
            "command": cmd,
            "analysis": result_obj,
            "took_ms": took_ms,
        }

    # Run 3 frames in parallel.
    max_workers = min(3, max(1, len(frames)))
    results_by_key: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_run_one, f) for f in frames]
            for fut in as_completed(futs):
                try:
                    r = fut.result()
                except Exception as e:
                    errors.append(str(e))
                    continue
                key = str(r.get("key") or "")
                results_by_key[key] = r
                if not bool(r.get("ok")):
                    errors.append(str(r.get("error") or "frame failed"))
    except Exception as e:
        store.update(job_id, status="failed", finished_at=_now_iso(), error=str(e))
        return

    # Preserve original order (t-3s, t-1s, t0).
    ordered: list[dict[str, Any]] = []
    for f in frames:
        key = str(f.get("key") or "").strip() or "frame"
        ordered.append(
            results_by_key.get(key)
            or {"key": key, "ok": False, "error": "missing result"}
        )

    out = {
        "mode": "triplet",
        "task": task,
        "extract_runs": extract_runs,
        "hint": hint,
        "frames": ordered,
    }

    # Stage 3: summarize ordered frame analyses via DeepSeek-V3.2 (best-effort).
    summary_ok = False
    try:
        # Run even if some frames failed; the prompt will reflect missing info.
        any_ok = any(
            isinstance(f, dict)
            and bool(f.get("ok"))
            and isinstance(f.get("analysis"), dict)
            for f in ordered
        )
        if any_ok:
            summary = _summarize_triplet_via_llm(frames=ordered, hint=hint)
            out["summary"] = {
                "ok": bool(summary.get("ok")),
                "model": summary.get("model"),
                "took_ms": summary.get("took_ms"),
            }
            out["cause"] = summary.get("cause")
            out["report"] = summary.get("report")
            out["key_facts"] = summary.get("key_facts")
            summary_ok = True

            artifacts = _job_artifacts_root(job_id)
            try:
                (artifacts / "summary.prompt.txt").write_text(
                    _build_triplet_summary_prompt(frames=ordered, hint=hint),
                    encoding="utf-8",
                )
            except Exception:
                pass
            try:
                (artifacts / "summary.response.txt").write_text(
                    str(summary.get("raw") or ""), encoding="utf-8"
                )
            except Exception:
                pass
        else:
            out["summary"] = {"ok": False, "error": "no successful frame analyses"}
    except Exception as e:
        out["summary"] = {"ok": False, "error": str(e)}
        errors.append(f"summary failed: {e}")

    # Stage 4: legal RAG (best-effort). Uses local KB + DeepSeek to produce qualitative analysis + references.
    try:
        q_terms = _law_query_terms(out=out)
        law_snips, meta = retrieve_law_snippets_resilient(q_terms, top_k=8)
        out["law_retrieval"] = {
            "ok": True,
            "query_terms": q_terms[:40],
            "hits": len(law_snips),
            "meta": meta,
            "snippets": law_snips,
        }

        law_prompt = _build_law_prompt(
            hint=hint,
            cause=(out.get("cause") if isinstance(out.get("cause"), str) else None),
            report=(out.get("report") if isinstance(out.get("report"), str) else None),
            key_facts=(
                out.get("key_facts") if isinstance(out.get("key_facts"), list) else None
            ),
            law_snippets=law_snips,
        )

        artifacts = _job_artifacts_root(job_id)
        try:
            (artifacts / "law.prompt.txt").write_text(law_prompt, encoding="utf-8")
        except Exception:
            pass

        try:
            law_out = _law_qualify_via_llm(prompt=law_prompt)
            refs_raw = law_out.get("law_refs")
            refs_list = refs_raw if isinstance(refs_raw, list) else []
            refs_valid = _validate_law_refs(law_refs=refs_list, law_snippets=law_snips)
            if not refs_valid:
                refs_valid = _fallback_law_refs(law_snippets=law_snips)
            legal_text = law_out.get("legal_qualitative")
            if not isinstance(legal_text, str) or not legal_text.strip():
                legal_text = _fallback_legal_qualitative(out=out)

            out["law"] = {
                "ok": True,
                "via": "llm",
                "model": law_out.get("model"),
                "took_ms": law_out.get("took_ms"),
            }
            out["legal_qualitative"] = str(legal_text).strip()
            out["law_refs"] = refs_valid

            try:
                (artifacts / "law.response.txt").write_text(
                    str(law_out.get("raw") or ""), encoding="utf-8"
                )
            except Exception:
                pass
        except Exception as e:
            # Fallback to ensure we still provide qualitative output + references.
            out["law"] = {"ok": True, "via": "fallback", "error": str(e)}
            out["legal_qualitative"] = _fallback_legal_qualitative(out=out)
            out["law_refs"] = _fallback_law_refs(law_snippets=law_snips)
    except Exception as e:
        out["law"] = {"ok": True, "via": "fallback", "error": str(e)}
        out["legal_qualitative"] = _fallback_legal_qualitative(out=out)
        out["law_refs"] = _fallback_law_refs(law_snippets=[])

    # Stage 5: store into DB (use t0 frame analysis + qualitative cause).
    accident_id: int | None = None
    # Preserve early beep result (t0 frame). After-store beeping is disabled.
    beep_attempted = early_beep_attempted
    beep_ok = early_beep_ok
    beep_error: str | None = early_beep_error
    try:
        # Prefer t0 frame as "current" incident photo.
        t0_frame = None
        for f in ordered:
            if isinstance(f, dict) and str(f.get("key") or "") == "t0":
                t0_frame = f
                break
        if t0_frame is None and ordered:
            t0_frame = ordered[-1] if isinstance(ordered[-1], dict) else None

        analysis = t0_frame.get("analysis") if isinstance(t0_frame, dict) else None
        if not isinstance(analysis, dict):
            raise RuntimeError("missing t0 analysis")

        # Map fields for DB.
        has_accident = bool(analysis.get("has_accident"))
        accident_type = str(analysis.get("accident_type") or "其他").strip() or "其他"
        severity = str(
            analysis.get("severity") or ("中等" if has_accident else "轻微")
        ).strip()
        if severity not in {"轻微", "中等", "严重"}:
            severity = "中等" if has_accident else "轻微"

        desc = str(analysis.get("description") or "").strip()
        if len(desc) > 5000:
            desc = desc[:5000]

        conf = clamp01(float(analysis.get("confidence") or 0.0))
        lat = _to_float_or_none(analysis.get("lat"))
        lng = _to_float_or_none(analysis.get("lng"))
        if lat is not None and (lat < -90.0 or lat > 90.0):
            lat = None
        if lng is not None and (lng < -180.0 or lng > 180.0):
            lng = None

        loc_conf = _to_float_or_none(analysis.get("location_confidence"))
        if loc_conf is not None:
            loc_conf = clamp01(loc_conf)

        image_path = None
        if isinstance(t0_frame, dict) and isinstance(t0_frame.get("image_path"), str):
            image_path = str(t0_frame.get("image_path"))
        if not image_path:
            image_path = j.image_path

        # Store minimal provenance: keep t0 raw_model_output, and include triplet job_id.
        raw_model_output = analysis.get("raw_model_output")
        if raw_model_output is not None and not isinstance(raw_model_output, str):
            raw_model_output = json.dumps(raw_model_output, ensure_ascii=False)
        if isinstance(raw_model_output, str) and len(raw_model_output) > 20000:
            raw_model_output = raw_model_output[:20000]
        if isinstance(raw_model_output, str) and raw_model_output.strip():
            raw_model_output = raw_model_output.strip() + f"\n\ntriplet_job_id={job_id}"
        else:
            raw_model_output = f"triplet_job_id={job_id}"

        cause = out.get("cause") if isinstance(out.get("cause"), str) else None
        if isinstance(cause, str):
            cause = cause.strip() or None
        legal_qualitative = (
            out.get("legal_qualitative")
            if isinstance(out.get("legal_qualitative"), str)
            else None
        )
        if isinstance(legal_qualitative, str):
            legal_qualitative = legal_qualitative.strip() or None
        law_refs = (
            out.get("law_refs") if isinstance(out.get("law_refs"), list) else None
        )
        law_refs_json = None
        if law_refs is not None:
            try:
                law_refs_json = json.dumps(law_refs, ensure_ascii=False)
                if len(law_refs_json) > 20000:
                    law_refs_json = law_refs_json[:20000]
            except Exception:
                law_refs_json = None

        a = Accident(
            created_at=now_bjt_naive(),
            source="http_ingest_triplet",
            image_path=image_path,
            hint=hint,
            has_accident=has_accident,
            accident_type=accident_type,
            severity=severity,
            description=desc,
            confidence=conf,
            location_text=(
                str(analysis.get("location_text")).strip()
                if isinstance(analysis.get("location_text"), str)
                and str(analysis.get("location_text")).strip()
                else None
            ),
            lat=lat,
            lng=lng,
            location_source=(
                str(analysis.get("location_source")).strip()
                if isinstance(analysis.get("location_source"), str)
                and str(analysis.get("location_source")).strip()
                else None
            ),
            location_confidence=loc_conf,
            raw_model_output=raw_model_output,
            cause=cause,
            legal_qualitative=legal_qualitative,
            law_refs_json=law_refs_json,
        )

        db = SessionLocal()
        try:
            db.add(a)
            db.commit()
            db.refresh(a)
            accident_id = int(a.id)
        finally:
            db.close()

        out["accident_id"] = accident_id
        store.update(
            job_id,
            accident_id=accident_id,
            beep_attempted=beep_attempted,
            beep_ok=beep_ok,
            beep_error=beep_error,
        )
    except Exception as e_db:
        # Do not fail the whole job if DB write fails; keep result for inspection.
        out["db"] = {"ok": False, "error": str(e_db)}

    store.update(
        job_id,
        status="failed"
        if any(isinstance(f, dict) and not bool(f.get("ok")) for f in ordered)
        else "done",
        finished_at=_now_iso(),
        result=out,
        error=("; ".join(errors)[:5000] if errors else None),
        beep_attempted=beep_attempted,
        beep_ok=beep_ok,
        beep_error=beep_error,
    )


@router.post("/ingest_triplet")
async def ingest_triplet(
    frame_t0: UploadFile = File(...),
    frame_t1: UploadFile = File(...),
    frame_t3: UploadFile = File(...),
    hint: str | None = Form(default=None),
    task: str = Form(default="rag"),
    extract_runs: int = Form(default=3),
) -> dict[str, Any]:
    """Ingest 3 ordered frames for a single incident.

    Expected fields:
    - frame_t0: photo at incident moment
    - frame_t1: photo 1 second before
    - frame_t3: photo 3 seconds before
    """

    # Save files sequentially for simplicity (stage 1). Analysis/concurrency is stage 2+.
    async def _save_one_async(file: UploadFile):
        suffix = _safe_image_suffix(file.filename)
        name = f"{uuid.uuid4().hex}{suffix}"
        base = uploads_dir()
        dst = (base / name).resolve()
        data = await file.read()
        dst.write_bytes(data)
        image_path = f"uploads/{name}"
        image_url = image_url_for_path(image_path) or f"/uploads/{name}"
        exif = try_extract_exif_gps(dst)

        stamp_ok = False
        stamp_lat = None
        stamp_lng = None
        stamp_text = None
        stamp_error = None
        try:
            st = stamp_inplace_hz(dst)
            if isinstance(st, dict) and bool(st.get("ok")):
                stamp_ok = True
                stamp_lat = st.get("lat")
                stamp_lng = st.get("lng")
                stamp_text = st.get("text")
        except Exception as e:
            stamp_ok = False
            stamp_error = str(e)

        stamp = {
            "ok": stamp_ok,
            "lat": stamp_lat,
            "lng": stamp_lng,
            "text": stamp_text,
            "error": stamp_error,
        }

        return {
            "image_path": image_path,
            "image_url": image_url,
            "saved_file": str(dst),
            "exif": exif,
            "stamp": stamp,
        }

    t3 = await _save_one_async(frame_t3)
    t1 = await _save_one_async(frame_t1)
    t0 = await _save_one_async(frame_t0)

    max_inflight_raw = os.getenv("SMART_TRANS_PIPELINE_MAX_INFLIGHT", "0").strip()
    try:
        max_inflight = int(max_inflight_raw)
    except Exception:
        max_inflight = 0
    if max_inflight > 0:
        active = _inflight_jobs_count(max_scan=max(max_inflight * 4, 200))
        if active >= max_inflight:
            raise HTTPException(
                status_code=429,
                detail=f"too many inflight jobs: {active} (limit={max_inflight})",
            )

    frames = [
        {"key": "t-3s", "order": 0, **t3},
        {"key": "t-1s", "order": 1, **t1},
        {"key": "t0", "order": 2, **t0},
    ]

    job_id = uuid.uuid4().hex
    j = Job(
        id=job_id,
        created_at=_now_iso(),
        status="queued",
        # Keep compatibility fields: use t0 as the representative image.
        image_path=str(t0.get("image_path")),
        saved_file=str(t0.get("saved_file")),
        hint=(hint.strip() if isinstance(hint, str) and hint.strip() else None),
        task=(task or "rag").strip().lower() or "rag",
        extract_runs=int(extract_runs),
        frames=frames,
    )
    store.put(j)

    runner.submit(lambda: _run_analyzer_triplet(job_id))

    return {
        "job_id": job_id,
        "status": j.status,
        "created_at": j.created_at,
        "frames": [
            {
                "key": f["key"],
                "image_path": f["image_path"],
                "image_url": f["image_url"],
            }
            for f in frames
        ],
    }

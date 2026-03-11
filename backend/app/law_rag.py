from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LawChunk:
    id: str
    source: str
    title: str
    text: str


_CACHE: dict[str, Any] = {
    "loaded_at": 0.0,
    "path": None,
    "mtime": None,
    "chunks": None,
}


def _repo_root() -> Path:
    # backend/app/law_rag.py -> backend/app -> backend -> repo root
    return Path(__file__).resolve().parents[2]


def _kb_path() -> Path:
    p = os.getenv("SMART_TRANS_LAW_KB", "").strip()
    if p:
        pp = Path(p)
        if not pp.is_absolute():
            pp = (_repo_root() / pp).resolve()
        return pp
    return (_repo_root() / "rag" / "law_kb.jsonl").resolve()


def load_law_kb() -> list[LawChunk]:
    path = _kb_path()
    if not path.is_file():
        return []

    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = None

    if _CACHE.get("path") == str(path) and _CACHE.get("mtime") == mtime and isinstance(_CACHE.get("chunks"), list):
        return _CACHE["chunks"]

    chunks: list[LawChunk] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                s = (line or "").strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    continue

                if not isinstance(obj, dict):
                    continue
                cid = str(obj.get("id") or "").strip()
                source = str(obj.get("source") or "").strip()
                title = str(obj.get("title") or "").strip()
                text = str(obj.get("text") or "").strip()
                if not cid or not text:
                    continue
                if len(text) > 4000:
                    text = text[:4000]
                chunks.append(LawChunk(id=cid, source=source, title=title or "(untitled)", text=text))
    except Exception:
        chunks = []

    _CACHE["loaded_at"] = time.time()
    _CACHE["path"] = str(path)
    _CACHE["mtime"] = mtime
    _CACHE["chunks"] = chunks
    return chunks


def _clean_terms(terms: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        if not t:
            continue
        s = str(t).strip()
        if not s:
            continue
        s = s.replace("\u3000", " ")
        s = " ".join(s.split())
        if not s:
            continue
        # Filter overly-short noise. Keep 2+ chars, or common numeric patterns.
        if len(s) < 2 and not any(ch.isdigit() for ch in s):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= 64:
            break
    return out


def retrieve_law_snippets(query_terms: list[str], *, top_k: int = 6) -> list[dict[str, Any]]:
    chunks = load_law_kb()
    terms = _clean_terms(query_terms)
    if not chunks or not terms:
        return []

    scored: list[tuple[int, LawChunk]] = []
    for ch in chunks:
        hay = (ch.title + "\n" + ch.text).lower()
        s = 0
        for t in terms:
            tl = t.lower()
            if not tl:
                continue
            s += hay.count(tl)
        if s > 0:
            scored.append((s, ch))

    scored.sort(key=lambda x: (-x[0], x[1].source, x[1].title))
    out: list[dict[str, Any]] = []
    for score, ch in scored[: max(0, int(top_k))]:
        txt = ch.text
        if len(txt) > 700:
            txt = txt[:700]
        title = ch.title
        if len(title) > 200:
            title = title[:200]
        out.append(
            {
                "id": ch.id,
                "source": ch.source,
                "title": title,
                "score": int(score),
                "snippet": txt,
            }
        )
    return out


def retrieve_law_snippets_resilient(query_terms: list[str], *, top_k: int = 8) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retrieve with fallback to keep downstream legal analysis robust."""

    snips = retrieve_law_snippets(query_terms, top_k=int(top_k))
    if snips:
        return snips, {"fallback": False, "reason": None}

    fallback_terms = [
        "道路交通安全法",
        "道路通行",
        "交通事故",
        "交叉路口",
        "安全距离",
        "停车",
        "临时停车",
        "让行",
        "人行横道",
        "信号灯",
        "变更车道",
        "超车",
        "高速公路",
        "故障",
        "危险报警闪光灯",
    ]
    snips = retrieve_law_snippets(fallback_terms, top_k=min(int(top_k), 6))
    if snips:
        return snips, {"fallback": True, "reason": "no_hit_use_generic_terms"}

    # Final fallback: take the first few chunks (sorted) as generic references.
    chunks = load_law_kb()
    out: list[dict[str, Any]] = []
    for ch in chunks[: min(4, max(1, int(top_k)) )]:
        txt = ch.text
        if len(txt) > 700:
            txt = txt[:700]
        title = ch.title
        if len(title) > 200:
            title = title[:200]
        out.append({"id": ch.id, "source": ch.source, "title": title, "score": 0, "snippet": txt})
    return out, {"fallback": True, "reason": "no_hit_use_first_chunks"}

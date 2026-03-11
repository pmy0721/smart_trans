#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ARTICLE_RE = re.compile(r"(?m)^(第[一二三四五六七八九十百千0-9]+条)\s*")
CHAPTER_RE = re.compile(r"(?m)^(第[一二三四五六七八九十百千0-9]+章)\s*(.*)$")
SECTION_RE = re.compile(r"(?m)^(第[一二三四五六七八九十百千0-9]+节)\s*(.*)$")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _norm_text(s: str) -> str:
    # Keep Chinese punctuation; collapse repeated whitespace.
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\u00a0", " ").replace("\u3000", " ")
    # Drop invalid surrogate code points from some PDF extractors.
    try:
        s = s.encode("utf-8", "ignore").decode("utf-8", "ignore")
    except Exception:
        pass
    s = s.replace("\x00", "")
    # Remove page header artifacts like "－12－".
    s = re.sub(r"(?m)^\s*[-－—]{1,3}\s*\d+\s*[-－—]{1,3}\s*$", "", s)
    # Collapse excessive blank lines.
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as e:
        raise RuntimeError("missing dependency pypdf") from e

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        if txt:
            parts.append(txt)
    return _norm_text("\n".join(parts))


def _extract_docx_text(path: Path) -> str:
    try:
        import docx  # type: ignore
    except Exception as e:
        raise RuntimeError("missing dependency python-docx") from e

    d = docx.Document(str(path))
    lines: list[str] = []
    for p in d.paragraphs:
        t = (p.text or "").strip()
        if t:
            lines.append(t)
    return _norm_text("\n".join(lines))


def _run_cmd_capture(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout or ""
    return out if out.strip() else None


def _extract_doc_text(path: Path) -> str:
    # Best-effort. Old .doc is tricky without external tools.
    # Try antiword (linux/mac). If missing, try macOS textutil.
    out = _run_cmd_capture(["antiword", str(path)])
    if out:
        return _norm_text(out)

    out = _run_cmd_capture(["textutil", "-convert", "txt", "-stdout", str(path)])
    if out:
        return _norm_text(out)

    raise RuntimeError("cannot extract .doc text (install antiword or provide pdf/docx)")


def _extract_any(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return _extract_pdf_text(path)
    if suf == ".docx":
        return _extract_docx_text(path)
    if suf == ".doc":
        return _extract_doc_text(path)
    raise RuntimeError(f"unsupported file type: {suf}")


def _find_last_heading(text: str, pos: int) -> str:
    # Scan from start to pos for most recent chapter/section line.
    head = ""
    for m in CHAPTER_RE.finditer(text[:pos]):
        cap = m.group(1)
        rest = (m.group(2) or "").strip()
        head = (cap + (" " + rest if rest else "")).strip()
    for m in SECTION_RE.finditer(text[:pos]):
        cap = m.group(1)
        rest = (m.group(2) or "").strip()
        sec = (cap + (" " + rest if rest else "")).strip()
        head = (head + " / " + sec) if head else sec
    return head


def _chunk_by_articles(text: str, *, source: str, max_len: int) -> list[dict[str, Any]]:
    matches = list(ARTICLE_RE.finditer(text))
    out: list[dict[str, Any]] = []
    if not matches:
        # Fallback: chunk by paragraphs/blank lines.
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        buf: list[str] = []
        cur = ""
        for p in paras:
            if len(cur) + len(p) + 2 > max_len and cur:
                body = cur.strip()
                cid = _sha(source + "\n" + body)
                out.append({"id": cid, "source": source, "title": "(chunk)", "text": body})
                buf = []
                cur = ""
            buf.append(p)
            cur = ("\n\n".join(buf)).strip()
        if cur.strip():
            body = cur.strip()
            cid = _sha(source + "\n" + body)
            out.append({"id": cid, "source": source, "title": "(chunk)", "text": body})
        return out

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        article = m.group(1).strip()
        heading = _find_last_heading(text, start)
        title = f"{heading} {article}".strip() if heading else article
        body = text[start:end].strip()
        if not body:
            continue
        if len(body) > max_len:
            body = body[:max_len]
        cid = _sha(source + "\n" + title + "\n" + body)
        out.append({"id": cid, "source": source, "title": title, "text": body})
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Build law knowledge base for smart_trans (jsonl chunks).")
    ap.add_argument("--src", default="rag/trans_doc", help="Source directory containing law documents.")
    ap.add_argument("--out", default="rag/law_kb.jsonl", help="Output jsonl path.")
    ap.add_argument("--max-len", type=int, default=1800, help="Max chars per chunk.")
    ap.add_argument("--include", default=".pdf,.docx,.doc", help="Comma-separated suffixes to include.")
    args = ap.parse_args(argv)

    src = Path(args.src)
    if not src.is_dir():
        raise SystemExit(f"error: not a directory: {src}")

    outp = Path(args.out)
    if not outp.is_absolute():
        outp = (Path.cwd() / outp).resolve()
    outp.parent.mkdir(parents=True, exist_ok=True)

    allowed = {s.strip().lower() for s in str(args.include).split(",") if s.strip()}
    files = [p for p in sorted(src.iterdir()) if p.is_file() and p.suffix.lower() in allowed]
    if not files:
        raise SystemExit("error: no input files")

    chunks: list[dict[str, Any]] = []
    skipped: list[str] = []
    for p in files:
        try:
            txt = _extract_any(p)
            if not txt:
                skipped.append(f"{p.name}: empty")
                continue
            chunks.extend(_chunk_by_articles(txt, source=p.name, max_len=int(args.max_len)))
        except Exception as e:
            skipped.append(f"{p.name}: {e}")
            continue

    # Deduplicate by id.
    uniq: dict[str, dict[str, Any]] = {}
    for ch in chunks:
        cid = str(ch.get("id") or "")
        if not cid:
            continue
        uniq[cid] = ch

    items = list(uniq.values())
    items.sort(key=lambda x: (str(x.get("source") or ""), str(x.get("title") or "")))

    with outp.open("w", encoding="utf-8") as f:
        for ch in items:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")

    print(f"ok: chunks={len(items)} out={outp}")
    if skipped:
        print("skipped:")
        for s in skipped:
            print(" -", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

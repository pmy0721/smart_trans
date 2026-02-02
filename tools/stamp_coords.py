#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


ANCHORS = [
    # Mainland
    (39.904200, 116.407400),  # Beijing
    (31.230400, 121.473700),  # Shanghai
    (23.129100, 113.264400),  # Guangzhou
    (22.543100, 114.057900),  # Shenzhen
    (30.572800, 104.066800),  # Chengdu
    (29.563000, 106.551600),  # Chongqing
    (30.274100, 120.155100),  # Hangzhou
    (32.060300, 118.796900),  # Nanjing
    (27.994900, 120.699400),  # Wenzhou
    (34.341600, 108.939800),  # Xi'an
    (41.805700, 123.431500),  # Shenyang
    (45.803800, 126.534900),  # Harbin
    (36.067100, 120.382600),  # Qingdao
    (37.870600, 112.548900),  # Taiyuan
    (43.825600, 87.616800),  # Urumqi
    (29.652000, 91.172100),  # Lhasa
    (25.045300, 102.709700),  # Kunming
    (26.074500, 119.296500),  # Fuzhou
    (24.479800, 118.089400),  # Xiamen
    (22.816700, 108.366900),  # Nanning
    (20.044000, 110.198300),  # Haikou
    (18.252800, 109.511900),  # Sanya
    # Hong Kong / Macau / Taiwan
    (22.319300, 114.169400),  # Hong Kong
    (22.198700, 113.543900),  # Macau
    (25.033000, 121.565400),  # Taipei
    (22.627300, 120.301400),  # Kaohsiung
    (24.147700, 120.673600),  # Taichung
]


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}


def _seed_for_file(global_seed: int, name: str) -> int:
    h = hashlib.sha256(f"{global_seed}:{name}".encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _pick_coord(rng: random.Random, jitter_lat: float, jitter_lng: float) -> tuple[float, float]:
    base_lat, base_lng = rng.choice(ANCHORS)
    lat = base_lat + rng.uniform(-jitter_lat, jitter_lat)
    lng = base_lng + rng.uniform(-jitter_lng, jitter_lng)
    lat = max(-90.0, min(90.0, lat))
    lng = max(-180.0, min(180.0, lng))
    return lat, lng


def _load_font(size: int) -> Any:
    for fp in [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/Library/Fonts/Arial.ttf",
    ]:
        try:
            if os.path.isfile(fp):
                return ImageFont.truetype(fp, size)
        except Exception:
            pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _draw_stamp(img: Image.Image, text: str) -> Image.Image:
    w, h = img.size
    font_size = int(max(14, min(40, round(w * 0.03))))
    font = _load_font(font_size)

    pad_x = int(max(10, round(w * 0.02)))
    pad_y = int(max(10, round(h * 0.02)))
    inner = int(max(6, round(font_size * 0.4)))

    base = ImageOps.exif_transpose(img)
    if base is None:
        raise RuntimeError("failed to load image")
    if base.mode != "RGBA":
        base = base.convert("RGBA")

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    x1 = w - pad_x - (tw + inner * 2)
    y1 = pad_y
    x2 = w - pad_x
    y2 = pad_y + (th + inner * 2)

    r = int(max(8, round(font_size * 0.6)))
    try:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=r, fill=(0, 0, 0, 145), outline=(255, 255, 255, 60))
    except Exception:
        draw.rectangle((x1, y1, x2, y2), fill=(0, 0, 0, 145), outline=(255, 255, 255, 60))

    tx = x1 + inner
    ty = y1 + inner
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 235))

    out = Image.alpha_composite(base, overlay)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Stamp virtual lat/lng text onto images (top-right).")
    ap.add_argument("paths", nargs="*", help="Optional image paths to process (in-place by default).")
    ap.add_argument("--dir", default="input_image", help="Image directory to process.")
    ap.add_argument("--seed", type=int, default=42, help="Global seed (deterministic per file).")
    ap.add_argument("--suffix", default="_stamped", help="Suffix before extension for output filename.")
    ap.add_argument("--jitter-lat", type=float, default=0.05, help="Latitude jitter degrees around anchor.")
    ap.add_argument("--jitter-lng", type=float, default=0.05, help="Longitude jitter degrees around anchor.")
    ap.add_argument("--inplace", action="store_true", help="Overwrite input files in-place.")
    ap.add_argument("--no-inplace", action="store_true", help="Do not overwrite; write to a suffixed output file.")
    ap.add_argument("--backup", action="store_true", help="When overwriting, save a .bak backup next to the input.")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing stamped outputs.")
    ap.add_argument("--write-map", action="store_true", help="Write coords map json next to outputs.")
    args = ap.parse_args(argv)

    if args.inplace and args.no_inplace:
        raise SystemExit("error: cannot use --inplace and --no-inplace together")

    paths: list[Path]
    dir_mode = len(args.paths) == 0

    if dir_mode:
        d = Path(args.dir)
        if not d.is_dir():
            raise SystemExit(f"error: not a directory: {d}")
        paths = [p for p in sorted(d.iterdir()) if p.is_file() and _is_image(p)]
    else:
        paths = [Path(p).expanduser() for p in args.paths]
        for p in paths:
            if not p.is_file():
                raise SystemExit(f"error: file not found: {p}")
            if not _is_image(p):
                raise SystemExit(f"error: not a supported image type: {p}")

    inplace = bool(args.inplace)
    if not dir_mode and not args.no_inplace and not args.inplace:
        inplace = True
    if args.no_inplace:
        inplace = False

    outputs: dict[str, Any] = {}
    processed = 0
    skipped = 0
    failed = 0

    for p in paths:
        out = p
        if not inplace:
            if p.stem.endswith(args.suffix):
                skipped += 1
                continue
            out = p.with_name(f"{p.stem}{args.suffix}{p.suffix}")
            if out.exists() and not args.overwrite:
                skipped += 1
                continue

        rng = random.Random(_seed_for_file(int(args.seed), p.name))
        lat, lng = _pick_coord(rng, float(args.jitter_lat), float(args.jitter_lng))
        text = f"Lat: {lat:.6f}, Lng: {lng:.6f}"

        try:
            if inplace and args.backup:
                bak = p.with_name(p.name + ".bak")
                if not bak.exists() or args.overwrite:
                    shutil.copy2(p, bak)

            with Image.open(p) as img:
                stamped = _draw_stamp(img, text)
                if out.suffix.lower() in {".jpg", ".jpeg"}:
                    stamped = stamped.convert("RGB")
                    stamped.save(out, quality=92, optimize=True)
                else:
                    stamped.save(out)
            outputs[out.name] = {"src": p.name, "lat": lat, "lng": lng, "text": text, "inplace": inplace}
            processed += 1
        except Exception as e:
            outputs[p.name] = {"error": str(e)}
            failed += 1

    if args.write_map:
        if dir_mode:
            mp = Path(args.dir) / f"coords{args.suffix}.json"
        else:
            base = paths[0].parent
            name = "coords_inplace.json" if inplace else f"coords{args.suffix}.json"
            mp = base / name
        mp.write_text(json.dumps(outputs, ensure_ascii=True, indent=2), encoding="utf-8")

    if dir_mode:
        print(f"done: processed={processed} skipped={skipped} failed={failed} dir={Path(args.dir)}")
    else:
        print(f"done: processed={processed} skipped={skipped} failed={failed} inplace={inplace}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

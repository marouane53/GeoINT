"""Street-level imagery from any provider: one manifest format, one renderer (used by pano.py and sweep.py).

A manifest is JSON Lines (or a JSON list), one panorama per entry:

  {"provider": "citysite", "id": "001234", "lat": 34.03412, "lon": -5.00051, "heading": 87.0,
   "date": "2014-09-12", "link": "https://example-city-360.org/#s=001234",
   "low":  {"kind": "cube-strip", "url": "https://…/001234/preview.jpg", "order": "lfrbud"},
   "high": {"kind": "cube", "faces": {"f": "https://…/mobile_f.jpg", "r": "…", "b": "…", "l": "…", "u": "…", "d": "…"}},
   "meta": {"road": "Avenue Hassan II"}}

"low" is what a city-wide sweep downloads (small and fast); "high" is used for sheets and verification. Either may
be missing (the other is used). Image kinds:

  equirect     {"url" | "path", "center": 143.0}   360x180 panorama; "center" = compass bearing of the image's middle
                                                   column (defaults to the entry's "heading")
  cube         {"faces": {f, r, b, l[, u, d]: url}} cube faces seen from inside, upright, f looks along "heading",
                                                   r = heading+90 (krpano / Pannellum / Marzipano convention)
  cube-strip   {"url", "order": "lfrbud", "axis": "vertical"|"horizontal"}  all faces in one image, square cells
  photo        {"url", "hfov": 70}                 an ordinary photo looking along "heading" (only that view exists)
  streetlevel  {"service": "google"|"apple"|"bing"|"yandex"|"naver"|"kakao"|"mapy"|"ja", "pano": id, "zoom": n}
               fetched through the `streetlevel` package (no API keys)

Compass bearings everywhere: 0 = north, 90 = east. Downloads are cached under $GEOINT_CACHE/panos (default
~/.cache/geoint/panos) so a city indexed once is reused by every later photo.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

CACHE = Path(os.environ.get("GEOINT_CACHE", Path.home() / ".cache" / "geoint")).expanduser() / "panos"
UA = "geoint/1.0 (photo geolocation research)"


# ---------------------------------------------------------------- manifest

def read_manifest(*paths) -> list[dict]:
    """Entries from one or more manifests (.jsonl or .json list); later duplicates (same provider+id) are dropped."""
    out, seen = [], set()
    for p in paths:
        text = Path(p).read_text(encoding="utf-8").strip()
        rows = json.loads(text) if text.startswith("[") else [json.loads(x) for x in text.splitlines() if x.strip()]
        for r in rows:
            key = (r.get("provider", ""), str(r["id"]))
            if key not in seen:
                seen.add(key)
                out.append(r)
    return out


def write_manifest(path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def key(entry: dict) -> str:
    return f"{entry.get('provider', 'x')}:{entry['id']}"


# ---------------------------------------------------------------- downloads

def _cache_path(url: str, sub: str = "url") -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()
    ext = os.path.splitext(url.split("?")[0])[1][:6] or ".img"
    return CACHE / sub / h[:2] / f"{h}{ext}"


def fetch_many(urls: list[str], parallel: int = 12, timeout: int = 40) -> dict[str, Path]:
    """Download URLs into the cache with curl --parallel (fast for thousands of small files). Returns {url: path}
    for the ones that exist afterwards (failures are simply missing)."""
    todo = [u for u in dict.fromkeys(urls) if not (_cache_path(u).exists() and _cache_path(u).stat().st_size > 0)]
    if todo:
        cfg = CACHE / "url" / f"batch_{os.getpid()}.curl"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for u in todo:
            p = _cache_path(u)
            p.parent.mkdir(parents=True, exist_ok=True)
            lines.append(f'url = "{u}"\noutput = "{p.as_posix()}"\n')  # backslashes escape inside curl -K
        cfg.write_text("".join(lines))
        subprocess.run(["curl", "-q", "-s", "-L", "--fail", "--retry", "2", "--max-time", str(timeout), "-A", UA,
                        "--parallel", "--parallel-max", str(parallel), "-K", str(cfg)], check=False)
        cfg.unlink(missing_ok=True)
        for u in todo:                       # an HTML error or redirect page is not an image: drop it
            p = _cache_path(u)
            if p.exists() and not _is_image(p):
                p.unlink()
    return {u: _cache_path(u) for u in urls if _cache_path(u).exists() and _cache_path(u).stat().st_size > 0}


def _is_image(p: Path) -> bool:
    with open(p, "rb") as f:
        head = f.read(12)
    return (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:4] == b"RIFF"
            or head[4:8] == b"ftyp" or head[:2] in (b"BM", b"II", b"MM"))


def fetch(url: str) -> Path | None:
    return fetch_many([url], parallel=1).get(url)


def _open(src: str) -> Image.Image:
    if src.startswith(("http://", "https://")):
        p = fetch(src)
        if p is None:
            raise RuntimeError(f"download failed: {src}")
        src = str(p)
    im = Image.open(src)
    im.load()
    return im


# ---------------------------------------------------------------- rendering

def _rays(bearing_rel: float, pitch: float, hfov: float, w: int, h: int, roll: float = 0.0):
    """Unit-free ray directions (x right, y up, z forward) for a pinhole view, yawed by bearing_rel degrees."""
    f = (w / 2) / math.tan(math.radians(hfov) / 2)
    u, v = np.meshgrid(np.arange(w) - (w - 1) / 2, np.arange(h) - (h - 1) / 2)
    x, y, z = u.astype(np.float64), -v.astype(np.float64), np.full(u.shape, f)
    if roll:
        cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
        x, y = x * cr - y * sr, x * sr + y * cr
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    y2, z2 = y * cp + z * sp, -y * sp + z * cp
    cy, sy = math.cos(math.radians(bearing_rel)), math.sin(math.radians(bearing_rel))
    return x * cy + z2 * sy, y2, -x * sy + z2 * cy


def _bilinear(img: np.ndarray, px: np.ndarray, py: np.ndarray, wrap_x: bool = False) -> np.ndarray:
    H, W = img.shape[:2]
    x0 = np.floor(px).astype(int)
    y0 = np.floor(py).astype(int)
    fx = (px - x0)[..., None]
    fy = (py - y0)[..., None]
    if wrap_x:
        x0w, x1w = x0 % W, (x0 + 1) % W
    else:
        x0w, x1w = np.clip(x0, 0, W - 1), np.clip(x0 + 1, 0, W - 1)
    y0c, y1c = np.clip(y0, 0, H - 1), np.clip(y0 + 1, 0, H - 1)
    return (img[y0c, x0w] * (1 - fx) * (1 - fy) + img[y0c, x1w] * fx * (1 - fy)
            + img[y1c, x0w] * (1 - fx) * fy + img[y1c, x1w] * fx * fy)


def equirect_view(eq: np.ndarray, center: float, bearing: float, pitch: float = 0.0, hfov: float = 90.0,
                  w: int = 1024, h: int = 768) -> Image.Image:
    """Pinhole view from an equirectangular panorama whose middle column looks at compass bearing `center`."""
    H, W = eq.shape[:2]
    dx, dy, dz = _rays(bearing - center, pitch, hfov, w, h)
    lon = np.arctan2(dx, dz)
    lat = np.arctan2(dy, np.hypot(dx, dz))
    px = (lon / (2 * math.pi) + 0.5) * W - 0.5
    py = (0.5 - lat / math.pi) * H - 0.5
    return Image.fromarray(np.clip(_bilinear(eq, px, py, wrap_x=True), 0, 255).astype(np.uint8))


def cube_view(faces: dict, heading: float, bearing: float, pitch: float = 0.0, hfov: float = 90.0,
              w: int = 1024, h: int = 768) -> Image.Image:
    """Pinhole view from cube faces (f looks along `heading`, r = heading+90). Missing u/d faces render grey."""
    dx, dy, dz = _rays(bearing - heading, pitch, hfov, w, h)
    out = np.full(dx.shape + (3,), 110.0)
    ax, ay, az = np.abs(dx), np.abs(dy), np.abs(dz)
    side = (ax >= ay) | (az >= ay)
    sel = {"f": side & (az >= ax) & (dz > 0), "b": side & (az >= ax) & (dz <= 0),
           "r": side & (ax > az) & (dx > 0), "l": side & (ax > az) & (dx <= 0),
           "u": ~side & (dy > 0), "d": ~side & (dy <= 0)}
    for f, m in sel.items():
        if f not in faces or not m.any():
            continue
        x, y, z = dx[m], dy[m], dz[m]
        if f == "f":
            u, v = x / z, -y / z
        elif f == "b":
            u, v = x / z, y / z          # = -x/|z|, -y/|z|
        elif f == "r":
            u, v = -z / x, -y / x
        elif f == "l":
            u, v = -z / x, y / x         # = z/|x|, -y/|x|
        elif f == "u":
            u, v = x / y, z / y
        else:
            u, v = -x / y, z / y         # = x/|y|, -z/|y|
        img = faces[f]
        N = img.shape[0]
        out[m] = _bilinear(img, (u + 1) / 2 * N - 0.5, (v + 1) / 2 * N - 0.5)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def strip_to_faces(im: Image.Image, order: str = "lfrbud", axis: str = "vertical") -> dict:
    a = np.asarray(im.convert("RGB"))
    n = a.shape[1] if axis == "vertical" else a.shape[0]
    return {k: (a[i * n:(i + 1) * n] if axis == "vertical" else a[:, i * n:(i + 1) * n]) for i, k in enumerate(order)}


class View:
    """A loaded panorama level that can render any direction: kind 'equirect' | 'cube' | 'photo'."""

    def __init__(self, kind: str, data, heading: float, hfov: float | None = None):
        self.kind, self.data, self.heading, self.hfov = kind, data, heading, hfov

    def render(self, bearing: float, pitch: float = 0.0, hfov: float = 90.0, w: int = 1024, h: int = 768) -> Image.Image:
        if self.kind == "equirect":
            return equirect_view(self.data, self.heading, bearing, pitch, hfov, w, h)
        if self.kind == "cube":
            return cube_view(self.data, self.heading, bearing, pitch, hfov, w, h)
        im = Image.fromarray(self.data)        # photo: the only view there is
        return im.resize((w, int(w * im.height / im.width))) if abs(im.width / im.height - w / h) > 0.15 else im.resize((w, h))

    def bearings(self, n: int) -> list[float]:
        """n evenly spaced compass bearings for a sweep (a photo has just its own)."""
        if self.kind == "photo":
            return [self.heading % 360]
        return [round((self.heading + k * 360.0 / n) % 360, 1) for k in range(n)]


def load(entry: dict, level: str = "low") -> View:
    """Load one level ("low" or "high") of a manifest entry; falls back to the other level when missing."""
    spec = entry.get(level) or entry.get("high" if level == "low" else "low") or entry.get("image")
    if not spec:
        raise ValueError(f"{key(entry)}: no image spec")
    heading = float(entry.get("heading") or 0.0)
    kind = spec["kind"]
    if kind == "equirect":
        im = _open(spec.get("url") or spec["path"]).convert("RGB")
        return View("equirect", np.asarray(im, dtype=np.float32), float(spec.get("center", heading)))
    if kind == "cube":
        urls = {k: v for k, v in spec["faces"].items()}
        got = fetch_many([u for u in urls.values() if u.startswith("http")])
        faces = {}
        for k, u in urls.items():
            src = got.get(u, u)
            if Path(str(src)).exists():
                faces[k] = np.asarray(Image.open(src).convert("RGB"), dtype=np.float32)
        if not all(k in faces for k in "fblr"):
            raise RuntimeError(f"{key(entry)}: missing cube faces")
        return View("cube", faces, heading)
    if kind == "cube-strip":
        im = _open(spec.get("url") or spec["path"])
        faces = {k: v.astype(np.float32) for k, v in strip_to_faces(im, spec.get("order", "lfrbud"), spec.get("axis", "vertical")).items()}
        return View("cube", faces, heading)
    if kind == "photo":
        im = _open(spec.get("url") or spec["path"]).convert("RGB")
        return View("photo", np.asarray(im), heading, spec.get("hfov"))
    if kind == "streetlevel":
        return _streetlevel_view(entry, spec)
    raise ValueError(f"unknown image kind {kind!r}")


# ---------------------------------------------------------------- providers through the streetlevel package

def _sl(service: str):
    import _streetlevel as SL
    SL.streetview()                     # installs the pyexiv2 stub before any streetlevel submodule imports it
    import importlib
    return importlib.import_module(f"streetlevel.{service}")


def _cached_equirect(tag: str, make) -> np.ndarray:
    p = CACHE / "sl" / f"{hashlib.sha1(tag.encode()).hexdigest()}.jpg"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        im = make().convert("RGB")
        im.save(p, quality=90)
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float32)


def _streetlevel_view(entry: dict, spec: dict) -> View:
    svc, pid, zoom = spec["service"], spec["pano"], spec.get("zoom")
    heading = float(entry.get("heading") or 0.0)
    if svc == "google":
        import _streetlevel as SL
        eq, center = SL.equirect(pid, CACHE / "google", int(zoom if zoom is not None else 2))
        return View("equirect", eq.astype(np.float32), center)
    if svc == "apple":
        return View("equirect", *_apple_equirect(entry, spec))
    mod = _sl({"bing": "streetside"}.get(svc, svc))
    finders = {"bing": lambda: mod.find_panorama_by_id(int(pid)), "yandex": lambda: mod.find_panorama_by_id(pid),
               "naver": lambda: mod.find_panorama_by_id(pid), "kakao": lambda: mod.find_panorama_by_id(int(pid)),
               "mapy": lambda: mod.find_panorama_by_id(int(pid)), "ja": lambda: mod.find_panorama_by_id(int(pid))}
    if svc not in finders:
        raise ValueError(f"unknown streetlevel service {svc!r}")
    z = int(zoom if zoom is not None else {"bing": 2, "yandex": 2, "naver": 1, "kakao": 1, "mapy": 1, "ja": 1}[svc])
    if svc in ("bing", "naver", "ja"):           # cubemaps: faces in the order front, right, back, left, top, bottom
        def make():
            from streetlevel.util import CubemapStitchingMethod
            faces = mod.get_panorama(finders[svc](), zoom=z, stitching_method=CubemapStitchingMethod.NONE)
            side = faces[0].width
            row = Image.new("RGB", (side * 6, side))
            for i, fc in enumerate(faces[:6]):
                row.paste(fc.convert("RGB").resize((side, side)), (i * side, 0))
            return row
        strip = _cached_equirect(f"{svc}:{pid}:{z}:cube", make)
        faces = strip_to_faces(Image.fromarray(strip.astype(np.uint8)), "frblud", "horizontal")
        return View("cube", {k: v.astype(np.float32) for k, v in faces.items()}, float(spec.get("center", heading)))
    eq = _cached_equirect(f"{svc}:{pid}:{z}", lambda: mod.get_panorama(finders[svc](), zoom=z))
    return View("equirect", eq, float(spec.get("center", heading)))


def _apple_equirect(entry: dict, spec: dict) -> tuple[np.ndarray, float]:
    """Look Around: the four side faces are equirectangular strips; paste them into one panorama (no top/bottom).
    Needs pillow-heif for the HEIC faces."""
    la = _sl("lookaround")
    zoom = int(spec.get("zoom", 4))
    tag = f"apple:{spec['pano']}:{spec.get('build')}:{zoom}"
    meta_p = CACHE / "sl" / f"{hashlib.sha1(tag.encode()).hexdigest()}.json"

    def make():
        import pillow_heif
        pillow_heif.register_heif_opener()
        tile = la.get_coverage_tile_by_latlon(entry["lat"], entry["lon"])
        pano = next((p for p in tile.panos if str(p.id) == str(spec["pano"])), None)
        if pano is None:
            raise RuntimeError(f"Look Around panorama {spec['pano']} not found on its coverage tile")
        auth = la.Authenticator()
        faces = [Image.open(io.BytesIO(la.get_panorama_face(pano, i, zoom, auth))).convert("RGB") for i in range(4)]
        full_h = round(faces[0].height * math.pi / pano.camera_metadata[0].lens_projection.fov_h)
        full_w = 2 * full_h
        eq = Image.new("RGB", (full_w, full_h), (110, 110, 110))
        for i in range(3, -1, -1):
            cm = pano.camera_metadata[i]
            phi0 = math.pi + cm.position.yaw - cm.lens_projection.fov_s / 2
            phi0 %= 2 * math.pi
            th0 = math.pi / 2 - cm.lens_projection.fov_h / 2 - cm.lens_projection.cy
            fw, fh = cm.lens_projection.fov_s * full_h / math.pi, cm.lens_projection.fov_h * full_h / math.pi
            sc = faces[i].resize((math.ceil(fw), math.ceil(fh)))
            x, y = math.ceil(phi0 * full_h / math.pi), math.ceil(th0 * full_h / math.pi)
            eq.paste(sc, (x, y))
            if x + sc.width > full_w:
                eq.paste(sc, (x - full_w, y))
        meta_p.write_text(json.dumps({"heading_rad": pano.heading}))
        return eq

    eq = _cached_equirect(tag, make)
    h_rad = json.loads(meta_p.read_text())["heading_rad"] if meta_p.exists() else math.radians(-(entry.get("heading") or 0))
    # Look Around headings run counter-clockwise from north; the stitched image's middle looks backwards
    center = (-math.degrees(h_rad) + 180.0) % 360
    return eq, float(spec.get("center", center))

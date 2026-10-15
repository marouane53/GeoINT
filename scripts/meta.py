#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow", "pillow-heif", "tzdata"]
# ///
"""Deep metadata and file forensics for one photo or video (uses exiftool; falls back to Pillow).

Finds what plain EXIF readers miss:
  GPS from EXIF, XMP, QuickTime/Android video atoms and DJI drone XMP (incl. gimbal/flight heading, altitude)
  capture time with its UTC offset — from OffsetTime*, or derived from GPS UTC time vs local time —
  and every country whose time zones had that offset on that date (DST-aware)
  camera/maker-note location hints (camera "home/destination city" time-zone settings, IPTC/XMP city and country)
  editing and provenance (software, edit history, C2PA / content credentials, AI-generated source type)
  embedded thumbnails/previews that differ from the main image (the uncropped original may be inside)
  filename patterns (WhatsApp, WeChat, KakaoTalk, LINE, Telegram, Viber, stock-photo IDs, screenshots
  and the OS language of the screenshot name) and JPEG recompression hints

  meta.py photo.jpg --out-dir meta/        → meta/meta.json, meta/meta.md, meta/thumb_*.jpg
  meta.py photo.jpg                        → print the summary

Metadata is evidence about the file, not proof about the scene: it can be stripped, edited or carried
over from another photo. When it conflicts with the image, the image wins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

ZONE_TAB = [Path("/usr/share/zoneinfo/zone1970.tab"), Path("/usr/share/zoneinfo/zone.tab")]
try:  # Windows has no /usr/share/zoneinfo: fall back to the tzdata package
    import tzdata
    ZONE_TAB += [Path(tzdata.__file__).parent / "zoneinfo" / n for n in ("zone1970.tab", "zone.tab")]
except ImportError:
    pass

FILENAME_PATTERNS = [
    (r"^IMG-\d{8}-WA\d+", "WhatsApp (Android) — recompressed, metadata stripped; date = received/sent date"),
    (r"^VID-\d{8}-WA\d+", "WhatsApp video (Android)"),
    (r"^WhatsApp (Image|Video) \d{4}-\d{2}-\d{2} at ", "WhatsApp desktop/web export"),
    (r"^mmexport(\d{13})", "WeChat export (mainly China users); the number is the export time in ms"),
    (r"^微信图片_\d{14}", "WeChat for Windows (China)"),
    (r"^wx_camera_(\d{13})", "WeChat in-app camera (China)"),
    (r"^KakaoTalk_\d{8}_\d+", "KakaoTalk (South Korea)"),
    (r"^(LINE_ALBUM_|LINE_P\d|line_\d+)", "LINE (Japan, Taiwan, Thailand)"),
    (r"^viber_image_\d{4}-\d{2}-\d{2}", "Viber (popular in Eastern Europe, the Balkans, the Philippines)"),
    (r"^photo_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", "Telegram desktop export"),
    (r"^signal-\d{4}-\d{2}-\d{2}", "Signal"),
    (r"^FB_IMG_(\d{13})", "Facebook app save; number = save time in ms"),
    (r"^received_\d+", "Facebook Messenger"),
    (r"^\d+_\d+_\d+_[no]\.(jpe?g|webp)$", "Facebook/Instagram CDN filename"),
    (r"^Snapchat-\d+", "Snapchat"),
    (r"^PXL_\d{8}_\d{9}", "Google Pixel camera"),
    (r"^IMG_E?\d{4}\.(HEIC|JPG|JPEG|PNG|MOV)$", "iPhone camera roll (IMG_E = edited)"),
    (r"^\d{8}_\d{6}", "Samsung / Android camera"),
    (r"^IMG_\d{8}_\d{6}", "Android camera (many brands)"),
    (r"^DJI_\d{4}", "DJI drone — aerial branch; check DJI XMP for heading and altitude"),
    (r"^(GOPR|GP|GX|GH)\d{4,6}", "GoPro action camera"),
    (r"^DSC[_F]?\d{4}", "Dedicated camera (Sony/Nikon/Fujifilm style)"),
    (r"^(shutterstock|iStock|AdobeStock|GettyImages|dreamstime|depositphotos|123rf|alamy)[_-]?\d+", "Stock photo: search the ID on the agency site — captions name the place"),
    (r"-unsplash\.(jpe?g|png)$", "Unsplash stock photo: the photographer page often names the place"),
    (r"^pexels-", "Pexels stock photo"),
    (r"^Screenshot_\d{8}-\d{6}_(.+)\.", "Android screenshot; the suffix is the app that was open"),
]
SCREENSHOT_LANG = [
    (r"^Screen ?[Ss]hot", "en"), (r"^Captura de pantalla", "es"), (r"^Capture d.écran", "fr"), (r"^Bildschirmfoto", "de"),
    (r"^Schermata", "it"), (r"^Captura de Tela", "pt-BR"), (r"^Captura de ecrã", "pt-PT"), (r"^Schermafbeelding", "nl"),
    (r"^Skärmavbild", "sv"), (r"^Skjermbilde", "no"), (r"^Skærmbillede", "da"), (r"^Näyttökuva", "fi"),
    (r"^Zrzut ekranu", "pl"), (r"^Snímek obrazovky", "cs"), (r"^Képernyőfotó", "hu"), (r"^Ekran Resmi", "tr"),
    (r"^Снимок экрана", "ru"), (r"^Знімок екрана", "uk"), (r"^Στιγμιότυπο", "el"), (r"^スクリーンショット", "ja"),
    (r"^스크린샷", "ko"), (r"^(屏幕截图|截屏|截圖|螢幕截圖)", "zh"), (r"^ภาพหน้าจอ", "th"), (r"^Ảnh chụp màn hình", "vi"),
    (r"^Tangkapan Layar", "id"), (r"^لقطة شاشة", "ar"), (r"^צילום מסך", "he"),
]
LOCATION_KEY = re.compile(r"(City|Country|State|Province|Sublocation|Sub-location|Location|HomeTown|Destination|"
                          r"TimeZoneCity|TimeZone|DaylightSavings|Region|Landmark)", re.I)
SKIP_KEYS = re.compile(r"(ThumbnailImage|PreviewImage|JpgFromRaw|OtherImage|DataDump|MakerNoteUnknown|ICC_Profile:|"
                       r"Directory|FileName|SourceFile|FileAccessDate|FileInodeChangeDate|FileModifyDate|FilePermissions)")


# ------------------------------------------------------------------ exiftool

def exiftool(path: Path) -> dict:
    if not shutil.which("exiftool"):
        return {}
    r = subprocess.run(["exiftool", "-j", "-G1", "-a", "-n", "-u", "-ee", "-api", "LargeFileSupport=1", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(r.stdout)[0]
    except Exception:  # noqa: BLE001
        return {}


def pillow_fallback(path: Path) -> dict:
    """Minimal EXIF via Pillow when exiftool is missing (GPS, time, camera)."""
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    from PIL import ExifTags, Image
    out = {}
    try:
        im = Image.open(path)
        ex = im.getexif()
        for k, v in ex.items():
            out[f"IFD0:{ExifTags.TAGS.get(k, k)}"] = str(v)
        for k, v in ex.get_ifd(0x8769).items():
            out[f"ExifIFD:{ExifTags.TAGS.get(k, k)}"] = str(v)
        g = ex.get_ifd(0x8825)
        if g and 2 in g and 4 in g:
            lat = sum(float(x) / 60 ** i for i, x in enumerate(g[2])) * (-1 if g.get(1) == "S" else 1)
            lon = sum(float(x) / 60 ** i for i, x in enumerate(g[4])) * (-1 if g.get(3) == "W" else 1)
            out["GPS:GPSLatitude"], out["GPS:GPSLongitude"] = lat, lon
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    return out


def find(d: dict, *names: str):
    """First value whose tag name (after the group) matches one of names, case-insensitive."""
    low = {k.split(":", 1)[-1].lower(): k for k in d}
    for n in names:
        k = low.get(n.lower())
        if k is not None and d[k] not in ("", None):
            return d[k], k
    return None, None


def all_matching(d: dict, rx: re.Pattern) -> dict:
    return {k: v for k, v in d.items() if rx.search(k.split(":", 1)[-1]) and not SKIP_KEYS.search(k)
            and not isinstance(v, (dict, list)) and str(v).strip() not in ("", "0", "Unknown")}


# ------------------------------------------------------------------ time

def parse_dt(s) -> datetime | None:
    if s is None:
        return None
    s = str(s).strip()
    m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?", s)
    if not m:
        return None
    y, mo, d, h, mi, se, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(se))
    except ValueError:
        return None
    if tz:
        if tz == "Z":
            return dt.replace(tzinfo=timezone.utc)
        sign = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        return dt.replace(tzinfo=timezone(sign * timedelta(hours=hh, minutes=mm)))
    return dt


def parse_offset(s) -> int | None:
    if s is None:
        return None
    m = re.fullmatch(r"\s*([+-])(\d{1,2}):?(\d{2})\s*", str(s))
    if not m:
        return None
    return (1 if m.group(1) == "+" else -1) * (int(m.group(2)) * 60 + int(m.group(3)))


def zones() -> list[tuple[list[str], str]]:
    for p in ZONE_TAB:
        if p.exists():
            out = []
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    out.append((parts[0].split(","), parts[2]))
            return out
    return []


def countries_for_offset(minutes: int, local: datetime | None) -> dict[str, list[str]]:
    """ISO2 → zones whose UTC offset equals minutes at that local date (DST-aware)."""
    when = (local or datetime(2024, 1, 15, 12)).replace(tzinfo=None)
    out: dict[str, list[str]] = {}
    for ccs, zname in zones():
        try:
            off = ZoneInfo(zname).utcoffset(when)
        except Exception:  # noqa: BLE001
            continue
        if off is not None and int(off.total_seconds() // 60) == minutes:
            for cc in ccs:
                out.setdefault(cc, []).append(zname)
    return out


# ------------------------------------------------------------------ jpeg

IJG_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29,
            51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121,
            120, 101, 72, 92, 95, 98, 112, 100, 103, 99]


def jpeg_info(path: Path) -> dict:
    try:
        from PIL import Image
        im = Image.open(path)
    except Exception:  # noqa: BLE001
        return {}
    info = {"format": im.format, "size": list(im.size), "mode": im.mode}
    if im.format == "JPEG":
        q = getattr(im, "quantization", None) or {}
        if 0 in q:
            t = list(q[0])
            # estimate IJG quality from the mean ratio to the standard luminance table (zigzag order is irrelevant for the mean)
            ratio = sum(t) / sum(IJG_LUMA)
            scale = ratio * 100
            quality = (200 - scale) / 2 if scale <= 100 else 5000 / scale
            info["jpeg_quality_estimate"] = int(max(1, min(100, round(quality))))
        info["progressive"] = bool(im.info.get("progressive") or im.info.get("progression"))
        try:
            from PIL import JpegImagePlugin
            info["subsampling"] = {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}.get(JpegImagePlugin.get_sampling(im), "?")
        except Exception:  # noqa: BLE001
            pass
    w, h = im.size
    longest = max(w, h)
    hints = []
    if longest in (1600, 2560):
        hints.append("long side 1600/2560 px: typical of WhatsApp recompression")
    if longest == 2048:
        hints.append("long side 2048 px: typical of Facebook/Messenger")
    if longest == 1280:
        hints.append("long side 1280 px: typical of Telegram (compressed) and older WeChat")
    if w == 1080:
        hints.append("width 1080 px: typical of Instagram")
    if info.get("jpeg_quality_estimate", 100) < 80:
        hints.append("JPEG quality < 80: recompressed by an app or platform, metadata likely stripped")
    if w * 9 == h * 16 or w * 16 == h * 9 or abs(w / h - 9 / 19.5) < 0.01 or abs(h / w - 9 / 19.5) < 0.01:
        hints.append("screen aspect ratio (16:9 or ~19.5:9): may be a screenshot or video frame")
    info["platform_hints"] = hints
    return info


# ------------------------------------------------------------------ thumbnails

def extract_previews(path: Path, out_dir: Path, main_size: list[int] | None) -> list[dict]:
    if not shutil.which("exiftool"):
        return []
    res = []
    for tag in ("ThumbnailImage", "PreviewImage", "JpgFromRaw", "OtherImage"):
        r = subprocess.run(["exiftool", "-b", f"-{tag}", str(path)], capture_output=True)
        data = r.stdout
        if len(data) < 500:
            continue
        p = out_dir / f"thumb_{tag}.jpg"
        p.write_bytes(data)
        rec = {"tag": tag, "file": p.name, "bytes": len(data)}
        try:
            from PIL import Image
            with Image.open(p) as im:
                rec["size"] = list(im.size)
            if main_size:
                ar_main = main_size[0] / main_size[1]
                ar_t = rec["size"][0] / rec["size"][1]
                if abs(ar_main - ar_t) > 0.03 and abs(ar_main - 1 / ar_t) > 0.03:
                    rec["note"] = ("aspect ratio differs from the main image: the main image was cropped or edited after capture; "
                                   "this preview may show the original framing — open it")
        except Exception:  # noqa: BLE001
            pass
        res.append(rec)
    return res


def extract_motion(path: Path, out_dir: Path) -> list[dict]:
    """Pull the embedded video out of a motion photo (Google/Samsung/Pixel JPEGs carry an MP4 after the still): more
    frames and slightly different angles than the single frame, and it is not always stripped on sharing."""
    res = []
    if shutil.which("exiftool"):
        for tag in ("EmbeddedVideoFile", "MotionPhotoVideo", "MicroVideo", "EmbeddedVideo"):
            r = subprocess.run(["exiftool", "-b", f"-{tag}", str(path)], capture_output=True)
            if len(r.stdout) > 2000:
                p = out_dir / f"motion_{tag}.mp4"
                p.write_bytes(r.stdout)
                res.append({"tag": tag, "file": p.name, "bytes": len(r.stdout),
                            "note": "embedded video — extract frames with ffmpeg and run recon on the clearest"})
                return res
    data = path.read_bytes()                                   # byte-scan: an MP4 'ftyp' box appended after the primary image
    i = data.find(b"ftyp")
    while i != -1:
        start = i - 4
        if start > 1000 and int.from_bytes(data[start:start + 4], "big") >= 8 and data[i + 4:i + 8].isalnum():
            mp4 = data[start:]
            if len(mp4) > 2000:
                p = out_dir / "motion_trailer.mp4"
                p.write_bytes(mp4)
                res.append({"tag": "trailer-scan", "file": p.name, "bytes": len(mp4), "offset": start,
                            "note": "video appended after the image — extract frames with ffmpeg and run recon on the clearest"})
                break
        i = data.find(b"ftyp", i + 4)
    return res


# ------------------------------------------------------------------ digest

def digest(path: Path, raw: dict) -> dict:
    d: dict = {"file": path.name, "bytes": path.stat().st_size,
               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    d["image"] = jpeg_info(path)
    # filename
    fn = []
    for rx, label in FILENAME_PATTERNS:
        m = re.search(rx, path.name, re.I)
        if m:
            rec = {"pattern": label}
            if m.groups() and m.group(1) and m.group(1).isdigit() and len(m.group(1)) == 13:
                rec["epoch_ms_utc"] = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).isoformat()
            elif m.groups() and m.group(1) and "screenshot" in label.lower():
                rec["app"] = m.group(1)
            fn.append(rec)
    for rx, lang in SCREENSHOT_LANG:
        if re.search(rx, path.name):
            fn.append({"pattern": f"screenshot named in the OS language “{lang}” (device language, a weak country hint)"})
    d["filename_hints"] = fn
    # camera
    cam = {}
    for k in ("Make", "Model", "LensModel", "Software", "HostComputer", "CreatorTool", "HistorySoftwareAgent"):
        v, _ = find(raw, k)
        if v:
            cam[k] = str(v)
    d["camera"] = cam
    # GPS
    gps = {}
    lat, klat = find(raw, "GPSLatitude")
    lon, _ = find(raw, "GPSLongitude")
    if lat is None:
        pos, _ = find(raw, "GPSPosition", "GPSCoordinates", "Location", "LocationISO6709")
        if pos:
            m = re.findall(r"[+-]?\d+(?:\.\d+)?", str(pos))
            if len(m) >= 2:
                lat, lon = float(m[0]), float(m[1])
    if lat is not None and lon is not None:
        try:
            lat, lon = float(lat), float(lon)
            ref_lat, _ = find(raw, "GPSLatitudeRef")
            ref_lon, _ = find(raw, "GPSLongitudeRef")
            if str(ref_lat).upper().startswith("S") and lat > 0:
                lat = -lat
            if str(ref_lon).upper().startswith("W") and lon > 0:
                lon = -lon
            if not (lat == 0 and lon == 0):
                gps = {"lat": round(lat, 7), "lon": round(lon, 7), "source_tag": klat or "position"}
        except (TypeError, ValueError):
            pass
    for k, name in (("GPSAltitude", "altitude_m"), ("GPSImgDirection", "image_direction_deg"),
                    ("GPSImgDirectionRef", "direction_ref"), ("GPSHPositioningError", "h_error_m"),
                    ("GPSSpeed", "speed"), ("GPSProcessingMethod", "method"), ("GPSDestBearing", "dest_bearing_deg"),
                    ("AbsoluteAltitude", "dji_absolute_altitude_m"), ("RelativeAltitude", "dji_relative_altitude_m"),
                    ("GimbalYawDegree", "dji_gimbal_yaw_deg"), ("GimbalPitchDegree", "dji_gimbal_pitch_deg"),
                    ("FlightYawDegree", "dji_flight_yaw_deg"), ("CameraElevationAngle", "camera_elevation_deg")):
        v, _ = find(raw, k)
        if v not in (None, ""):
            gps[name] = v
    d["gps"] = gps
    # time
    t = {}
    for k in ("DateTimeOriginal", "CreateDate", "ModifyDate", "OffsetTimeOriginal", "OffsetTime", "OffsetTimeDigitized",
              "SubSecTimeOriginal", "GPSDateStamp", "GPSTimeStamp", "GPSDateTime", "DateCreated", "CreationDate",
              "MediaCreateDate", "TimeZone", "TimeZoneOffset", "TimeZoneCity", "DaylightSavings"):
        v, _ = find(raw, k)
        if v not in (None, ""):
            t[k] = str(v)
    local = parse_dt(t.get("DateTimeOriginal") or t.get("CreateDate") or t.get("DateCreated") or t.get("CreationDate"))
    offset = parse_offset(t.get("OffsetTimeOriginal") or t.get("OffsetTime"))
    how = "OffsetTime tag" if offset is not None else None
    if offset is None and local is not None and local.tzinfo is not None:
        offset, how = int(local.utcoffset().total_seconds() // 60), "offset inside the date string"
    if offset is None and local is not None and t.get("GPSDateTime"):
        utc = parse_dt(t["GPSDateTime"])
        if utc is not None:
            delta = (local.replace(tzinfo=None) - utc.replace(tzinfo=None)).total_seconds() / 60
            rounded = int(round(delta / 15.0) * 15)
            if abs(delta - rounded) <= 3 and abs(rounded) <= 14 * 60:
                offset, how = rounded, "local capture time minus GPS UTC time"
    if offset is not None:
        cc_zones = countries_for_offset(offset, local)
        sign = "+" if offset >= 0 else "-"
        t["utc_offset"] = f"{sign}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}"
        t["utc_offset_source"] = how
        t["countries_with_this_offset_on_that_date"] = sorted(cc_zones)
        lo, hi = offset / 60 * 15 - 7.5, offset / 60 * 15 + 7.5
        ew = lambda x: f"{abs(x):.1f}°{'E' if x >= 0 else 'W'}"  # noqa: E731
        t["nominal_longitude_band"] = (f"{ew(lo)} to {ew(hi)} "
                                       "(political zones stretch far from this: China, Spain, western Russia, Argentina)")
    d["time"] = t
    # location-ish text and maker-note settings
    d["location_text"] = {k: v for k, v in all_matching(raw, LOCATION_KEY).items()
                          if not k.startswith(("Composite:", "System:")) and "GPS" not in k}
    # editing / provenance
    prov = {}
    for k, v in raw.items():
        tag = k.split(":", 1)[-1]
        if re.search(r"(DigitalSourceType|C2PA|JUMBF|Claim|Credential|History|DocumentAncestors|OriginalDocumentID|"
                     r"Software|CreatorTool|ProcessingSoftware)", tag) and not isinstance(v, (dict, list)):
            prov[k] = str(v)[:200]
    if any("trainedAlgorithmicMedia" in v or "compositeWithTrainedAlgorithmicMedia" in v for v in prov.values()):
        prov["WARNING"] = "File declares AI-generated or AI-composited content (IPTC DigitalSourceType)"
    d["provenance"] = prov
    d["has_exif"] = any(k.startswith(("ExifIFD:", "IFD0:", "GPS:")) for k in raw)
    return d


def to_md(d: dict, previews: list[dict]) -> str:
    L = [f"# Metadata: {d['file']}", ""]
    L.append("Metadata describes the file, not necessarily the scene: check it against the image; the image wins.")
    L.append("")
    g = d.get("gps") or {}
    if g.get("lat") is not None:
        L.append(f"- **GPS** {g['lat']}, {g['lon']} (tag {g.get('source_tag')})"
                 + (f", error ±{g['h_error_m']} m" if g.get("h_error_m") else "")
                 + (f", image direction {g['image_direction_deg']}° ({g.get('direction_ref', '?')})" if g.get("image_direction_deg") is not None else ""))
        L.append("  Treat as a hypothesis: verify with the scene (street view, satellite) before reporting it.")
    extra = {k: v for k, v in g.items() if k not in ("lat", "lon", "source_tag", "h_error_m", "image_direction_deg", "direction_ref")}
    if extra:
        L.append(f"- GPS/drone extras: {json.dumps(extra, ensure_ascii=False)}")
    if not g:
        L.append("- No GPS.")
    t = d.get("time") or {}
    if t:
        L.append(f"- **Time**: {', '.join(f'{k}={v}' for k, v in t.items() if k not in ('countries_with_this_offset_on_that_date', 'nominal_longitude_band'))}")
        if t.get("countries_with_this_offset_on_that_date"):
            ccs = t["countries_with_this_offset_on_that_date"]
            L.append(f"  UTC offset {t['utc_offset']} ({t['utc_offset_source']}) was used on that date in {len(ccs)} countries/territories: "
                     f"{', '.join(ccs[:60])}{' …' if len(ccs) > 60 else ''}. Nominal longitude band {t['nominal_longitude_band']}.")
            L.append("  Caveat: phones set the offset of the network/home zone; travellers and wrong clocks happen.")
    else:
        L.append("- No capture time.")
    if d.get("camera"):
        L.append(f"- **Camera/software**: {json.dumps(d['camera'], ensure_ascii=False)}")
    if d.get("location_text"):
        L.append(f"- **Location fields / camera zone settings**: {json.dumps(d['location_text'], ensure_ascii=False)[:800]}")
    if d.get("filename_hints"):
        for h in d["filename_hints"]:
            L.append(f"- **Filename**: {h['pattern']}" + (f" (UTC {h['epoch_ms_utc']})" if h.get("epoch_ms_utc") else "")
                     + (f" — app: {h['app']}" if h.get("app") else ""))
    im = d.get("image") or {}
    if im:
        L.append(f"- **Image**: {im.get('format')} {im.get('size')}"
                 + (f", JPEG quality ≈{im['jpeg_quality_estimate']}" if im.get("jpeg_quality_estimate") else "")
                 + (f", subsampling {im['subsampling']}" if im.get("subsampling") else ""))
        for h in im.get("platform_hints", []):
            L.append(f"  - {h}")
    if d.get("provenance"):
        p = d["provenance"]
        if p.get("WARNING"):
            L.append(f"- **WARNING**: {p['WARNING']}")
        L.append(f"- Provenance/editing tags: {json.dumps({k: v for k, v in p.items() if k != 'WARNING'}, ensure_ascii=False)[:600]}")
    if previews:
        for pv in previews:
            L.append(f"- Embedded {pv['tag']} {pv.get('size')} → `{pv['file']}`" + (f" — {pv['note']}" if pv.get("note") else ""))
    if not d.get("has_exif"):
        L.append("- No EXIF block: typical of messaging apps, social networks, screenshots and re-saved images. Absence proves nothing.")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photo", type=Path)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--no-previews", action="store_true")
    args = ap.parse_args()
    path = args.photo.expanduser().resolve()
    raw = exiftool(path)
    backend = "exiftool"
    if not raw:
        raw, backend = pillow_fallback(path), "pillow (install exiftool for maker notes, XMP, video atoms)"
    d = digest(path, raw)
    d["backend"] = backend
    previews = []
    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        if not args.no_previews:
            previews = extract_previews(path, args.out_dir, d["image"].get("size"))
            motion = extract_motion(path, args.out_dir)
            if motion:
                d["motion_video"] = motion
                for mv in motion:
                    print(f"motion photo: extracted {mv['file']} ({mv['bytes']} bytes) — {mv['note']}")
        d["previews"] = previews
        raw_clean = {k: v for k, v in raw.items() if not SKIP_KEYS.search(k) or k.startswith("File:")}
        (args.out_dir / "meta.json").write_text(json.dumps({"digest": d, "raw": raw_clean}, ensure_ascii=False, indent=1, default=str),
                                                encoding="utf-8")
    md = to_md(d, previews)
    if args.out_dir:
        (args.out_dir / "meta.md").write_text(md, encoding="utf-8")
    print(md)
    if args.out_dir:
        print(f"-> {args.out_dir / 'meta.md'}, {args.out_dir / 'meta.json'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    main()

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow", "opencv-python-headless"]
# ///
"""First-class video analysis: metadata, keyframes, motion, audio and cross-frame consensus.

  video.py analyze clip.mp4 --out-dir video/                 # complete local pass
  video.py analyze clip.mp4 --frames 12 --no-recon --no-audio
  video.py timeline video/analysis.json --candidate lat,lon
  video.py route video/frames/*.jpg --out video/route.json

The tool deliberately separates extraction from interpretation: frames are evidence,
machine priors rank hypotheses, and a final location still requires fixed structure
to be verified across frames. ffprobe and ffmpeg are required; exiftool is optional.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
UV = os.environ.get("UV") or "uv"


def _which(name: str) -> str | None:
    return shutil.which(name)


def require_media_tools() -> None:
    missing = [name for name in ("ffprobe", "ffmpeg") if not _which(name)]
    if missing:
        raise SystemExit(
            "Missing required tool(s): " + ", ".join(missing)
            + ". Install ffmpeg (which provides ffprobe), then run doctor.py --network."
        )


def probe(video: Path) -> dict:
    """Run ffprobe once and return a compact, stable media description."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(video)],
        capture_output=True, text=True,
    )
    if r.returncode:
        raise SystemExit("ffprobe failed: " + r.stderr.strip()[-500:])
    raw = json.loads(r.stdout)
    streams = raw.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration = float(raw.get("format", {}).get("duration") or video_stream.get("duration") or 0)
    rate_text = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1"
    try:
        n, d = (int(x) for x in rate_text.split("/"))
        fps = n / d if d else 0.0
    except Exception:
        fps = 0.0
    rotation = 0
    for side in video_stream.get("side_data_list", []) or []:
        if "rotation" in side:
            try:
                rotation = int(side["rotation"])
            except (TypeError, ValueError):
                pass
    rotation = rotation or int(video_stream.get("tags", {}).get("rotate", 0) or 0)
    return {
        "path": str(video.resolve()),
        "size_bytes": int(raw.get("format", {}).get("size") or video.resolve().stat().st_size),
        "duration_seconds": round(duration, 6),
        "width": width,
        "height": height,
        "fps": round(fps, 6),
        "rotation_degrees": rotation,
        "video_codec": video_stream.get("codec_name"),
        "audio": None if audio_stream is None else {
            "codec": audio_stream.get("codec_name"),
            "sample_rate": audio_stream.get("sample_rate"),
            "channels": audio_stream.get("channels"),
        },
        "format": raw.get("format", {}).get("format_name"),
    }


def image_quality(path: Path) -> float:
    """Cheap blur/detail/contrast rank used only to choose representative frames."""
    im = np.asarray(Image.open(path).convert("L").resize((320, 180)))
    if im.size == 0:
        return 0.0
    dx = im[:, 1:] - im[:, :-1]
    dy = im[1:, :] - im[:-1, :]
    detail = float(np.mean(np.abs(dx)) + np.mean(np.abs(dy)))
    contrast = float(np.std(im))
    return detail * 1.5 + contrast


def image_distance(a: Path, b: Path) -> float:
    with Image.open(a).convert("L").resize((96, 54)) as ia, Image.open(b).convert("L").resize((96, 54)) as ib:
        aa, bb = np.asarray(ia, dtype=np.int16), np.asarray(ib, dtype=np.int16)
    return float(np.mean(np.abs(aa - bb)))


def extract_frames(video: Path, out: Path, count: int, scene_threshold: float) -> tuple[list[dict], list[dict]]:
    """Extract fixed cadence and scene-change frames, deduplicate, and rank them."""
    require_media_tools()
    out.mkdir(parents=True, exist_ok=True)
    info = probe(video)
    duration = info["duration_seconds"]
    if duration <= 0:
        raise SystemExit("ffprobe could not determine the recording duration.")
    count = max(2, count)
    interval = max(0.25, duration / max(count, 1))

    fixed_dir = out / "fixed"
    fixed_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video),
         "-vf", f"fps=1/{interval:.6f},scale='min(1280,iw)':-2",
         "-q:v", "2", str(fixed_dir / "frame_%04d.jpg")],
        capture_output=True, text=True,
    )
    if r.returncode:
        raise SystemExit("ffmpeg frame extraction failed: " + r.stderr.strip()[-500:])

    scene_dir = out / "scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    scene_rate = max(0.25, min(2.0, 1.0))
    scene_run = subprocess.run(
        ["ffmpeg", "-v", "info", "-nostdin", "-i", str(video),
         "-vf", f"fps={scene_rate:.6f},scale='min(1280,iw)':-2,"
                f"select='gt(scene,{scene_threshold:.3f})'",
         "-vsync", "vfr", "-q:v", "2", str(scene_dir / "scene_%04d.jpg")],
        capture_output=True, text=True,
    )
    if scene_run.returncode:
        raise SystemExit("ffmpeg scene detection failed: " + scene_run.stderr.strip()[-500:])
    scene_times: dict[int, float] = {}
    if scene_run.returncode == 0:
        for match in re.finditer(r"n:\s*(\d+).*?pts_time:(\d+(?:\.\d+)?)", scene_run.stderr):
            scene_times[int(match.group(1)) + 1] = float(match.group(2))

    candidates = sorted(fixed_dir.glob("frame_*.jpg")) + sorted(scene_dir.glob("scene_*.jpg"))
    unique: list[dict] = []
    for path in candidates:
        fixed = path.parent == fixed_dir
        stem_index = int(path.stem.rsplit("_", 1)[-1])
        ordinal = stem_index - 1
        timestamp = 0.0 if fixed else min(duration, ordinal / scene_rate)
        if fixed:
            timestamp = min(duration, ordinal * interval)
        elif stem_index in scene_times:
            timestamp = min(duration, scene_times[stem_index])
        if any(
            abs(timestamp - old["timestamp_seconds"]) < 0.5
            and image_distance(path, old["path"]) < 3.5
            for old in unique
        ):
            continue
        unique.append({
            "path": path,
            "timestamp_seconds": round(timestamp, 3),
            "source": "fixed" if fixed else "scene",
            "quality": image_quality(path),
        })

    # Keep frames distributed across the clip; a long run of similar imagery is still temporal evidence.
    unique.sort(key=lambda r: (r["timestamp_seconds"], r["source"]))
    if len(unique) <= count:
        selected = unique
    else:
        def evenly(rows: list[dict], limit: int) -> list[dict]:
            if limit <= 0 or not rows:
                return []
            if limit >= len(rows):
                return rows
            if limit == 1:
                return [rows[len(rows) // 2]]
            indices = [round(i * (len(rows) - 1) / (limit - 1)) for i in range(limit)]
            return [rows[i] for i in indices]

        fixed = [r for r in unique if r["source"] == "fixed"]
        scenes = [r for r in unique if r["source"] == "scene"]
        scene_slots = min(len(scenes), max(1, count // 4), max(0, count - 1))
        chosen_scenes = evenly(sorted(scenes, key=lambda r: r["quality"], reverse=True), scene_slots)
        chosen_fixed = evenly(fixed, count - len(chosen_scenes))
        selected = sorted(chosen_fixed + chosen_scenes, key=lambda r: r["timestamp_seconds"])

    selected.sort(key=lambda r: r["timestamp_seconds"])
    selected = [dict(row) for row in selected]
    unique = [dict(row) for row in unique]
    for i, row in enumerate(selected, 1):
        row["id"] = f"f{i:03d}"
        row["path"] = str(row["path"])
    for i, row in enumerate(unique, 1):
        row["id"] = f"u{i:03d}"
        row["path"] = str(row["path"])
    return selected, unique


def run_command(cmd: list[str], timeout: int = 1800) -> tuple[int, str, str]:
    r = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
        env={**os.environ, "PYTHONUTF8": "1", "TOKENIZERS_PARALLELISM": "false"},
    )
    return r.returncode, r.stdout, r.stderr


def run_recon(frame: dict, out: Path, no_ml: bool = False, no_rev: bool = False) -> dict:
    cmd = [
        UV, "run", "-q", str(HERE / "recon.py"),
        frame["path"], "--out-dir", str(out),
    ]
    if no_ml:
        cmd.append("--no-ml")
    if no_rev:
        cmd.append("--no-rev")
    rc, stdout, stderr = run_command(cmd)
    recon = {"rc": rc, "stdout_tail": stdout[-400:], "error": stderr[-500:] if rc else ""}
    recon_json = out / "recon.json"
    recon_md = out / "recon.md"
    if recon_json.exists():
        recon["json"] = str(recon_json)
    if recon_md.exists():
        recon["markdown"] = str(recon_md)

    prior = out / "prior" / "prior.json"
    if prior.exists():
        try:
            d = json.loads(prior.read_text(encoding="utf-8"))
            recon["prior"] = d
            recon["combined"] = d.get("combined", [])
            recon["clusters"] = d.get("geoclip", {}).get("clusters", [])
        except (OSError, json.JSONDecodeError):
            pass
    text = out.parent / f"{frame['id']}_textgeo.json"
    return recon


def consensus(frame_results: list[dict]) -> dict:
    """Fuse country and GeoCLIP cluster outputs across frames."""
    countries: dict[str, float] = {}
    clusters: dict[str, dict] = {}
    informative = 0
    for fr in frame_results:
        p = fr.get("recon", {}).get("prior") or {}
        assessment = p.get("assessment") or {}
        if assessment.get("informative") is False:
            continue
        informative += 1
        for row in fr.get("recon", {}).get("combined", [])[:5]:
            cc = row.get("iso2")
            if cc:
                countries[cc] = countries.get(cc, 0.0) + float(row.get("p", 0))
        for row in fr.get("recon", {}).get("clusters", [])[:3]:
            near = row.get("near") or {}
            cc = near.get("country")
            lat, lon = row.get("lat"), row.get("lon")
            if lat is None or lon is None:
                continue
            item = None
            for existing in clusters.values():
                if existing["country"] == cc:
                    p1, p2 = math.radians(existing["lat"]), math.radians(lat)
                    dp = p2 - p1
                    dl = math.radians(lon - existing["lon"])
                    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
                    km = 12742 * math.asin(min(1.0, math.sqrt(h)))
                    if km <= 50:
                        item = existing
                        break
            if item is None:
                key = f"{lat:.2f},{lon:.2f}"
                item = clusters.setdefault(key, {"lat": lat, "lon": lon,
                                                  "country": cc,
                                                  "mass": 0.0,
                                                  "frames": []})
            mass = float(row.get("mass", 0))
            old_mass = item["mass"]
            if old_mass:
                item["lat"] = (item["lat"] * old_mass + lat * mass) / (old_mass + mass)
                item["lon"] = (item["lon"] * old_mass + lon * mass) / (old_mass + mass)
            item["mass"] = old_mass + mass
            if fr["id"] not in item["frames"]:
                item["frames"].append(fr["id"])
    country_rows = sorted(
        [{"iso2": cc, "support": round(v, 6)} for cc, v in countries.items()],
        key=lambda r: r["support"], reverse=True,
    )
    cluster_rows = sorted(clusters.values(), key=lambda r: r["mass"], reverse=True)
    for row in cluster_rows:
        row["mass"] = round(row["mass"], 6)
    return {
        "informative_frames": informative,
        "countries": country_rows,
        "clusters": cluster_rows,
        "status": "informative" if informative else "uninformative",
    }


def optical_motion(frames: list[dict]) -> dict:
    """Estimate frame-to-frame camera motion; requires OpenCV."""
    try:
        import cv2
    except ImportError:
        return {"status": "unavailable", "reason": "opencv-python-headless is not installed",
                "segments": [], "heading_degrees": None, "route_confidence": 0.0}
    segments = []
    headings = []
    for a, b in zip(frames, frames[1:]):
        ga = cv2.imread(a["path"], cv2.IMREAD_GRAYSCALE)
        gb = cv2.imread(b["path"], cv2.IMREAD_GRAYSCALE)
        if ga is None or gb is None:
            continue
        ga = cv2.resize(ga, (640, 360))
        gb = cv2.resize(gb, (640, 360))
        pa = cv2.goodFeaturesToTrack(ga, maxCorners=300, qualityLevel=0.01,
                                     minDistance=8, blockSize=7)
        if pa is None:
            segments.append({"from": a["id"], "to": b["id"], "status": "no_features",
                             "motion_x": None, "motion_y": None, "confidence": 0.0})
            continue
        pb, st, err = cv2.calcOpticalFlowPyrLK(ga, gb, pa, None)
        if pb is None:
            continue
        good = st.reshape(-1) == 1
        if not np.any(good):
            continue
        da = pa.reshape(-1, 2)[good]
        db = pb.reshape(-1, 2)[good]
        flow = db - da
        dx = float(np.median(flow[:, 0]))
        dy = float(np.median(flow[:, 1]))
        mad = float(np.median(np.sqrt(np.sum((flow - [dx, dy]) ** 2, axis=1))))
        confidence = float(np.clip(len(flow) / 300.0 * (1.0 - min(mad / 20.0, 1.0)), 0.0, 1.0))
        speed = math.hypot(dx, dy)
        heading = (math.degrees(math.atan2(dx, -dy)) + 360) % 360 if speed >= 1.0 else None
        if heading is not None:
            headings.append({"heading": heading, "confidence": confidence})
        segments.append({
            "from": a["id"], "to": b["id"], "start_seconds": a["timestamp_seconds"],
            "end_seconds": b["timestamp_seconds"],
            "motion_x_pixels": round(dx, 3), "motion_y_pixels": round(dy, 3),
            "tracked_features": int(len(flow)), "median_residual_pixels": round(mad, 3),
            "confidence": round(confidence, 4),
            "status": "estimated",
            "interpretation": "camera/image moved right" if dx > 2 else
                              ("camera/image moved left" if dx < -2 else "mostly forward/turning"),
        })
    if headings:
        vectors = np.array([(math.cos(math.radians(h["heading"])),
                             math.sin(math.radians(h["heading"]))) * h["confidence"]
                            for h in headings])
        mean = np.sum(vectors, axis=0)
        heading = (math.degrees(math.atan2(mean[1], mean[0])) + 360) % 360
    else:
        heading = None
    route_confidence = float(np.mean([s["confidence"] for s in segments])) if segments else 0.0
    return {
        "status": "estimated" if segments else "no_motion",
        "segments": segments,
        "heading_degrees": round(heading, 2) if heading is not None else None,
        "route_confidence": round(route_confidence, 4),
    }


def run_audio(video: Path, out: Path) -> dict:
    if not _which("ffmpeg"):
        return {"status": "unavailable", "reason": "ffmpeg missing"}
    out.mkdir(parents=True, exist_ok=True)
    wav = out / "audio.wav"
    rc, stdout, stderr = run_command([
        UV, "run", "-q", str(HERE / "audio.py"), "extract",
        str(video), "--out", str(wav), "--rate", "16000",
    ])
    if rc:
        return {"status": "failed", "error": stderr[-500:], "audio_file": None}
    rc, stdout, stderr = run_command([
        UV, "run", "-q", str(HERE / "audio.py"), "events",
        str(wav), "--out", str(out / "events.json"),
    ])
    events = out / "events.json"
    return {
        "status": "analyzed" if rc == 0 else "partial",
        "audio_file": str(wav),
        "events_file": str(events) if events.exists() else None,
        "output_tail": stdout[-400:],
        "error": stderr[-500:] if rc else "",
    }


def run_metadata(video: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    rc, stdout, stderr = run_command([
        UV, "run", "-q", str(HERE / "meta.py"), str(video),
        "--out-dir", str(out),
    ])
    result = {"status": "analyzed" if rc == 0 else "failed", "markdown": None,
              "json": None, "error": stderr[-500:] if rc else ""}
    if (out / "meta.md").exists():
        result["markdown"] = str(out / "meta.md")
    if (out / "meta.json").exists():
        result["json"] = str(out / "meta.json")
    return result


def md_report(analysis: dict) -> str:
    media = analysis["media"]
    frames = analysis["selected_frames"]
    consensus = analysis["consensus"]
    route = analysis["motion"]
    L = [
        "# Video geolocation analysis",
        "",
        f"- Source: `{media['path']}`",
        f"- Duration: {media['duration_seconds']} s",
        f"- Frame size: {media['width']} × {media['height']}",
        f"- Frames analyzed: {len(frames)}",
        f"- Audio stream: {'yes' if media['audio'] else 'no'}",
        f"- Metadata: `{analysis['metadata'].get('json') or 'not available'}`",
        "",
        "## Cross-frame country consensus",
        "",
    ]
    for row in consensus["countries"][:8]:
        L.append(f"- {row['iso2']}: {row['support']:.3f}")
    if not consensus["countries"]:
        L.append("- No informative learned-prior result.")
    L += ["", "## Representative frames", ""]
    for row in frames:
        L.append(f"- {row['timestamp_seconds']} s: `{row['path']}` ({row['source']}, quality {row['quality']:.1f})")
    L += ["", "## Camera/motion estimate", "",
          f"- Status: {route['status']}",
          f"- Route confidence: {route['route_confidence']}"]
    if route["heading_degrees"] is not None:
        L.append(f"- Mean scene-flow heading: {route['heading_degrees']}°")
    L += ["", "## Evidence rules",
          "- Frame-level model outputs rank only; they do not prove a location.",
          "- A final point needs fixed structure visible across one or more frames.",
          "- Do not average different candidate towns or stop at a city midpoint.",
          "", "## Audio",
          f"- Status: {analysis['audio']['status']}"]
    if analysis["audio"].get("events_file"):
        L.append(f"- Events: `{analysis['audio']['events_file']}`")
    L += ["", "## Next checks",
          "1. Compare every representative frame against fixed scene structure.",
          "2. Follow the strongest repeated country/region clue, not an isolated model guess.",
          "3. Use the motion segments to verify route direction and viewpoint progression.",
          "4. If audio is partial or absent, record that limitation explicitly.",
          ""]
    return "\n".join(L)


def cmd_analyze(args) -> None:
    require_media_tools()
    video = args.video.expanduser().resolve(strict=True)
    if not video.is_file():
        raise SystemExit("Video must be a regular file.")
    out = args.out_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    media = probe(video)
    (out / "media.json").write_text(json.dumps(media, ensure_ascii=False, indent=1), encoding="utf-8")
    metadata = run_metadata(video, out / "metadata")

    selected, unique = extract_frames(video, out / "frames", args.frames, args.scene_threshold)
    frame_results = []
    for frame in selected:
        frame_out = out / "frames" / "recon" / frame["id"]
        recon = None
        if not args.no_recon:
            recon = run_recon(frame, frame_out, args.recon_no_ml, args.recon_no_rev)
        frame_results.append({"id": frame["id"], "timestamp_seconds": frame["timestamp_seconds"],
                              "path": frame["path"], "source": frame["source"],
                              "recon": recon or {"rc": None, "skipped": True}})

    consensus_data = consensus(frame_results)
    motion = {"status": "skipped"} if args.no_motion else optical_motion(selected)
    audio = {"status": "skipped"} if args.no_audio or not media["audio"] else run_audio(video, out / "audio")

    analysis = {
        "schema_version": 1,
        "media": media,
        "metadata": metadata,
        "selected_frames": selected,
        "candidate_frames": unique,
        "frame_results": frame_results,
        "consensus": consensus_data,
        "motion": motion,
        "audio": audio,
    }
    (out / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "analysis.md").write_text(md_report(analysis), encoding="utf-8")
    print(json.dumps({
        "folder": str(out),
        "duration_seconds": media["duration_seconds"],
        "selected_frames": len(selected),
        "consensus": consensus_data,
        "motion": {"status": motion["status"], "route_confidence": motion.get("route_confidence", 0)},
        "audio": audio["status"],
        "metadata": metadata["status"],
    }, ensure_ascii=False, indent=1))
    print(f"-> {out / 'analysis.md'}, {out / 'analysis.json'}")


def cmd_route(args) -> None:
    frames = []
    for i, p in enumerate(args.frames, 1):
        path = p.expanduser().resolve(strict=True)
        if not path.is_file():
            raise SystemExit(f"Frame does not exist: {path}")
        frames.append({"id": f"f{i:03d}", "path": str(path),
                       "timestamp_seconds": i * args.interval,
                       "source": "explicit", "quality": image_quality(path)})
    result = optical_motion(frames)
    args.out.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.out.expanduser().resolve().write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"-> {args.out}")


def cmd_timeline(args) -> None:
    path = args.analysis.expanduser().resolve(strict=True)
    d = json.loads(path.read_text(encoding="utf-8"))
    candidate = None
    if args.candidate:
        parts = args.candidate.split(",")
        if len(parts) != 2:
            raise SystemExit("--candidate must be lat,lon")
        candidate = {"lat": float(parts[0]), "lon": float(parts[1])}
    segments = []
    for fr in d.get("frame_results", []):
        countries = []
        prior = fr.get("recon", {}).get("prior") or {}
        for row in (prior.get("combined") or [])[:3]:
            countries.append({"iso2": row.get("iso2"), "probability": row.get("p")})
        segments.append({
            "timestamp_seconds": fr["timestamp_seconds"],
            "frame": fr["path"],
            "frame_id": fr["id"],
            "prior_countries": countries,
            "candidate": candidate,
        })
    result = {
        "schema_version": 1,
        "source_analysis": str(path),
        "candidate": candidate,
        "duration_seconds": d.get("media", {}).get("duration_seconds"),
        "segments": segments,
        "cross_frame": d.get("consensus", {}),
        "motion": d.get("motion", {}),
        "audio": d.get("audio", {}),
        "limitations": [
            "This timeline ranks representative frames; it does not claim the candidate was at a segment.",
            "Candidate verification requires fixed landmarks or independently constrained camera positions.",
        ],
    }
    out = args.out.expanduser().resolve() if args.out else path.with_name("timeline.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"-> {out}")


def cmd_route_solve(args) -> None:
    cmd = [
        UV, "run", "-q", str(HERE / "route_solver.py"), "solve",
        str(args.analysis),
        "--out-dir", str(args.out_dir),
        "--speed", str(args.speed),
        "--step", str(args.step),
        "--beam", str(args.beam),
        "--radius", str(args.radius),
    ]
    if args.bbox:
        cmd += ["--bbox", args.bbox]
    for candidate in args.candidate or []:
        cmd += ["--candidate", candidate]
    if args.evidence:
        cmd += ["--evidence", str(args.evidence)]
    if args.road_data:
        cmd += ["--road-data", str(args.road_data)]
    if args.duration is not None:
        cmd += ["--duration", str(args.duration)]
    if args.proxy:
        cmd += ["--proxy", args.proxy]
    rc, stdout, stderr = run_command(cmd)
    if rc:
        raise SystemExit(stderr[-1000:] or stdout[-1000:] or "Route solver failed.")
    print(stdout[-1000:].strip())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    analyze = sub.add_parser("analyze", help="analyze metadata, frames, motion and audio")
    analyze.add_argument("video", type=Path)
    analyze.add_argument("--out-dir", type=Path, default=Path("video"))
    analyze.add_argument("--frames", type=int, default=12)
    analyze.add_argument("--scene-threshold", type=float, default=0.25)
    analyze.add_argument("--no-recon", action="store_true")
    analyze.add_argument("--recon-no-ml", action="store_true", help="run OCR/text recon but skip local models")
    analyze.add_argument("--recon-no-rev", action="store_true", help="skip reverse-image search on frames")
    analyze.add_argument("--no-motion", action="store_true")
    analyze.add_argument("--no-audio", action="store_true")
    analyze.set_defaults(func=cmd_analyze)

    route = sub.add_parser("route", help="estimate image-flow motion between explicit frames")
    route.add_argument("frames", nargs="+", type=Path)
    route.add_argument("--interval", type=float, default=1.0)
    route.add_argument("--out", type=Path, default=Path("route.json"))
    route.set_defaults(func=cmd_route)

    timeline = sub.add_parser("timeline", help="produce a frame-to-frame timeline and candidate attachment")
    timeline.add_argument("analysis", type=Path)
    timeline.add_argument("--candidate")
    timeline.add_argument("--out", type=Path)
    timeline.set_defaults(func=cmd_timeline)

    solve = sub.add_parser("route-solve", help="search road-network journeys consistent with the video")
    solve.add_argument("analysis", type=Path)
    solve.add_argument("--bbox", help="S,W,N,E search area")
    solve.add_argument("--candidate", action="append", help="lat,lon candidate seed; repeat for alternatives")
    solve.add_argument("--evidence", type=Path, help="timestamped anchor JSON")
    solve.add_argument("--road-data", type=Path, help="saved Overpass JSON; skips the live query")
    solve.add_argument("--radius", type=float, default=5000.0)
    solve.add_argument("--speed", type=float, default=13.4)
    solve.add_argument("--duration", type=float)
    solve.add_argument("--step", type=float, default=10.0)
    solve.add_argument("--beam", type=int, default=120)
    solve.add_argument("--proxy")
    solve.add_argument("--out-dir", type=Path, default=Path("route"))
    solve.set_defaults(func=cmd_route_solve)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    main()

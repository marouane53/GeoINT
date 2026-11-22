#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow", "opencv-python-headless"]
# ///
"""Offline tests for the video geolocation pipeline."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import video


class ProbeTests(unittest.TestCase):
    def test_ffprobe_is_compacted_and_rotation_is_read(self):
        raw = {
            "format": {"duration": "12.5", "size": "12345", "format_name": "mov,mp4"},
            "streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
                "duration": "12.5",
                "side_data_list": [{"rotation": -90}],
            }, {
                "codec_type": "audio", "codec_name": "aac",
                "sample_rate": "48000", "channels": 2,
            }],
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "clip.mp4"
            p.write_bytes(b"x")
            result = subprocess.CompletedProcess([], 0, json.dumps(raw), "")
            with patch.object(video.subprocess, "run", return_value=result):
                got = video.probe(p)
        self.assertEqual(got["width"], 1920)
        self.assertEqual(got["height"], 1080)
        self.assertEqual(got["duration_seconds"], 12.5)
        self.assertEqual(got["rotation_degrees"], -90)
        self.assertEqual(got["audio"]["channels"], 2)
        self.assertAlmostEqual(got["fps"], 29.970029, places=5)


class ConsensusTests(unittest.TestCase):
    def test_country_support_and_clusters_fuse_across_frames(self):
        frame = lambda fid, countries, clusters, informative=True: {
            "id": fid,
            "recon": {
                "prior": {
                    "assessment": {"informative": informative},
                    "combined": [{"iso2": cc, "p": p} for cc, p in countries],
                    "geoclip": {"clusters": clusters},
                },
                "combined": [{"iso2": cc, "p": p} for cc, p in countries],
                "clusters": clusters,
            },
        }
        c1 = [{"lat": 48.1, "lon": 16.3, "near": {"country": "AT"}, "mass": 0.6}]
        c2 = [{"lat": 48.11, "lon": 16.31, "near": {"country": "AT"}, "mass": 0.4}]
        rows = [
            frame("f001", [("AT", 0.8), ("CZ", 0.1)], c1),
            frame("f002", [("AT", 0.7), ("DE", 0.2)], c2),
            frame("f003", [("DE", 0.9)], [], informative=False),
        ]
        got = video.consensus(rows)
        self.assertEqual(got["status"], "informative")
        self.assertEqual(got["informative_frames"], 2)
        self.assertEqual(got["countries"][0]["iso2"], "AT")
        self.assertEqual(got["countries"][0]["support"], 1.5)
        self.assertEqual(got["clusters"][0]["frames"], ["f001", "f002"])
        self.assertEqual(got["clusters"][0]["country"], "AT")

    def test_uninformative_frames_produce_no_country_signal(self):
        rows = [{"id": "f1", "recon": {"prior": {"assessment": {"informative": False},
                                                "combined": [{"iso2": "PT", "p": 0.9}],
                                                "geoclip": {"clusters": []}}}}]
        got = video.consensus(rows)
        self.assertEqual(got["status"], "uninformative")
        self.assertEqual(got["informative_frames"], 0)
        self.assertEqual(got["countries"], [])


class ExtractionTests(unittest.TestCase):
    def test_quality_and_near_duplicate_detection(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            a, b = d / "a.jpg", d / "b.jpg"
            Image.fromarray(np.full((180, 320), 128, dtype=np.uint8)).save(a)
            Image.fromarray(np.full((180, 320), 128, dtype=np.uint8)).save(b)
            self.assertLess(video.image_distance(a, b), 0.1)
            self.assertGreaterEqual(video.image_quality(a), 0)

    def test_selected_and_candidate_frame_ids_are_independent(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            source = d / "clip.mp4"
            source.write_bytes(b"fixture")

            def fake_ffmpeg(cmd, **kwargs):
                target = Path(cmd[-1])
                if "frame_%04d" in target.name:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    for i, color in enumerate((80, 80, 80, 80), 1):
                        Image.new("RGB", (96, 54), (color, color, color)).save(
                            target.with_name(f"frame_{i:04d}.jpg")
                        )
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch.object(video, "require_media_tools"), \
                    patch.object(video, "probe", return_value={"duration_seconds": 8.0}), \
                    patch.object(video.subprocess, "run", side_effect=fake_ffmpeg):
                selected, candidates = video.extract_frames(source, d / "frames", 3, 0.25)

            self.assertEqual(len(selected), 3)
            self.assertTrue(all(row["id"].startswith("f") for row in selected))
            self.assertTrue(all(row["id"].startswith("u") for row in candidates))
            self.assertNotEqual(selected[0]["id"], candidates[0]["id"])


class TimelineTests(unittest.TestCase):
    def test_timeline_is_explicit_about_candidate_verification(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            analysis = d / "analysis.json"
            analysis.write_text(json.dumps({
                "media": {"duration_seconds": 10.0},
                "frame_results": [{
                    "id": "f001", "timestamp_seconds": 5.0, "path": "frame.jpg",
                    "recon": {"prior": {"combined": [{"iso2": "PT", "p": 0.8}]}},
                }],
                "consensus": {"countries": [{"iso2": "PT", "support": 0.8}]},
                "motion": {"status": "estimated", "route_confidence": 0.5},
                "audio": {"status": "skipped"},
            }))
            args = video.argparse.Namespace(
                analysis=analysis, candidate="38.5,-9.1", out=d / "timeline.json"
            ) if hasattr(video, "argparse") else None
            if args is None:
                from argparse import Namespace
                args = Namespace(analysis=analysis, candidate="38.5,-9.1", out=d / "timeline.json")
            video.cmd_timeline(args)
            result = json.loads((d / "timeline.json").read_text())
            self.assertEqual(result["candidate"], {"lat": 38.5, "lon": -9.1})
            self.assertEqual(result["segments"][0]["prior_countries"][0]["iso2"], "PT")
            self.assertTrue(any("does not claim" in x for x in result["limitations"]))


if __name__ == "__main__":
    unittest.main()

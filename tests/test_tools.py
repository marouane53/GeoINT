#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pillow", "phonenumbers>=8.13", "lingua-language-detector>=2.0", "tzdata"]
# ///
"""Offline checks for the newer tools. Run: uv run tests/test_tools.py
Network-free: GeoNames- and brand-index-dependent checks are skipped when those caches are absent."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import geodata  # noqa: E402
import textgeo  # noqa: E402
import meta  # noqa: E402
import _streetlevel as SL  # noqa: E402

CACHE = Path.home() / ".cache" / "geoint"
HAVE_PLACES = any((CACHE / "geonames").glob("cities500*.pkl"))
HAVE_BRANDS = (CACHE / "nsi" / "brands.v1.pkl").exists()


def signals(*texts: str, places: bool = False, brands: bool = False) -> tuple[list[dict], list[dict]]:
    lines = [{"text": t, "conf": 1.0, "box": None, "pass": "typed", "src": "typed"} for t in texts]
    ev = textgeo.Board(None)
    textgeo.analyse_scripts(lines, ev)
    textgeo.analyse_letters(lines, ev)
    textgeo.analyse_domains(lines, ev)
    textgeo.analyse_postal(lines, ev)
    textgeo.analyse_currency(lines, ev)
    textgeo.analyse_words(lines, ev)
    if brands:
        textgeo.analyse_brands(lines, ev, None)
    textgeo.analyse_phones(lines, ev, [r["iso2"] for r in textgeo.shortlist(ev)] or ["US"])
    if places:
        textgeo.analyse_places(lines, ev, None, False, None)
    return ev.signals, textgeo.shortlist(ev)


class CountryDataTests(unittest.TestCase):
    def test_aliases_and_driving_side(self):
        self.assertEqual(geodata.country("Czech Republic")["iso2"], "CZ")
        self.assertEqual(geodata.country("UK")["iso2"], "GB")
        self.assertEqual(geodata.country("Ivory Coast")["iso2"], "CI")
        self.assertEqual(geodata.country("Azores")["iso2"], "PT")
        self.assertEqual(geodata.country("GB")["drive"], "left")
        self.assertEqual(geodata.country("CN")["drive"], "right")
        self.assertEqual(geodata.country("HK")["drive"], "left")
        self.assertEqual(geodata.country("GI")["drive"], "right")
        self.assertNotIn("CS", geodata.countries())

    def test_norm_folds_accents_not_scripts(self):
        self.assertEqual(geodata.norm("São Tomé"), "sao tome")
        self.assertEqual(geodata.norm("Москва"), "москва")


class TextSignalTests(unittest.TestCase):
    def top(self, *texts, **kw):
        _, short = signals(*texts, **kw)
        return short[0]["iso2"] if short else None

    def test_scripts(self):
        self.assertEqual(self.top("ถนนสุขุมวิท ซอย 11"), "TH")
        self.assertEqual(self.top("東京駅 丸の内北口"), "JP")
        self.assertEqual(self.top("서울특별시 종로구"), "KR")
        self.assertIn(self.top("台北市忠孝東路四段"), ("TW", "HK"))

    def test_unique_letters_confirm_language_and_explain_shared_ones(self):
        sigs, short = signals("Zażółć gęślą jaźń, ul. Długa 5")
        self.assertEqual(short[0]["iso2"], "PL")
        pl = [s for s in sigs if s.get("language") == "pl"]
        self.assertTrue(pl and pl[0]["lr"]["PL"] == 10.0)
        self.assertFalse(any(s.get("language") == "lt" for s in sigs))

    def test_phone_domain_currency_words(self):
        self.assertEqual(self.top("+351 21 342 0000"), "PT")
        self.assertEqual(self.top("www.example.co.uk"), "GB")
        self.assertEqual(self.top("Fiyat 25 ₺"), "TR")
        self.assertEqual(self.top("Borracharia do Zé"), "BR")
        self.assertEqual(self.top("Talho Central"), "PT")

    def test_phone_digits_are_not_postal_codes(self):
        sigs, _ = signals("+351 21 342 0000")
        self.assertFalse([s for s in sigs if s["kind"] == "postal"])

    def test_watermarks_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ocr.json"
            p.write_text(json.dumps({"items": [{"text": "© 2026 Google", "conf": 1.0}, {"text": "Rua Augusta", "conf": 1.0}]}))
            lines = textgeo.load_lines(Namespace(ocr=str(p), text=None, file=None, min_conf=0.4))
            self.assertEqual([ln["text"] for ln in lines], ["Rua Augusta"])

    @unittest.skipUnless(HAVE_BRANDS, "brand index not cached")
    def test_brand(self):
        self.assertEqual(self.top("Żabka", brands=True), "PL")
        _, short = signals("Biedronka", brands=True)
        self.assertEqual({r["iso2"] for r in short[:2]}, {"PL", "SK"})  # operates in both: a tie is the right answer

    @unittest.skipUnless((CACHE / "geonames" / "admin1CodesASCII.txt").exists(), "admin1 names not cached")
    def test_region_names_tolerate_admin_words(self):
        names = lambda cc, q: [geodata.admin1_name(cc, c) for c in geodata.admin1_codes(cc, q)]  # noqa: E731
        self.assertEqual(names("PL", "Pomeranian Voivodeship"), ["Pomerania"])
        self.assertEqual(names("RU", "Moscow Oblast"), ["Moscow Oblast"])
        self.assertIn("Munster", geodata.admin1_names("IE"))

    @unittest.skipUnless(HAVE_PLACES, "GeoNames not cached")
    def test_places_primary_names_first(self):
        rows = geodata.search("Augusta")
        self.assertEqual(rows[0]["match"], "name")
        self.assertEqual(geodata.reverse(38.57, -7.91)[0]["country"], "PT")


class MetaTests(unittest.TestCase):
    def test_offset_countries_are_dst_aware(self):
        from datetime import datetime
        summer = meta.countries_for_offset(60, datetime(2024, 7, 12, 10))
        winter = meta.countries_for_offset(60, datetime(2024, 1, 12, 10))
        self.assertIn("PT", summer)   # WEST in summer
        self.assertIn("GB", summer)   # BST
        self.assertNotIn("PT", winter)
        self.assertIn("FR", winter)   # CET in winter
        self.assertNotIn("FR", summer)

    @unittest.skipUnless(shutil.which("exiftool"), "exiftool missing")
    def test_offset_from_gps_time_and_thumbnail_crop(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "PXL_20240712_101530123.jpg"
            Image.new("RGB", (400, 300), (90, 140, 90)).save(f)
            Image.new("RGB", (160, 90)).save(Path(d) / "t.jpg")
            subprocess.run(["exiftool", "-q", "-overwrite_original", "-DateTimeOriginal=2024:07:12 10:15:30",
                            "-GPSDateStamp=2024:07:12", "-GPSTimeStamp=09:15:30", "-GPSLatitude=38.71", "-GPSLatitudeRef=N",
                            "-GPSLongitude=9.13", "-GPSLongitudeRef=W", f"-ThumbnailImage<={Path(d) / 't.jpg'}", str(f)], check=True)
            raw = meta.exiftool(f)
            dg = meta.digest(f, raw)
            self.assertAlmostEqual(dg["gps"]["lon"], -9.13, places=2)
            self.assertEqual(dg["time"]["utc_offset"], "+01:00")
            self.assertIn("PT", dg["time"]["countries_with_this_offset_on_that_date"])
            prev = meta.extract_previews(f, Path(d), dg["image"]["size"])
            self.assertTrue(any("cropped" in p.get("note", "") for p in prev))


class BoardTests(unittest.TestCase):
    def run_board(self, d: Path, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "board.py"), *args], cwd=d, capture_output=True, text=True)

    def test_ingest_caps_model_and_model_cannot_exclude(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.run_board(d, "init", "--photo", "x.jpg")
            sig = {"tool": "prior.py", "signals": [{"kind": "model", "status": "model", "level": "country", "clue": "model says PT",
                                                    "p": {"PT": 0.9, "ES": 0.05}, "n_classes": 200}]}
            (d / "prior.json").write_text(json.dumps(sig))
            r = self.run_board(d, "ingest", "prior.json")
            self.assertEqual(r.returncode, 0, r.stderr)
            b = json.loads((d / "board.json").read_text())
            pt = [e for e in b["evidence"] if e["candidate"] == "Portugal"]
            self.assertEqual(pt[0]["lr"], 3.0)          # 0.9 × 200 capped at 3
            self.assertEqual(b["candidates"]["Portugal"]["iso2"], "PT")
            again = self.run_board(d, "ingest", "prior.json")
            self.assertIn("Ingested 0 signals", again.stdout)
            r = self.run_board(d, "exclude", "Spain", "--clue", "K1", "--computed", "prior.json")
            self.assertNotEqual(r.returncode, 0)
            r = self.run_board(d, "check")
            self.assertIn("no read/computed evidence", r.stdout)


    def test_retract_report_json_and_status_cap(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.run_board(d, "init", "--photo", "x.jpg")
            r = self.run_board(d, "apply", "--kind", "driving-side", "--value", "left", "--status", "observed")
            self.assertEqual(r.returncode, 0, r.stderr)
            b = json.loads((d / "board.json").read_text())
            self.assertEqual(b["clues"]["K1"]["status"], "observed")
            self.assertTrue(all(e["lr"] <= 5.0 for e in b["evidence"]))
            r = self.run_board(d, "retract", "K1", "--why", "it was a mirrored image")
            self.assertEqual(r.returncode, 0, r.stderr)
            b = json.loads((d / "board.json").read_text())
            self.assertTrue(all(e["lr"] == 1.0 for e in b["evidence"] if e["clue"] == "K1"))
            r = self.run_board(d, "report")
            json.loads(r.stdout)  # stdout must be valid JSON (reminders go to stderr)

    def test_streetview_coverage_prior(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.run_board(d, "init", "--photo", "x.jpg")
            r = self.run_board(d, "apply", "--kind", "coverage", "--value", "streetview")
            self.assertEqual(r.returncode, 0, r.stderr)
            b = json.loads((d / "board.json").read_text())
            iso = {v.get("iso2") for v in b["candidates"].values()}
            self.assertIn("KE", iso)          # official coverage
            self.assertNotIn("MA", iso)       # Morocco: no official road coverage
            self.assertTrue(all(v.get("iso2") for v in b["candidates"].values()))

    @unittest.skipUnless(HAVE_PLACES and shutil.which("uv"), "GeoNames or uv missing")
    def test_children_of_a_country_are_offline_regions(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.run_board(d, "init", "--photo", "x.jpg")
            r = self.run_board(d, "children", "Ghana")
            self.assertEqual(r.returncode, 0, r.stderr)
            b = json.loads((d / "board.json").read_text())
            regions = {k: v for k, v in b["candidates"].items() if v["level"] == "admin1"}
            self.assertIn("Greater Accra", regions)
            self.assertTrue(regions["Greater Accra"]["center"])


class StreetViewProjectionTests(unittest.TestCase):
    def test_heading_maps_to_the_right_part_of_the_panorama(self):
        W, H = 720, 360
        eq = np.zeros((H, W, 3), dtype=np.uint8)
        pano_heading = 40.0
        # paint a red column at compass bearing 130° = pano_heading + 90° → x = W * (90/360 + 0.5)
        x = int(W * (90 / 360 + 0.5))
        eq[:, x - 3:x + 4] = (255, 0, 0)
        im = np.asarray(SL.perspective(eq, pano_heading, 130.0, 0, 60, 101, 61))
        self.assertGreater(im[30, 50, 0], 200)
        self.assertLess(np.asarray(SL.perspective(eq, pano_heading, 40.0, 0, 60, 101, 61))[30, 50, 0], 50)

    def test_tile_math(self):
        self.assertEqual(SL.tile_xy(40.7580, -73.9855), (38598, 49258))


class BenchTests(unittest.TestCase):
    def test_metrics(self):
        import importlib
        bench = importlib.import_module("bench")
        m = bench.metrics([{"error_km": 0.5, "country_true": "PT", "country_pred": "PT"},
                           {"error_km": 300, "country_true": "PT", "country_pred": "ES"}])
        self.assertEqual(m["within"]["1km"], 0.5)
        self.assertEqual(m["within"]["750km"], 1.0)
        self.assertEqual(m["country_accuracy"], 0.5)
        self.assertEqual(bench.score_km(0), 5000)


class NewToolTests(unittest.TestCase):
    """Offline checks for reveal, opendata, phone patterns, GIBS tiles, motion photos, match sheets, town sheets,
    house-number queries and the weather ranking (network calls are stubbed)."""

    def test_reveal_surfaces_dark_text(self):
        import imgprep
        from PIL import ImageDraw
        im = Image.new("RGB", (300, 120), (10, 10, 12))
        ImageDraw.Draw(im).text((20, 50), "PA 471 KT", fill=(34, 32, 30))
        panels = dict(imgprep.reveal(im, 2))
        self.assertEqual(set(panels), {"crop", "levels", "equalize", "gamma1.8", "gamma2.6", "shadows", "gray", "clahe"})
        std = lambda p: float(np.asarray(p.convert("L"), dtype=float).std())
        self.assertGreater(std(panels["levels"]), 3 * std(panels["crop"]))      # the stretch makes the faint text stand out
        g = np.asarray(im.convert("L"))
        self.assertEqual(imgprep._clahe(g).shape, g.shape)

    def test_opendata_points_from_any_geometry(self):
        import opendata
        self.assertEqual(opendata._rep_point({"type": "Point", "coordinates": [-73.9, 40.7]}), [40.7, -73.9])
        line = {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1], [2, 2]]]}
        self.assertEqual(opendata._rep_point(line), [1, 1])                    # a lane → its midpoint
        poly = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2]]]}
        self.assertEqual(opendata._rep_point(poly), [1.0, 1.0])
        fc = {"features": [{"geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {"street": "MAIN"}},
                           {"geometry": {"type": "Point", "coordinates": [3, 4]}, "properties": {"street": "MAIN"}}]}
        self.assertEqual(opendata._points_from_geojson(fc, "street", 10), {"MAIN": [2, 1], "MAIN_": [4, 3]})
        rows = [{"Latitude": "40.1", "Longitude": "-73.2", "species": "pine"}]
        self.assertEqual(opendata._points_from_rows(rows, "species", 10), {"pine": [40.1, -73.2]})
        q = opendata._soql(Namespace(eq=["lane_color=Red", "boro=3"], range=["streetwidt:40:45"], where=None))
        self.assertEqual(q, "lane_color='Red' AND boro=3 AND streetwidt between 40 and 45")
        typed = opendata._soql(Namespace(eq=["segmentid=0029245", "tree_id=180683", "alive=TRUE"], range=None, where=None),
                               {"segmentid": "text", "tree_id": "number", "alive": "checkbox"})
        self.assertEqual(typed, "segmentid='0029245' AND tree_id=180683 AND alive=true")   # a digit string in a text column stays quoted

    def test_phone_pattern_enumerates_valid_candidates(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            textgeo.enumerate_phone("6941 2?7788", "GR")
        out = buf.getvalue()
        self.assertIn("10 valid number(s)", out)
        self.assertIn("+306941207788", out)
        self.assertIn('"6941297788"', out)

    def test_gibs_daily_tiles(self):
        import tiles
        u = tiles.tile_url("gibs:2024-07-16", 22, 19, 6)
        self.assertIn("/MODIS_Terra_CorrectedReflectance_TrueColor/default/2024-07-16/GoogleMapsCompatible_Level9/6/19/22.jpg", u)
        self.assertIn("/VIIRS_SNPP_CorrectedReflectance_TrueColor/", tiles.tile_url("gibs:2024-07-16:VIIRS_SNPP_CorrectedReflectance_TrueColor", 1, 1, 3))

    def test_motion_photo_video_is_extracted(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            still = td / "still.jpg"
            Image.new("RGB", (200, 150), (90, 90, 90)).save(still, "JPEG")
            mp = td / "motion.jpg"
            mp.write_bytes(still.read_bytes() + b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 3000)
            got = meta.extract_motion(mp, td)
            self.assertEqual(len(got), 1)
            self.assertTrue((td / got[0]["file"]).read_bytes()[4:8] == b"ftyp")
            self.assertEqual(meta.extract_motion(still, td), [])

    def test_match_sheet_renders(self):
        import evidence
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            Image.new("RGB", (400, 300), "gray").save(td / "a.jpg")
            Image.new("RGB", (500, 360), "gray").save(td / "b.jpg")
            spec = {"left": {"image": "a.jpg"}, "right": {"image": "b.jpg"}, "height": 300,
                    "pairs": [{"color": "#ff5bbf", "label": "window", "a": [100, 80], "b": [120, 100]}]}
            evidence.build_match(spec, td, td / "m.jpg")
            with Image.open(td / "m.jpg") as m:
                self.assertEqual(m.size[1], 300 + 34)
                self.assertGreater(m.size[0], 400 + 500 * 300 // 360)
            # the workflow's layout: spec saved in evidence/, image paths relative to the session folder (the cwd)
            import os
            (td / "evidence").mkdir()
            for n in ("a.jpg", "b.jpg"):
                (td / n).rename(td / "evidence" / n)
            spec2 = dict(spec, left={"image": "evidence/a.jpg"}, right={"image": "evidence/b.jpg"})
            old = os.getcwd()
            os.chdir(td)
            try:
                evidence.build_match(spec2, td / "evidence", td / "evidence" / "m2.jpg")
            finally:
                os.chdir(old)
            self.assertTrue((td / "evidence" / "m2.jpg").exists())

    def test_refsheet_towns_sample_inside_each_town(self):
        import refsheet
        cands = refsheet.candidates("45.764,4.836:Lyon;45.771,4.880:Villeurbanne", "towns", Namespace(points=None))
        self.assertEqual([c["label"] for c in cands], ["Lyon", "Villeurbanne"])
        pts = refsheet.sample_towns(cands[0], Namespace(n=3, spread=500, seed=1))
        self.assertEqual(len(pts), 12)
        for p in pts:
            self.assertLess(SL.dist_m((p["lat"], p["lon"]), tuple(cands[0]["center"])), 501)

    def test_house_number_pair_query(self):
        import osm
        seen = {}
        real = osm.run
        osm.run = lambda ql, proxy, cache, **kw: seen.setdefault("ql", ql) and {"elements": []}
        try:
            pts = osm.cmd_addr(Namespace(area=None, bbox=(38.70, -9.20, 38.76, -9.10), number="214,226", within=60,
                                         proxy=None, cache=Path(tempfile.gettempdir())))
        finally:
            osm.run = real
        self.assertEqual(pts, {})
        self.assertIn('nwr["addr:housenumber"="214"](38.7,-9.2,38.76,-9.1)->.a;', seen["ql"])
        self.assertIn('nwr["addr:housenumber"="226"](around.a:60)', seen["ql"])
        self.assertIn("nwr.a(around.x:60)", seen["ql"])

    def test_regional_plate_codes_with_eras(self):
        import clues
        if not (clues.DATA / "plate_codes.json").exists():
            self.skipTest("data/plate_codes.json not built (clues.py update plate_codes)")
        place = lambda v, c=None: [m["admin1"] for m in clues.lookup_plate_code(v, c)["matches"]]
        self.assertIn("Palermo", place("PA", "IT"))
        self.assertIn("Rome", place("ROMA", "Italy"))
        self.assertIn("Rabat", place("1", "MA"))
        self.assertIn("Corse-du-Sud", place("2A", "FR"))
        self.assertTrue(any("Constantine" in p for p in place("93", "FR")))       # French Algeria before 1957
        self.assertTrue(any("Patras" in p for p in place("AA", "GR")))            # Latin look-alikes → Greek letters
        self.assertTrue(any(m["note"].startswith("[GB] registered 1 Sep") for m in clues.lookup_plate_code("51", "GB")["matches"]))
        self.assertGreaterEqual(len({m["note"][1:3] for m in clues.lookup_plate_code("BA")["matches"]}), 4)   # no country: every match

    def test_weather_ranks_sunniest_day_first(self):
        import contextlib
        import io
        import _net
        import sun
        hours = [f"2024-07-{d:02d}T{h:02d}:00" for d in (15, 16) for h in range(24)]
        cloud = [90] * 24 + [5] * 24                                            # the 15th overcast, the 16th clear
        fake = {"timezone": "Europe/Lisbon", "hourly": {"time": hours, "cloud_cover": cloud, "cloud_cover_low": cloud,
                "sunshine_duration": [0] * 24 + [3600] * 24, "precipitation": [0.2] * 24 + [0] * 24,
                "snowfall": [0] * 48, "weather_code": [3] * 24 + [0] * 24, "temperature_2m": [15] * 48}}
        real = _net.fetch_bytes
        _net.fetch_bytes = lambda *a, **k: json.dumps(fake).encode()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                sun.cmd_weather(Namespace(at=(38.72, -9.14), date=None, dates="2024-07-15:2024-07-16", hours=None, tz=None, proxy=None))
        finally:
            _net.fetch_bytes = real
        import re
        rows = [ln for ln in buf.getvalue().splitlines() if re.match(r"\d{4}-\d\d-\d\d [A-Z][a-z]{2} ", ln)]
        self.assertTrue(rows[0].startswith("2024-07-16 Tue"))
        self.assertIn("clear", rows[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)

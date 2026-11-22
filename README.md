# GeoInt

An agent skill for finding where any photo, screenshot, video, recording or Street View image was taken — the
instincts of a strong GeoGuessr player plus the discipline of an OSINT investigation. The agent leads with its own
expert read of the scene (a written gut call), then proves, sharpens or overturns it with web research and local
tools, down to street or camera level when the evidence allows. Every photo gets its own timestamped archive with
a report, evidence images and a scoreboard once the true location is known.

`SKILL.md` is the method; `references/` holds the playbooks and the worldwide clue library; `scripts/` holds the
tools.

## What's new

- **Skyline → place** (November 2026). Give it a photo with hills or mountains on the skyline and the region they
  are in: `terrain.py find` ranks every place of the region by how much of the skyline's shape the terrain there
  explains, searches decoys of the skyline as a control, and says how far to trust the best place (solid, check
  or weak) and why. `view --peaks` names the summits you see. On geotagged test photos from Yosemite, the Alps and
  the High Atlas, the place it ranked first lay within a few hundred metres of the photo's geotag wherever that
  geotag held up. Method: `references/skyline.md`.
- **Sound → place** (November 2026). `audio.py` finds where a video was recorded from aircraft passes too faint to
  hear, matched against that day's open flight tracks, and also reads thunder and the mains hum. Method:
  `references/audio.md`.
- **Better terrain**: elevation now comes from Mapterhorn (Copernicus GLO-30 plus national lidar where published).

## Install

```bash
git clone https://github.com/marouane53/GeoInt.git ~/.agents/skills/geoint   # Codex and other agents
ln -s ~/.agents/skills/geoint ~/.claude/skills/geoint                         # Claude Code
uv run ~/.agents/skills/geoint/scripts/doctor.py --network                        # check the setup
uv run ~/.agents/skills/geoint/scripts/doctor.py --models                         # optional: pre-download local models (~6.2 GB)
```

Requirements: Python 3.10+, [uv](https://docs.astral.sh/uv/), `curl`; `exiftool` and `ffmpeg` recommended. Each
script declares its own dependencies and runs with `uv run`. Per-photo archives go to `~/geoint-photos` with
timestamps in this computer's time zone; set `GEOINT_PHOTOS` / `GEOINT_TZ`, or put
`{"photos_root": "…", "timezone": "…"}` in a `config.local.json` next to `SKILL.md` (git-ignored), to change that.
The optional Gemini check of reverse-search results (`compare.py`) reads `GEMINI_API_KEY`, or the env file named by
`"gemini_env_file"` in the same `config.local.json`; without a key, results are ranked locally only.

## What's inside

| Area | Tools |
|---|---|
| Archive and scoring | `photo_session.py` (start / finish / truth / scoreboard / list), `bench.py` |
| One-command local first pass | `recon.py` → `meta.py`, `intake.py`, `ocr.py`, `textgeo.py`, `detect.py`, `calib.py`, `prior.py`, `compare.py` |
| Reverse-search results → leads | `compare.py` (every similar image from every list and crop, merged, ranked against the photo with MegaLoc, the top ones checked by Gemini one image at a time; also ranks a venue's photos), `triage.py` (a lead's page: every picture and caption, ranked, with OCR), `imgprep.py mask` (people painted out before a search) |
| Text → place | `textgeo.py` (scripts, telltale letters, language, phone numbers incl. unreadable digits, domains, postal codes, currency, regional words, ~50k brands, GeoNames places), `geodata.py`, `clues.py` (incl. 2,100 regional plate codes across ten countries, with the years each was in use) |
| Reasoning ledger | `board.py` — candidates, clues, likelihood ratios capped by evidence type, exclusion rules, rank / next / check / report |
| Street level | `pano.py` (every provider plus your own manifests: coverage, list, render, sheet), `sweep.py` (rank every panorama of whole towns with MegaLoc), `gsv.py`, `refsheet.py` (real Street View rows per candidate country, region or town), `match.py`, `baidu_pano.py` |
| Clue → short list | `opendata.py` (city open-data portals: bus lanes, street trees, hydrants… filtered to candidate points), `osm.py addr` (house numbers, alone or co-occurring) |
| Skyline → place | `terrain.py find`: every place of an area ranked by how much of the photo's skyline shape the terrain there explains, each part of the trace judged on its own, decoys of the trace (mirrored, upside down) searched as a control, and a trust level (solid / check / weak) with its reasons, overlays and a map of the scores; `check` for one place, `ridge` to trace the skyline (snow under a blue sky included), `view --peaks` to name the summits in view; terrain from Mapterhorn (Copernicus GLO-30 plus national lidar) |
| Maps and geometry | `osm.py`, `poi.py`, `gazetteer.py`, `tiles.py` (incl. NASA daily satellite passes), `sat_scan.py`, `terrain.py`, `sun.py` (incl. past weather by day), `geo.py`, `pose.py`, `evidence.py` (incl. colour-coded match sheets) |
| Sound → place and time | `audio.py`: inaudible aircraft passes made visible, each fitted for speed and closest-approach distance, then searched against that day's open ADS-B tracks and scored on the modelled Doppler curves (often 100–300 m from two passes, plus the recording's start time); thunder against lightning records (GOES GLM built in); mains hum (50/60 Hz, ENF) |
| Video → place and route | `video.py`: ffprobe metadata, fixed-cadence and scene-change keyframes, per-frame recon, cross-frame country consensus, optical-flow route estimates and integrated audio evidence |
| Route reconstruction | `route_solver.py` / `video.py route-solve`: rank alternative journeys over an OpenStreetMap road graph using timestamped frame and cluster evidence, reject contradictions and export GeoJSON |
| Local models | StreetCLIP + GeoCLIP world prior (`prior.py`), OWLv2 object crops (`detect.py`), GeoCalib camera calibration (`calib.py`), MegaLoc place recognition (`sweep.py`, `match.py`, `compare.py`, `triage.py`), DINOv2/CLIP matching; optionally Gemini Flash to check search results |

The skill works end to end without asking questions: reverse image search, web search, maps and Street View
lookups run automatically as part of the flow.

## Tests

```bash
uv run tests/test_tools.py && uv run tests/test_runtime.py && uv run tests/test_audio.py && uv run tests/test_skyline.py && uv run tests/test_video.py && uv run tests/test_route_solver.py && python3 -m unittest tests/test_photo_session.py
```

## Licences and data

Code: MIT (see `LICENSE`, which keeps Oldcircle's copyright notice for the parts that come from geo-sleuth). Data: GeoNames (CC BY 4.0), Wikipedia-derived
tables (CC BY-SA 4.0), OpenStreetMap name-suggestion-index (downloaded at runtime); flight tracks from the adsb.lol
archive (ODbL), lightning from NOAA GOES (public domain) and elevation from Mapterhorn (built from open data, sources
listed at [mapterhorn.com/attribution](https://mapterhorn.com/attribution)), all fetched at runtime; details in
`data/README.md` and `references/data-sources.md`. Model weights are downloaded from their publishers under their own
licences (StreetCLIP is non-commercial); see `references/models.md`.

## Credits

### geo-sleuth

GeoInt builds on [geo-sleuth](https://github.com/Oldcircle/geo-sleuth) by [Oldcircle](https://github.com/Oldcircle)
and reuses its code, documentation and data, released under MIT, as of geo-sleuth commit
[`4ff6c2f`](https://github.com/Oldcircle/geo-sleuth/tree/4ff6c2fc6a57e841808a45052c0dac878125df40). `LICENSE` keeps
Oldcircle's copyright notice.

| | From geo-sleuth | New in GeoInt |
|---|---|---|
| Tools (37) | 8, plus the base of 13 more | **16 new**, plus new features in those 13 |
| Method (`SKILL.md`) | 3% | **97%** |
| Instructions (`SKILL.md` + `references/`) | 18% | **82%** |
| Python code | 40% | **60%** (about 12,000 lines) |
| Data tables | 7 | **4**, incl. 2,100 regional plate codes and 250 countries and territories |

Percentages count non-blank lines that match geo-sleuth at commit `4ff6c2f`.

**From geo-sleuth:** the base toolkit for maps and geometry, sun and terrain analysis, OCR, reverse image search,
image matching, the reasoning board and evidence images, plus the China-specific data and street-level imagery.

- `geo.py`, `exif.py`, `pose.py`, `revimg.py`, `gazetteer.py`, `sat_scan.py`,
  `baidu_pano.py`, `poi.py` (apart from its user-agent string) and the helpers `_net.py` and `_browser.py`; the guides
  `aerial.md` and `world/china.md`; the data tables `calling_codes`, `cn_admin`, `cn_area_codes`, `cn_plates` and
  `territories`.
- Lightly edited: the guides `geometry.md`, `corridors.md`, `observe.md` and `verify.md`; the data tables
  `country_names` and `driving_side`; `tests/test_runtime.py`.
- Extended by GeoInt (its additions in brackets): `board.py` (`ingest`, `retract`), `clues.py` (2,100 regional plate
  codes outside China, Street View coverage), `doctor.py` (`--models`, the Gemini check), `evidence.py` (match
  sheets), `gsv.py` (`area`), `imgprep.py` (`reveal`, `mask`), `intake.py` (every list re-ranked by `compare.py`
  after each search), `match.py` (MegaLoc), `ocr.py` (automatic language detection), `osm.py` (`addr`), `sun.py`
  (`weather`), `terrain.py` (panoramic mode, the skyline search `find` / `check`, `view --peaks`, `ridge` by blueness
  and with gaps, Mapterhorn terrain), `tiles.py` (Wayback history, NASA daily passes); the guides `search.md`,
  `data-sources.md` and `sky.md`.

**New in GeoInt:**

- 16 tools: the per-photo archive and scoring (`photo_session.py`, `bench.py`), the one-command first pass
  (`recon.py`, `meta.py`, `textgeo.py`, `detect.py`, `calib.py`, `prior.py`), street-level search across providers
  and whole towns (`pano.py`, `sweep.py`, `refsheet.py`), place lists and city open data (`geodata.py`,
  `opendata.py`), reverse-search results turned into checked leads (`compare.py`, `triage.py`), and a recording's
  sound as location evidence (`audio.py`).
- The method: `SKILL.md` (rewritten; 97% of its lines are new) and the `workflow`, `country-funnel`, `streetlevel`,
  `datasets`, `models`, `audio`, `skyline` and `benchmark` guides.
- The worldwide clue library in `references/world/`: Africa and the Middle East, the Americas, Asia, Europe, Oceania,
  general clues and Street View coverage.
- The data tables `countries`, `plate_codes`, `streetview_coverage` and `text_signals`, and the tests `test_tools.py`,
  `test_audio.py`, `test_skyline.py` and `test_photo_session.py`.

### Methods GeoInt learned from

GeoInt's code for these is its own; the methods and findings are theirs.

- **[Azimut](https://github.com/OsintMeThat/azimut)** by OsintMeThat, an open-source OSINT workspace whose Find mode
  ranks the places of an area whose relief fits a photo's traced skyline. GeoInt's skyline search (`terrain.py find`
  and `check`, `references/skyline.md`) follows its method: a place scored by how much of the trace's shape it
  explains, part by part across the frame; decoy traces as a control; trust read against what the area fits by
  chance; the skyline cut at several distances for haze; Mapterhorn terrain. Azimut is AGPL-3.0, and none of its
  code is copied here.
- **[colsto](https://www.youtube.com/@colsto)**, whose video
  [I discovered a sound that leaks your exact location](https://www.youtube.com/watch?v=UytYrhYLOKI) showed how
  aircraft passes too faint to hear, matched against open ADS-B tracks, give a microphone's position. He did not
  release his code; `audio.py` and `references/audio.md` are GeoInt's own implementation of the idea.
- **[Rainbolt](https://www.youtube.com/@georainbolt)**, whose way of turning one small detail into a short list of
  places (a house number, a half-read phone number, a licence plate, the weather that day) shaped `opendata.py`,
  `osm.py addr`, `textgeo.py --phone-pattern`, the plate codes in `clues.py` and `sun.py weather`.

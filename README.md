# GeoInt

An agent skill for finding where any photo, screenshot, video frame or Street View image was taken — the
instincts of a strong GeoGuessr player plus the discipline of an OSINT investigation. The agent leads with its own
expert read of the scene (a written gut call), then proves, sharpens or overturns it with web research and local
tools, down to street or camera level when the evidence allows. Every photo gets its own timestamped archive with
a report, evidence images and a scoreboard once the true location is known.

> GeoInt builds on [geo-sleuth](https://github.com/Oldcircle/geo-sleuth) by [Oldcircle](https://github.com/Oldcircle)
> (MIT). From geo-sleuth: 9 tools, the base of 12 more that GeoInt extended, and part of the documentation and data.
> New in GeoInt: 13 tools, a rewritten method (97% new), a worldwide clue library, street-level search across 11
> providers and a per-photo archive with scoring. In all, 80% of the instructions and 47% of the Python code are
> GeoInt's own; [Credits](#credits) shows which part is which.

`SKILL.md` is the method; `references/` holds the playbooks and the worldwide clue library; `scripts/` holds the
tools.

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

## What's inside

| Area | Tools |
|---|---|
| Archive and scoring | `photo_session.py` (start / finish / truth / scoreboard / list), `bench.py` |
| One-command local first pass | `recon.py` → `meta.py`, `intake.py`, `ocr.py`, `textgeo.py`, `detect.py`, `calib.py`, `prior.py` |
| Text → place | `textgeo.py` (scripts, telltale letters, language, phone numbers incl. unreadable digits, domains, postal codes, currency, regional words, ~50k brands, GeoNames places), `geodata.py`, `clues.py` (incl. 2,100 regional plate codes across ten countries, with the years each was in use) |
| Reasoning ledger | `board.py` — candidates, clues, likelihood ratios capped by evidence type, exclusion rules, rank / next / check / report |
| Street level | `pano.py` (every provider plus your own manifests: coverage, list, render, sheet), `sweep.py` (rank every panorama of whole towns with MegaLoc), `gsv.py`, `refsheet.py` (real Street View rows per candidate country, region or town), `match.py`, `baidu_pano.py` |
| Clue → short list | `opendata.py` (city open-data portals: bus lanes, street trees, hydrants… filtered to candidate points), `osm.py addr` (house numbers, alone or co-occurring) |
| Maps and geometry | `osm.py`, `poi.py`, `gazetteer.py`, `tiles.py` (incl. NASA daily satellite passes), `sat_scan.py`, `terrain.py`, `sun.py` (incl. past weather by day), `geo.py`, `pose.py`, `evidence.py` (incl. colour-coded match sheets) |
| Local models | StreetCLIP + GeoCLIP world prior (`prior.py`), OWLv2 object crops (`detect.py`), GeoCalib camera calibration (`calib.py`), MegaLoc place recognition (`sweep.py`, `match.py`), DINOv2/CLIP matching |

Several of these tools come from geo-sleuth; [Credits](#credits) lists which ones and what GeoInt added to them.

The skill works end to end without asking questions: reverse image search, web search, maps and Street View
lookups run automatically as part of the flow.

## Credits

GeoInt reuses code, documentation and data from [geo-sleuth](https://github.com/Oldcircle/geo-sleuth) by
[Oldcircle](https://github.com/Oldcircle), released under MIT, as of geo-sleuth commit
[`4ff6c2f`](https://github.com/Oldcircle/geo-sleuth/tree/4ff6c2fc6a57e841808a45052c0dac878125df40). `LICENSE` keeps
Oldcircle's copyright notice.

| | From geo-sleuth | New in GeoInt |
|---|---|---|
| Tools (34) | 9, plus the base of 12 more | **13 new**, plus new features in those 12 |
| Method (`SKILL.md`) | 3% | **97%** |
| Instructions (`SKILL.md` + `references/`) | 20% | **80%** |
| Python code | 53% | **47%** (about 7,100 lines) |
| Data tables | 7 | **4**, incl. 2,100 regional plate codes and 250 countries and territories |

Percentages count non-blank lines that match geo-sleuth at commit `4ff6c2f`.

**From geo-sleuth:** the base toolkit for maps and geometry, sun and terrain analysis, OCR, reverse image search,
image matching, the reasoning board and evidence images, plus the China-specific data and street-level imagery.

- `geo.py`, `exif.py`, `intake.py`, `pose.py`, `revimg.py`, `gazetteer.py`, `sat_scan.py`,
  `baidu_pano.py`, `poi.py` (apart from its user-agent string) and the helpers `_net.py` and `_browser.py`; the guides
  `aerial.md` and `world/china.md`; the data tables `calling_codes`, `cn_admin`, `cn_area_codes`, `cn_plates` and
  `territories`.
- Lightly edited: the guides `geometry.md`, `corridors.md`, `observe.md` and `verify.md`; the data tables
  `country_names` and `driving_side`; `tests/test_runtime.py`.
- Extended by GeoInt (its additions in brackets): `board.py` (`ingest`, `retract`), `clues.py` (2,100 regional plate
  codes outside China, Street View coverage), `doctor.py` (`--models`), `evidence.py` (match sheets), `gsv.py`
  (`area`), `imgprep.py` (`reveal`), `match.py` (MegaLoc), `ocr.py` (automatic language detection), `osm.py`
  (`addr`), `sun.py` (`weather`), `terrain.py` (panoramic mode), `tiles.py` (Wayback history, NASA daily passes); the
  guides `search.md`, `data-sources.md` and `sky.md`.

**New in GeoInt:**

- 13 tools: the per-photo archive and scoring (`photo_session.py`, `bench.py`), the one-command first pass
  (`recon.py`, `meta.py`, `textgeo.py`, `detect.py`, `calib.py`, `prior.py`), street-level search across providers
  and whole towns (`pano.py`, `sweep.py`, `refsheet.py`), and place lists and city open data (`geodata.py`,
  `opendata.py`).
- The method: `SKILL.md` (rewritten; 97% of its lines are new) and the `workflow`, `country-funnel`, `streetlevel`,
  `datasets`, `models` and `benchmark` guides.
- The worldwide clue library in `references/world/`: Africa and the Middle East, the Americas, Asia, Europe, Oceania,
  general clues and Street View coverage.
- The data tables `countries`, `plate_codes`, `streetview_coverage` and `text_signals`, and the tests `test_tools.py`
  and `test_photo_session.py`.

## Tests

```bash
uv run tests/test_tools.py && uv run tests/test_runtime.py && python3 -m unittest tests/test_photo_session.py
```

## Licences and data

Code: MIT (see `LICENSE`, which keeps Oldcircle's copyright notice for the parts that come from geo-sleuth). Data: GeoNames (CC BY 4.0), Wikipedia-derived
tables (CC BY-SA 4.0), OpenStreetMap name-suggestion-index (downloaded at runtime); details in `data/README.md`
and `references/data-sources.md`. Model weights are downloaded from their publishers under their own licences
(StreetCLIP is non-commercial); see `references/models.md`.

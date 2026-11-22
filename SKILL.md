---
name: geoint
description: World-class photo geolocation. Use it whenever someone wants to know where a photo, screenshot, video frame or Street View/GeoGuessr image was taken — "where is this?", "geolocate this", "find the location", "which country is this", "guess the place", "where was this picture taken", GeoGuessr rounds, OSINT location checks, chronolocation ("when was this taken"), videos placed by their sound — even if they just paste an image and ask "where?". Also 这是哪 / 在哪拍的 / 图寻 / 网络迷踪, "c'est où ?", "où a été prise cette photo", "¿dónde es esto?", "وين هادي". Combines the model's own expert eye and web research with local tools: deep metadata, OCR and text-to-country lookups, learned world-prior models, object crops, camera calibration, Street View reference sheets and matching, OpenStreetMap queries, skyline search over an area against terrain, sun geometry, satellite scanning, sound (aircraft Doppler vs ADS-B tracks, thunder, mains hum), a candidate board with evidence rules, and a per-photo archive with scoring.
---

# GeoInt — find where any photo was taken

You are a geolocator with two halves. One half is a top GeoGuessr player: in seconds you read soil, vegetation,
light, road paint, poles, plates, scripts and architecture and *know* roughly where you are. The other half is an
OSINT investigator: you prove it — with text, web searches, maps, street-level imagery and geometry — and you
never claim more than the evidence supports.

Your own knowledge of the world is the most powerful instrument here, and the web is the second. The scripts
exist to make you faster and more precise: they read what you might miss, rank what you cannot scan by eye,
and measure what eyes cannot measure. They do not replace judgment. Lead with the gut, follow with proof.

`${CLAUDE_SKILL_DIR}` below is this folder (for example `~/.agents/skills/geoint`). Claude Code substitutes it;
in Codex and other agents write this folder's absolute path instead (or `export CLAUDE_SKILL_DIR=…` in the same
shell command).

## 0. Every photo gets its own archive (default workflow)

Before analysing, read `references/workflow.md` once per session. In short:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/photo_session.py start --image "/absolute/path/photo.jpg"   # prints the new folder
cd "<that folder>"                                                                              # every output lives here
```

Keep the untouched original in `original/`, fill in the `report.md` skeleton that `start` creates as you go
(gut call first), and finish with
`photo_session.py finish … --confidence high|medium|low`, which appends country/region/city to the timestamped
folder name. End the answer with the exact analysis timestamp and links to the final folder and `report.md`.

## Work autonomously

The owner wants answers, not questions. Never stop to ask the user anything — not what the photo is for, not
whose it is, not which option they prefer, not permission to search. Reverse image search, web search, maps,
Street View and every other lookup are pre-approved: run them as part of the normal flow. When something is
ambiguous, pick the most reasonable interpretation, note the assumption in `report.md`, and keep going. When a
tool fails, try the next one; when a service blocks you (CAPTCHA, rate limit), skip it and say so in the report.
Run long jobs in the background and keep working meanwhile. Always finish with one best answer, however much
or little evidence there is — confidence and radius carry the uncertainty, not a question back to the user.

## Two speeds

- **Quick** — the default for game/Street View screenshots and casual "where is this?". Gut call → recon →
  one to three decisive checks → answer. Minutes.
- **Deep** — when the user wants it pinned down ("exact spot", OSINT, a real photo they care about), or when
  quick checks disagree. The full funnel down to the camera position, with evidence images.

Escalate from quick to deep whenever the decisive checks contradict each other or the user needs more
precision than you have. Say which speed you used.

## Rules that keep you honest (each one is here because skipping it caused a wrong answer)

1. **Gut first, written down.** Commit to a ranked shortlist with percentages *before* running tools (Step 1).
   It gets you close fast, and writing it down lets you notice when tools change your mind — or when you are
   bending the evidence to fit your first idea.
2. **Never fabricate verification.** "Matched on Street View", "measured on the map", "±20 m" must correspond to
   commands you ran and files you produced in this session. Otherwise write "unverified estimate".
3. **The image wins.** EXIF, file names, captions, hints and IP labels are hypotheses. When they conflict with
   the scene, believe the scene and say why.
4. **Models rank, evidence decides.** Learned outputs (`prior.py`, CLIP rankings, detector scores, reverse-search
   "AI overviews") order your hypotheses; they never prove one. On the board they have status `model`
   (likelihood ratio ≤3, cannot exclude).
5. **List the whole category before choosing.** "Left-hand traffic", "Cyrillic", "tropical", "hill town" each
   define a set; put the whole set on the board (`board.py apply`, `children`, `ingest`) and let evidence rank
   it. Fame and population are not evidence. A region hunch means *all* its towns (`geodata.py towns`), not
   the one your gut named: in a real case the answer was the region's largest town and the gut picked #2.
6. **Exclusion needs the same standard as confirmation.** Only read text or a computed result (with a file) can
   exclude a candidate, and only over the extent the evidence actually covers (`board.py exclude --covers`).
   "I didn't see X" first needs `geo.py frame`: X may be out of frame, occluded or too small.
7. **One answer, no midpoints.** Report the top-ranked candidate; put the rest in alternatives with the test
   that would separate them. Never average two places into a point between them.
8. **Precision needs two independent constraints.** An error radius ≤100 m needs two independent constraints
   (sight lines, alignment, a solved pose, a ground-level match on ≥3 invariant features).
9. **Once you have a unique anchor, close the loop** — work from it; don't drift back to generic features.
10. **Compute bearings before naming structures** (`sun.py compass`, `geo.py bearings`): which blob on the
    satellite is "the tower" is itself a hypothesis.
11. **Use every source, without asking.** Reverse image search (Yandex, Baidu, Google Lens, Bing, TinEye), web
    search, maps and street-level imagery are all pre-approved; a lead nobody followed is a lost answer.
12. **No Google Street View is not "no street level".** Probe every provider (`pano.py coverage`), then search
    the web in the local languages for national, local and historical panorama sites, and use the browser for
    Google Maps photo spheres and place photos, Mapillary and KartaView. A site you find becomes a manifest and
    is swept like any provider (`references/streetlevel.md`). The decisive imagery is often there.
13. **Sweep towns, don't sample them.** Once a source covers a candidate town, index every panorama of it and
    rank them all (`sweep.py`); then the next town. A 15 % sample missed the answer that a full sweep put first
    of 9,203. In deep mode, don't settle on a town-level answer before the candidate towns have been swept.
14. **Judge a match one image at a time, at full size.** Contact sheets and thumbnail grids are for scanning; a
    thumbnail can neither confirm nor exclude a place. Shown a 24-tile sheet, a vision model called three wrong
    buildings "same" at 99% and missed the right one; shown one image per candidate, it picked only the right
    one. In a real case the answer sat on a sheet nobody opened for an hour.

## Step 1 — Gut call (60 seconds, your own eyes and knowledge, no tools)

Look at the full image, then deliberately at every edge and corner. Read it the way a top player does —
coarse to fine, strongest exclusions first:

- **Sun and sky**: sun toward the north at midday → southern hemisphere (outside the tropics); low winter sun;
  light quality (tropical haze, high-altitude clarity, northern low sun).
- **Biome and terrain**: vegetation type (palms, eucalyptus, pines, cork oaks, rice paddies, cacti, birch),
  soil colour (red laterite → tropics/Africa/Brazil/Australia), aridity, relief, snow.
- **Roads**: driving side (car positions, roadside signs, steering wheels), paint (yellow centre lines → the
  Americas and a few others; white edge + dashed centre; Nordic yellow), surface, curbs, bollards, guardrails,
  kilometre/hectometre markers.
- **Infrastructure**: utility poles (wood, concrete, metal, cross-arms, insulators), wiring density, street
  lights, transformers, mailboxes, bus stops.
- **Signs and text**: alphabet and diacritics, sign shapes and colours (yellow diamonds vs red triangles),
  language of shop signs, phone formats, domain names, currency.
- **Vehicles**: plate shape and colours (EU band, yellow rear plates, US 2:1), car brands and age mix,
  motorbikes, tuk-tuks, taxi/bus livery.
- **Architecture and land use**: roof materials and colours, wall render, window bars, fences and walls,
  water tanks, solar heaters, churches/mosques/temples, field patterns.
- **The image itself**: phone photo vs Street View (car, blur, watermark year, camera generation) vs drone vs
  scan of an old print; mirrored text; season and weather.

Write this block at the top of `report.md` before anything else:

```
## Gut call (before tools)
1. Portugal 55% — calçada sidewalk, whitewash with yellow trim, terracotta roofs, EU plate shape
2. Spain 25% — similar roofs; but no yellow curb paint, wrong sidewalk
3. Brazil 10% — Portuguese-style houses exist; vegetation too dry, no Mercosur plates
Region hunch: Alentejo (whitewash + yellow trim, flat dry land)
Would change my mind: a Spanish-language sign, yellow curb paint, Mercosur plate
```

Be honest with the percentages: they are scored later against the truth (`photo_session.py truth`), so your
gut becomes calibrated over time. If nothing comes, write that — it tells you to lean on text and search.

## Step 2 — Recon: one local command

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/recon.py original/<photo> --out-dir recon     # ~30–60 s after the first run
```

It runs, in parallel: deep metadata (`meta.py`), edge/corner crops, OCR with automatic language detection and
reverse image search on Yandex and Baidu (`intake.py`), text → country signals (`textgeo.py`), object crops
(`detect.py`), camera calibration (`calib.py`) and the learned world prior (`prior.py`); then `compare.py` merges
every similar image the reverse search returned, ranks them against the photo with MegaLoc and has Gemini check
the top 24 one image at a time (`compare/compare.md`). Read `recon/recon.md`, then open the crops it lists —
`intake/edges/`, `detect/detect_sheet.jpg`, `intake/ocr.png` — at full size.

Then reconcile with your gut call:
- **They agree** → good; go to the decisive checks for that candidate.
- **They disagree** → find out why before going on: an OCR misread, an unusual scene that fools the model, a
  feature you misjudged. Write the reason down; either side can be wrong.
- **The model is sure, you are not** → when `prior.py` puts 70% or more on one country, or on one cluster of
  neighbours (a country and its dependencies), and your gut named another, spend the first decisive check on the
  model's choice. When people fill the frame, run the prior on a crop of the architecture or landscape too
  (`imgprep.py zoom original/<photo> --box … --scale 1 --out arch.jpg`, then `prior.py arch.jpg --out-dir
  prior_arch`): in a real case the whole photo gave the right country 30%; the building crop gave it and its
  neighbours 88% (GeoCLIP alone 86%, its point 35 km from the answer), while the gut's other country absorbed 40
  minutes.

What the pieces are worth (details in `references/models.md`):
- **Metadata**: GPS is a strong lead but must be confirmed by the scene; the UTC offset narrows countries for
  that date; filename patterns reveal the app (WhatsApp, WeChat, KakaoTalk, stock agencies with searchable IDs);
  embedded previews can show the uncropped original (a RAW file's full-size preview often shows past the edit's
  crop); a motion photo's embedded video (`meta.py --out-dir` extracts it) adds frames and angles.
- **Text**: the most valuable evidence in most photos. Apple's OCR reads ~30 languages but not Greek, Hebrew,
  Georgian, Armenian, Indic scripts, Sinhala, Khmer, Lao, Burmese or Ethiopic — read those yourself from the
  crops and run `textgeo.py --text "…"`.
- **World prior**: often right on country for ordinary outdoor scenes; useless on indoor scenes, close-ups,
  museum objects and old photos — it says so itself (`UNINFORMATIVE`) and then writes no board signal.
- **Calibration**: field of view, pitch, roll and horizon for the geometry tools; trust it only when recon does
  not mark it UNRELIABLE and the red horizon line in `calib.jpg` looks right.

Skip what is obviously pointless (`--no-ml` for a photo of a readable street sign; `--skip calib` for a
close-up). For video: extract keyframes first (`ffmpeg -i clip.mp4 -vf "select='gt(scene,0.3)'" -vsync vfr
frames/%03d.jpg`) and run recon on the most informative frames, or use the complete local pipeline:
`uv run ${CLAUDE_SKILL_DIR}/scripts/video.py analyze clip.mp4 --out-dir video/`.
The pipeline extracts fixed-cadence and scene-change frames, runs metadata and recon on the representatives,
fuses cross-frame country/GeoCLIP evidence, estimates motion by optical flow and analyzes the soundtrack.
Use `video.py timeline video/analysis.json --candidate lat,lon` to preserve segment timestamps and attach a
candidate; use `video.py route` for explicit frame sequences. For moving footage, use
`video.py route-solve video/analysis.json --candidate lat,lon --out-dir route/` to rank complete road-network
journeys instead of averaging frame guesses. Supply `--bbox S,W,N,E` when the starting point is unknown. For
timestamped frame constraints, use `--evidence evidence.json` (`ll`, `timestamp_seconds`, `radius_m`,
`heading`, and `frame` fields). Judge recurring fixed structure across frames,
not a single learned-prior result. Also look at the soundtrack directly (`audio.py look` / `events`): a
passing aircraft, thunder or the mains hum can place a recording whose frames cannot
(Special situations › Video or audio).

## Step 3 — Read everything and search the web (your biggest lever)

Every legible string is a search query. Use your web-search tool aggressively and in the local language:

- **Exact text in quotes**: shop names, street names, slogans, notices, bus destinations, school names, licence
  numbers on taxis, construction-permit boards. Add the candidate town or country to disambiguate.
- **Phone numbers, URLs, e-mail domains**: `textgeo.py` resolves them to country and often city; then search the
  number or domain to find the business address. A digit you can't read: `textgeo.py --phone-pattern "6941 2?7788"
  --country GR` lists the valid candidates (a landline's area code still gives the city); search each in quotes.
- **House numbers**: a readable number is a searchable key. `osm.py addr --area <town> --number 145`; two
  numbers seen close together on one street → `--number 214,226 --within 60` collapses a whole region to a few
  blocks. OSM lacks addresses in many places: then search `"145" "<town>"` on property portals or the address register.
- **Dark or flat details** (a plate in shadow, a sign at dusk): `imgprep.py reveal photo.jpg --box …` stretches
  the crop eight ways (levels, equalize, shadow gammas, CLAHE); read the sheet before calling it illegible.
- **Brands and chains**: `textgeo.py` says where a brand operates (incl. US states); then search the store
  locator or "<brand> <town>" for branches.
- **Route numbers and road names**: `osm.py route` / `osm.py find` turn them into corridors; search the name.
- **Distinctive objects** (a statue, a church, a mural, an odd sign): describe them in words and search images
  in the local language, with and without the suspected town (`references/search.md`).
- **Place names in the text**: `textgeo.py` lists every GeoNames match; `textgeo.py --country XX --deep` searches
  the full national gazetteer (villages, hills, rivers).
- **Facts to confirm a hunch**: "do <country> use yellow plates", "<country> bollard", "<region> roof tiles" —
  check current sources before relying on a memorised meta; conventions change.

**Reverse image search** (pre-approved; run it every time unless the answer is already certain):
- **People in the frame → paint them out first.** When a person or an outfit fills much of the photo, the
  engines (Lens included) mostly return outfits and poses. Box the person tightly (a few boxes for arms or a hat are
  fine; keep steps, railings and walls visible), then search the masked copy:
  `imgprep.py mask original/<photo> --box x0,y0,x1,y1 --out masked.jpg` → `intake.py masked.jpg --out-dir rev_masked`,
  and upload `masked.jpg` to Lens. Do it before the first manual search, not after half an hour of outfits.
- Scripted: `recon.py` already ran Yandex + Baidu on the original plus flips and colour variants
  (`recon/intake/rev/`, summary in `recon.md`); re-run on a tight crop of a distinctive object with
  `uv run ${CLAUDE_SKILL_DIR}/scripts/intake.py original/<photo> --box x0,y0,x1,y1 --out-dir rev_crop`. Strong for
  buildings, streets and anything posted in China. Every `intake.py` run ends by re-running `compare.py` over
  **all** the lists saved in the session, so `compare/compare.md` always has every result merged and ranked — the
  answer is often in a crop's list, not the whole photo's.
- **Google Lens** through the owner's own browser, when a browser- or computer-control tool for their Chrome is
  available (Claude in Chrome, Codex computer use): open `https://lens.google.com/`, upload the original or a tight crop with the
  browser's file-upload tool, then save the page text and a screenshot into `rev/`. Read "exact matches /
  pages that include this image" first — that is provenance; treat the AI overview as a candidate only. If
  Google shows a CAPTCHA, skip Lens for this photo and continue (note it in the report). For an image that is
  already public on the web, `https://lens.google.com/uploadbyurl?url=<image URL>` needs no upload.
- Bing Visual Search and TinEye by hand in the owner's browser are worth a try when the others fail (TinEye is
  best for finding the original, uncropped or older copies of a photo).
- A hit is the start of a trail: open the source page, read the caption and the rest of the album, then verify
  the place on the map. `triage.py "<page URL>"` downloads every picture and caption on the page, ranks them
  against the photo and reads text in the best ones (a sign, an address, the album's hotel). A "visually
  similar" result is a clue, not a location.
- **Read the whole list before chasing the top hit — compare.py reads it for you.** Visual matches are ranked by
  look, not by place: the first result is often a lookalike, and the right place can sit anywhere in the first
  80, or only in a crop's list. Open Gemini's matches in `compare/compare.md` first (a flag is a lead: confirm it on the
  full-size image and its page), then its MegaLoc table. Write down every place the results name, then open first
  the ones that two independent results name (a place's own page and its builder's or supplier's project page;
  two engines) and the ones whose name matches text, a logo or even one letter visible in the photo. Check each
  against two or three fixed features before going deep on one (`references/search.md` §1).
- **Checking a candidate venue** (a hotel, restaurant, gym, shop): its official gallery shows the main building;
  side wings, terraces and back rooms appear in guests' photos. Gather many into one folder (official gallery,
  Google Maps photos in the browser, Instagram/Facebook, travel blogs) and rank them all:
  `compare.py --images web/<venue> --no-lists` (→ `compare/images_<venue>/compare.md`; `--check 48` to have Gemini
  check more). A venue checked on three photos of its main house is not excluded; in a real case that dropped the
  answer for 20 more minutes. Near-identical venues (chain fit-outs, climbing walls, hotel rooms) fool Gemini
  too: in a test it called three photos of a lookalike "same" at 85–95%, so confirm on fixed features.

## Step 4 — Country (when Step 1–3 did not already settle it)

Follow `references/country-funnel.md`. The short version:

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/board.py init --photo original/<photo>
uv run ${CLAUDE_SKILL_DIR}/scripts/board.py apply --kind driving-side --value left|right      # whole category on the board
uv run ${CLAUDE_SKILL_DIR}/scripts/board.py ingest recon/textgeo.json                         # after checking the signals
uv run ${CLAUDE_SKILL_DIR}/scripts/board.py ingest recon/prior/prior.json                     # model status: ranks only
uv run ${CLAUDE_SKILL_DIR}/scripts/board.py clue "red laterite soil, eucalyptus" --kind vegetation --status observed
uv run ${CLAUDE_SKILL_DIR}/scripts/board.py evidence --clue K5 --for Kenya:3 --for Uganda:3 --against "South Africa":0.5 --why "…"
uv run ${CLAUDE_SKILL_DIR}/scripts/board.py rank && uv run ${CLAUDE_SKILL_DIR}/scripts/board.py next
```

In quick mode the board is optional once read evidence (text, plates, phone, brand) settles the country; use
it whenever the country is still open, and always in deep mode. A clue recorded by mistake can be withdrawn with
`board.py retract K3 --why "…"`; when the driving side rests on one blurry vehicle, add `--status observed` to
`apply` (caps it at 5 instead of 20).

To separate two or three look-alike countries, compare against reality instead of memory:

```bash
uv run ${CLAUDE_SKILL_DIR}/scripts/refsheet.py countries KE,UG,TZ --n 6 --out ref_ke_ug_tz.jpg    # real Street View rows per country
uv run ${CLAUDE_SKILL_DIR}/scripts/refsheet.py countries US --n 6 --pitch -10 --side right         # tilt down for road paint/curbs
```

and read the matching regional file in `references/world/` (Europe, Americas, Asia, Oceania, Africa & Middle
East, China, plus `streetview.md` for game imagery). Record what decided it as evidence with a comparison file.

## Step 5 — Region, town, neighbourhood

- Regional codes: plates (`clues.py lookup plate …` for China; `clues.py lookup plate-code PA --country IT` for
  Italy, Spain, France, Germany, Austria, Switzerland, Greece, Turkey, the UK and Morocco; the world files for
  others), area codes and phone
  geocoding (`textgeo.py`), postal codes, road-number systems, bilingual/regional languages.
- Lists, then evidence: `board.py children "<country or region>"` adds every subdivision; rank them with
  evidence. `refsheet.py regions "ES:Andalusia,ES:Galicia"` compares regions on the ground; `prior.py` lists its
  favourite regions (a hint, not a decision).
- Towns: `geodata.py towns <CC> --admin1 "<region>" --json > towns.json` lists every sizable town; compare their
  fabric at one scale (`tiles.py sheet --points towns.json --zoom 15 --size 640`: grid or medina, street width,
  river, relief) and keep those that can produce the photo; then `pano.py coverage` on each survivor.
- Climate, vegetation, terrain and architecture gradients inside the country (`references/world/*.md`).
- Shops, schools, churches and other named places in the text → `poi.py "<name>" --city <town>` / web search.
- Neighbouring towns look alike? Street furniture is bought per city (hydrants, bins, street-name signs, lamp
  posts): `refsheet.py towns --points towns.json --pitch -12 --side right` puts one row per town side by side.
- **Turn the rarest clue you are sure of into a filter** (`references/datasets.md`): a coloured bus lane, a less
  common street-tree species, a posted speed limit, a building height beside a stop or a school. City open data
  (`opendata.py`) and OSM tags (`osm.py find/near/addr`) collapse a city to a short list of points; check them
  on satellite and street level with three or four deal-breakers in mind. Filter on the one attribute you are
  sure of and rank by the rest: one missing tag or guessed attribute drops the answer.

## Step 6 — Pinpoint (deep mode)

Pick the branch that fits what you have (details in the references):

| What you have | Approach | Read |
|---|---|---|
| A unique anchor (building, statue, tower) | sight lines, alignment, tangents, camera height; `geo.py`, `pose.py` | `geometry.md` |
| Mountains / skyline | trace it (`terrain.py ridge`), then `terrain.py find` over the region: places ranked by how much of the skyline's shape each explains, with decoys and a trust level; `check` one place, `view --peaks --overlay` to name the summits; a far skyline gives a sight line, so add a second constraint | `skyline.md`, `geometry.md` 7.4, `corridors.md` 4.3 |
| Shadows, sun in frame, time known | `sun.py locate/when/facing/compass` | `sky.md` |
| A date (EXIF, a known day) or "which day?" | `sun.py weather` (past cloud/sun/rain/snow, rank days); `tiles.py --source gibs:<date>` (that day's satellite pass: clouds, snow, smoke) | `sky.md` §6 |
| A distinctive public asset in a city with open data | `opendata.py search/columns/get --points` → `tiles.py sheet` / `gsv.py sheet` | `datasets.md` |
| Readable house numbers | `osm.py addr --number 214,226 --within 60` | `datasets.md` |
| Route number, railway, power line, big river | `osm.py route/crossings/along/near/intersect` | `corridors.md` |
| Street layout, no anchor | `osm.py street-scan` → `tiles.py sheet` → street level | `corridors.md` |
| Scene elements only (field + road + coast) | `sat_scan.py grid --query …` ranks satellite cells; look at the top 20–30 | `search.md` |
| ≥4 known points in frame | `pose.py solve` (camera position, heading, height) with `calib.py` FOV/pitch as the start | `geometry.md` §10 |
| "X isn't in the photo" | `geo.py frame` before excluding | `geometry.md` §11 |
| Candidate streets to check | `gsv.py sheet --points … --along` / `--headings`, `pano.py sheet` (any provider); `match.py rank` (MegaLoc) orders them | `verify.md` |
| One or a few candidate towns, no anchor | `pano.py list` every source covering them → `sweep.py index` → `sweep.py rank --index t1 t2 …` → clusters, top sheet → `sweep.py view` | `streetlevel.md` |
| No Google coverage in the country | `pano.py coverage`, local/historical panorama sites (web search in local languages), Google Maps photo spheres and place photos in the browser, a manifest | `streetlevel.md` §1–3 |
| Old photo (years to decades) | the oldest street-level imagery of each town + `tiles.py wayback`; sweep (MegaLoc copes with decades); verify structure, not inliers | `streetlevel.md` §5 |
| Motorway / expressway scene | corridor from `osm.py along --line '["highway"="motorway"]' --bbox …` (or the `ref`), then `gsv.py sheet --points … --along --offset 180` for the other carriageway; match barriers, reflectors, dash pattern | `corridors.md` |
| Mainland China | Baidu panoramas (`baidu_pano.py`), GCJ-02 coordinates (`geo.py convert`) | `world/china.md`, `data-sources.md` |
| Plane window or drone | aerial branch | `aerial.md` |
| A video or recording with sound: an aircraft passing, thunder, mains hum | `audio.py look/events` → `fit` each pass → `adsb` → `locate`; `lightning` → `thunder`; `hum` | `audio.md` |

Town known (or a short list of towns), street unknown: sweep it. List every panorama of each town from every
source that covers it (`pano.py list --provider google|apple|yandex|… --near <town> --radius <m> --out t.jsonl`,
or a manifest you wrote for a local site you found), index all of them (`sweep.py index --manifest t.jsonl`, cached and
reused), rank (`sweep.py rank --index t --query original/<photo>`), then read `clusters.json` and the top sheets.
A cluster of top hits in one neighbourhood is the strongest signal; reproduce the view with `sweep.py view`.
For a small Google-only area the older route still works: `gsv.py area … --out panos.json` → `match.py rank
--panos panos.json --headings 0,90,180,270 --render gsv --top 10 --sheet m.jpg`.

Street level: `gsv.py near <lat,lon> --radius 50` gives a Google panorama, its capture date, history and address;
`pano.py render/sheet` render any provider's panorama in any direction. Compare **invariant** features
(building outlines, window spacing, pole positions, curbs, ridgelines), not cars, signs or foliage. ≥2 unique
features for road level, ≥3 plus two constraints for building level. Across years, see `streetlevel.md` §5:
expect added floors, new render and grown trees, and never decide on local-feature inlier counts.

## Step 7 — Verify, falsify, red-team

Before you call it (`references/verify.md`):
- Write 2–3 falsification conditions *before* looking at the candidate ("give up if the road bends left").
- Bearing self-check: left/right order, near/far occlusion, sun direction and shadows must all fit the
  candidate camera position.
- Redo the decisive step once with a different crop, query or tool; results tens of km apart lower the tier.
- For hard or high-stakes cases, if you can start a sub-agent, give it only the photo and your answer and ask
  it to find the strongest reason the answer is wrong (a red team). Fix or downgrade what it finds.
- `board.py check` then `board.py report --merge result.json`; build the evidence image
  (`evidence.py spec.json --out evidence.jpg`: satellite + camera wedge + comparison panels). For a
  street-level match, add the feature-by-feature proof: `evidence.py match.json --out match.jpg --match` draws
  colour-coded lines between the same balconies, stripes and roof lines in the photo and the reference.

## Step 8 — Answer and archive

Confidence per level (report the finest level rated medium or better):

| Level | High | Medium | Low |
|---|---|---|---|
| Country | read text/plates/phone, or a confirmed anchor | several independent visual clues agree | gut or a single clue |
| Region / city | regional code, named place, or reference sheets that clearly separate | consistent clues, alternatives unchecked | hunch |
| Street | ground-level match on ≥2 unique features | layout fits + 1 feature | satellite layout only |
| Building / camera point | two independent constraints + ≥3-feature match | one of the two | estimate |

Save `report.md` (gut call, decisive clues with files, commands run, alternatives with separating tests,
excluded candidates and why, unresolved clues, confidence and radius, source links), then:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/photo_session.py finish "$PWD" --country "…" --region "…" --city "…" \
  --latitude … --longitude … --confidence high|medium|low
```

**Where to put the pin when only a city or region is settled** (quick mode often ends here): put it on the most
likely *specific* area inside the settled unit, never on a midpoint between rival candidates — the
best-supported neighbourhood or road type that fits the scene (old centre vs suburb vs farmland), snapped to a
real road or covered street (`gsv.py near`, `geodata.py reverse --osm` for the neighbourhood name). If nothing
inside the unit is better supported, use the unit's population-weighted centre (`geodata.py regions <CC>` gives
one per region) and state the radius honestly ("city level, ~10 km"). The no-midpoint rule is about rival
candidates; a centre *within* the one settled unit is fine.

Lead the answer with the owner's format (see `references/workflow.md`): bold country/region/city line, bold
coordinates line (DMS and decimal), a confidence/accuracy line, the decisive clues, the evidence image, and
finally the exact analysis timestamp with links to the renamed folder and its `report.md`. When the real
location becomes known, record it: `photo_session.py truth <folder> --latitude … --longitude … --country …`.

## Special situations

- **Street View / GeoGuessr screenshots**: `references/world/streetview.md` (camera generation, car, coverage,
  © year — the display year, not the capture year; `gsv.py near` gives real capture dates). Coverage itself is a
  prior: official imagery follows roads in covered countries (`board.py apply --kind coverage --value streetview`,
  default weight when the © Google watermark is visible; add `--status observed` when it only looks like Street View).
  Skip `calib.py`: the camera is level, the horizon is the image's centre row unless the view was pitched, and
  the field of view is the viewer's setting (often ~90°). The smeared patch at the bottom of the frame is the
  blurred camera car, not a shadow — its colour and roof gear are themselves clues (`streetview.md`).
- **Historical photos, scans, postcards**: the world prior is useless; lean on architecture, vehicles, text,
  archives, reverse search and `references/search.md`; dating clues matter as much as place. Old plates are
  often regional where today's are not (Italy to 1994, Spain to 2000, France to 2009): read the code with
  `imgprep.py reveal`, then `clues.py lookup plate-code <code>` gives the province and the years it was used.
  For the street: the oldest street-level imagery of each candidate town (local archives often beat Google),
  `tiles.py wayback`, historical aerials (`data-sources.md`), a full `sweep.py` (MegaLoc found a 1990s print
  among 9,203 panoramas from 2013), and the across-years checks in `references/streetlevel.md` §5. A
  black-and-white photo goes into the sweep as it is: colourising measured no better than grayscale (read the
  top 50 and the clusters), and a colourised copy's colours are guesses, never evidence.
- **Indoor, close-up, product shots**: text, plugs and sockets, packaging, provenance; reverse search; metadata.
- **Video or audio**: the soundtrack is a second photo (`references/audio.md`). Look at it as a spectrogram before
  listening (`audio.py look`, `events`): most aircraft passes are inaudible but plain there. Two propeller or
  helicopter passes a known time apart, plus the silence between them, single out one spot among every flight of
  the day: fit each curve (speed and closest-approach distance), pull that day's ADS-B tracks for the area
  (`audio.py adsb`, open archive), and `audio.py locate` (often 100–300 m, and the recording's start time). One pass
  with a known clock gives the flight and a short segment. Thunder against lightning records gives the town
  (`lightning`, `thunder`); the hum gives the grid (50/60 Hz) and, with a grid-frequency record, the time (`hum`).
  Check the area has low non-jet traffic in the tracks first; volunteer ADS-B coverage is thin in parts of the
  world. Speech, calls to prayer, bells, sirens and birds are clues like text in a photo.
- **Puzzles (图寻 / 网络迷踪 style)**: setters pick obscure places — keep priors uniform, distrust fame, and
  read `references/world/china.md` for China.
- **No street-level coverage**: first make sure — `pano.py coverage`, a web search for local and historical
  panorama sites, Google Maps photo spheres in the browser (`references/streetlevel.md` §1). Then satellite scan
  + terrain + ground photos from news/encyclopedias/Commons (`references/verify.md` §4).

## Toolbox (all `uv run ${CLAUDE_SKILL_DIR}/scripts/<name>`)

| Script | Use it for |
|---|---|
| `photo_session.py` (python3) | archive start/finish, `truth`, `scoreboard`, `list` |
| `recon.py` | the whole local first pass in one command |
| `meta.py` | metadata, UTC-offset countries, filename/platform hints, embedded previews, motion-photo video, AI-provenance flags |
| `intake.py`, `ocr.py`, `imgprep.py`, `exif.py` | crops, variants, OCR (auto language), zooms, `reveal` (read text lost in shadow), `mask` (paint people out before a reverse search); Yandex + Baidu reverse image search |
| `compare.py` | every reverse-search result from every list, merged, ranked against the photo with MegaLoc, the top ones checked by Gemini one image at a time; also ranks a folder of a venue's photos (`--images`) |
| `triage.py` | a lead's page: every picture and caption on it, ranked against the photo, with OCR of the best ones |
| `textgeo.py` | text → scripts, letters, language, phones (`--phone-pattern` for unreadable digits), domains, postal codes, currency, regional words, brands, places |
| `opendata.py` | city open-data portals: find a dataset (bus lanes, street trees, hydrants…), filter it, export candidate points |
| `geodata.py` | country facts, aliases, GeoNames search/reverse/sample, the biggest towns of a region (`towns`), national gazetteers |
| `prior.py` | StreetCLIP + GeoCLIP world prior with self-assessment (ranking only) |
| `detect.py` | numbered crops of signs, plates, poles, bollards, markings, vehicles |
| `calib.py` | field of view, pitch, roll, horizon with uncertainty |
| `board.py` | candidates, clues, evidence (capped by status), exclusions, `ingest`, rank/next/check/report |
| `clues.py` | lookups: calling codes, driving side, territories, China plates/area codes/admin |
| `refsheet.py` | Street View sample rows per candidate country/region/town, side by side |
| `pano.py` | street-level imagery from every scriptable provider (Google, Apple, Bing, Yandex, Naver, Kakao, Mapy, Já, KartaView, Panoramax, Mapillary) and any source you find yourself (a manifest): coverage, list, render, sheet |
| `sweep.py` | city-scale search: index every panorama of one or more towns, rank them for the photo with MegaLoc, clusters, reproduce the view |
| `gsv.py`, `baidu_pano.py`, `match.py` | Google and Baidu discovery and rendering, comparison sheets, similarity ranking (MegaLoc / DINOv2 / CLIP) |
| `osm.py`, `poi.py`, `gazetteer.py` | OpenStreetMap queries (incl. `addr` house numbers), corridors, place names, admin areas |
| `tiles.py`, `sat_scan.py`, `evidence.py` | satellite/map mosaics (Google, hybrid with street names, Esri, Bing, Esri Wayback history since 2014, Sentinel-2 by year, NASA GIBS daily passes), town-fabric sheets, CLIP-ranked satellite scans, evidence images and colour-coded match sheets |
| `terrain.py`, `sun.py`, `geo.py`, `pose.py` | skylines (`ridge` trace, `find` an area, `check` a place, `view --peaks`), sun/shadows, past weather by day, bearings/frames/conversions, camera pose |
| `audio.py` | a recording's sound: whitened spectrograms, Doppler-pass detection and fitting, ADS-B tracks (adsb.lol), aircraft search scored on the modelled curves, GOES lightning and thunder search, mains hum and ENF |
| `video.py` | metadata, fixed-cadence and scene-change keyframes, per-frame recon, cross-frame country/cluster consensus, optical-flow route estimates and integrated audio events |
| `route_solver.py` | graph-based reconstruction of complete journeys from timestamped evidence, alternative routes, contradiction reports and GeoJSON |
| `bench.py` | benchmarks: Street View test sets, scoring predictions and sessions |
| `doctor.py` | setup check (`--network`, `--models`) |

## Budget and stopping

- Quick mode: stop when one decisive check confirms the gut call at the level the user asked for.
- Reverse search: three different queries/crops with nothing useful in `compare.md` → stop and return to the image.
- A lead that has not confirmed in about 15 minutes: go back to every result list you saved (all engines, all
  crops) and the places they name before widening the search; the answer is often already in them. Re-run
  `compare.py --query <tight crop of the most distinctive part>` (it re-ranks every list against that crop too)
  and open what it puts on top.
- Don't scan candidates one by one while they are still inseparable: run a discriminating test first
  (`board.py next` says which).
- Look only at the machine's top results (top 30 satellite cells, the top 15–40 sweep results and their
  clusters) before widening — but sweep a town completely before calling it a miss, then move to the next town.
- If you cannot pin a point, say which level is settled and exactly what information is missing.

## Runtime notes

- Python ≥3.10, `uv`, `curl`; `exiftool` and `ffmpeg` recommended. Check with `doctor.py --network`;
  `doctor.py --models` pre-downloads the local models (~6.2 GB in `~/.cache/huggingface` and `~/.cache/torch`; MegaLoc is 1.7 GB of it).
- Shared data caches live in `~/.cache/geoint` (GeoNames 14 MB, brand index 12 MB, panoramas); per-photo
  caches in `.geo-cache/` inside the session folder.
- `compare.py` and `triage.py --check` send the photo and candidate thumbnails to Google's Gemini API when a key
  is set (`GEMINI_API_KEY`, or `gemini_env_file` in `config.local.json` naming an env file that holds it); without
  one they rank locally with MegaLoc only. `doctor.py --network` checks the key and model without spending tokens.
- Google retired the old Street View lookup endpoint and blocks anonymous thumbnails; `gsv.py` now discovers
  panoramas through Maps coverage tiles and renders views locally. If it breaks again, update the
  `streetlevel` package first.
- macOS has no `timeout` command; zsh does not word-split `$var` (use `bash -c` in loops). Long Overpass and
  terrain scans: run them in the background, and run Overpass queries one at a time — public servers throttle
  parallel queries, and a "0 results" from a busy server is not evidence of absence (rerun before concluding).

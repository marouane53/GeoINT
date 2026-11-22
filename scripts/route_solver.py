#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Graph-based route reconstruction for moving video evidence.

The solver combines timestamped frame evidence with an OpenStreetMap road graph,
then searches alternative journeys instead of averaging individual locations.

  route_solver.py solve analysis.json --bbox S,W,N,E --out-dir route/
  route_solver.py solve analysis.json --candidate 38.723,-9.140 --candidate 38.726,-9.142
  route_solver.py solve analysis.json --bbox ... --evidence frames.json --speed 13

Evidence JSON is an array or {"points":[...]} with optional fields:
  timestamp_seconds, lat, lon, radius_m, heading, landmarks[], frame

The road graph is a hypothesis, not proof: OSM coverage, editable roads,
footways and recording gaps can make routes wrong. The output explicitly reports
rejected branches and independent verification requirements.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import subprocess
import sys
from pathlib import Path

EARTH_KM = 6371.0088
EARTH_M = EARTH_KM * 1000.0


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dp = lat2 - lat1
    dl = lon2 - lon1
    h = math.sin(dp / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(h)))


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dl = lon2 - lon1
    y = math.sin(dl) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def angle_diff(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def bbox_around(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 0.05))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def merge_bbox(boxes: list[tuple[float, float, float, float]], pad_m: float = 0.0) -> tuple[float, float, float, float]:
    s = min(b[0] for b in boxes)
    w = min(b[1] for b in boxes)
    n = max(b[2] for b in boxes)
    e = max(b[3] for b in boxes)
    lat = (s + n) / 2
    pad_lat = pad_m / 111320.0
    pad_lon = pad_m / (111320.0 * max(math.cos(math.radians(lat)), 0.05))
    return s - pad_lat, w - pad_lon, n + pad_lat, e + pad_lon


def fetch_overpass(bbox: tuple[float, float, float, float], proxy: str | None = None) -> dict:
    s, w, n, e = bbox
    ql = (
        '[out:json][timeout:180];'
        f'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|'
        f'living_street|service|track|road)"]({s},{w},{n},{e});'
        'out geom tags;'
    )
    cache = Path(os.environ.get("GEOINT_CACHE", Path.home() / ".cache" / "geoint")).expanduser() / "routes"
    cache.mkdir(parents=True, exist_ok=True)
    key = cache / (hashlib.sha1(ql.encode()).hexdigest()[:16] + ".json")
    if key.exists():
        return json.loads(key.read_text(encoding="utf-8"))
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    last = ""
    for endpoint in endpoints:
        cmd = ["curl", "-q", "-sS", "-L", "-m", "210", "--data-urlencode", f"data={ql}", endpoint]
        if proxy:
            cmd.extend(["--proxy", proxy])
        r = subprocess.run(cmd, capture_output=True, text=True)
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            last = r.stderr[-300:] or r.stdout[-300:]
            continue
        if data.get("elements"):
            key.write_text(json.dumps(data), encoding="utf-8")
            return data
        last = str(data.get("remark", ""))[-300:]
    raise SystemExit("No road data returned by Overpass. Try a smaller bbox or set --proxy; " + last)


def is_oneway(tags: dict) -> int:
    """Return 1, -1 or 0 for forward-only, reverse-only or bidirectional."""
    value = str(tags.get("oneway", "")).lower()
    if value in ("yes", "true", "1"):
        return 1
    if value in ("-1", "reverse"):
        return -1
    junction = str(tags.get("junction", "")).lower()
    if tags.get("highway") in ("motorway", "motorway_link") or junction == "roundabout":
        if value not in ("no", "false", "0"):
            return 1
    return 0


def build_graph(data: dict) -> tuple[dict[int, tuple[float, float]], dict[int, list[tuple[int, float, float]]]]:
    """Build a compact undirected/directed node graph."""
    nodes: dict[int, tuple[float, float]] = {}
    adjacency: dict[int, list[tuple[int, float, float]]] = {}
    for way in data.get("elements", []):
        if way.get("type") != "way":
            continue
        geom = way.get("geometry") or []
        ids = way.get("nodes") or []
        if len(geom) < 2:
            continue
        way_nodes = []
        for i, point in enumerate(geom):
            lon = float(point["lon"])
            lat = float(point["lat"])
            if ids and i < len(ids):
                node_id = int(ids[i])
            else:
                payload = f"{lat:.7f},{lon:.7f}".encode()
                node_id = -int(hashlib.sha1(payload).hexdigest()[:15], 16)
            nodes[node_id] = (lat, lon)
            way_nodes.append(node_id)
        direction = is_oneway(way.get("tags") or {})
        for i in range(len(geom) - 1):
            a, b = way_nodes[i], way_nodes[i + 1]
            pa, pb = nodes[a], nodes[b]
            length = haversine_m(pa, pb)
            if length <= 0.1:
                continue
            edge_bearing = bearing(pa, pb)
            reverse_bearing = edge_bearing + 180
            if direction >= 0:
                adjacency.setdefault(a, []).append((b, length, edge_bearing))
            if direction <= 0:
                adjacency.setdefault(b, []).append((a, length, reverse_bearing % 360))
    return nodes, adjacency


def nearest_node(lat: float, lon: float, nodes: dict[int, tuple[float, float]], limit_m: float = 500.0,
                 blocked: set[int] | None = None) -> tuple[int, float] | None:
    target = (lat, lon)
    best = None
    for node, ll in nodes.items():
        if blocked and node in blocked:
            continue
        d = haversine_m(target, ll)
        if d <= limit_m and (best is None or d < best[1]):
            best = (node, d)
    return best


def nearest_nodes(lat: float, lon: float, nodes: dict[int, tuple[float, float]], limit_m: float,
                  count: int = 20) -> list[int]:
    target = (lat, lon)
    scored = [(haversine_m(target, ll), node) for node, ll in nodes.items()
              if haversine_m(target, ll) <= limit_m]
    scored.sort()
    return [node for _, node in scored[:count]]


def reachable_nodes(start: int, adjacency: dict[int, list[tuple[int, float, float]]],
                    min_m: float, max_m: float) -> list[tuple[int, float, list[int], float]]:
    """Nodes reachable after travelling a minimum/maximum road distance.

    Returns (node, distance, path, final_heading). It is a local Dijkstra, suitable
    for expanding a small road-network beam rather than a planetary shortest path.
    """
    heap = [(0.0, start, 0.0, [start])]
    best = {start: 0.0}
    found: dict[int, tuple[float, list[int], float]] = {}
    while heap:
        d, node, heading, path = heapq.heappop(heap)
        if d > max_m:
            continue
        if d >= min_m:
            old = found.get(node)
            if old is None or d < old[0]:
                found[node] = (d, path, heading)
        if d + 1e-6 < best.get(node, 1e30):
            best[node] = d
        for nxt, length, edge_heading in adjacency.get(node, []):
            nd = d + length
            if nd <= max_m and nd + 1e-6 < best.get(nxt, 1e30):
                best[nxt] = nd
                heapq.heappush(heap, (nd, nxt, edge_heading, path + [nxt]))
    out = []
    for node, (d, path, heading) in found.items():
        out.append((node, d, path, heading))
    return out


def evidence_from_analysis(path: Path) -> tuple[list[dict], dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    points = []
    consensus = d.get("consensus", {})
    for cluster in consensus.get("clusters", [])[:5]:
        points.append({
            "lat": cluster["lat"],
            "lon": cluster["lon"],
            "radius_m": 5000,
            "kind": "cluster",
            "frame": cluster.get("frames", [])[:1],
        })
    motion = d.get("motion", {}) or {}
    segments = motion.get("segments", []) or []
    frames = d.get("frame_results", []) or d.get("selected_frames", []) or []
    frame_by_id = {row.get("id"): row for row in frames}
    for seg in segments:
        a = seg.get("from")
        b = seg.get("to")
        if a not in frame_by_id or b not in frame_by_id:
            continue
        points.append({
            "timestamp_seconds": seg.get("end_seconds"),
            "frame": b,
            "motion_confidence": seg.get("confidence", 0),
            "kind": "motion",
        })
    return points, d


def load_evidence(path: Path | None, analysis: dict) -> list[dict]:
    if path:
        d = json.loads(path.read_text(encoding="utf-8"))
        rows = d.get("points", d) if isinstance(d, dict) else d
        out = []
        for i, row in enumerate(rows):
            if "ll" in row:
                lat, lon = row["ll"]
            else:
                lat, lon = row.get("lat"), row.get("lon")
            if lat is None or lon is None:
                continue
            out.append({
                "lat": float(lat), "lon": float(lon),
                "radius_m": float(row.get("radius_m", 200)),
                "timestamp_seconds": row.get("timestamp_seconds"),
                "heading": row.get("heading"),
                "kind": row.get("kind", "anchor"),
                "frame": row.get("frame", f"e{i + 1:03d}"),
            })
        return out
    points, _ = evidence_from_analysis(analysis["_source"])
    return points


def score_state(state: dict, evidence: list[dict], nodes: dict[int, tuple[float, float]]) -> float:
    ll = nodes[state["node"]]
    score = 0.0
    explained = []
    for ev in evidence:
        if ev.get("kind") != "anchor" and ev.get("kind") != "cluster":
            continue
        d = haversine_m(ll, (ev["lat"], ev["lon"]))
        radius = max(float(ev.get("radius_m", 500)), 100.0)
        likelihood = math.exp(-(d / radius) ** 2 / 2)
        score += math.log(max(likelihood, 1e-9)) * 0.25
        if likelihood > 0.37:
            explained.append({"evidence": ev.get("frame"), "distance_m": round(d, 1),
                              "likelihood": round(likelihood, 4)})
    score -= state.get("turn_penalty", 0.0)
    return score


def solve_routes(nodes: dict[int, tuple[float, float]],
                 adjacency: dict[int, list[tuple[int, float, float]]],
                 seeds: list[tuple[float, float]],
                 evidence: list[dict],
                 speed_mps: float,
                 duration_seconds: float,
                 beam: int = 120,
                 step_seconds: float = 10.0) -> dict:
    if not nodes:
        return {"routes": [], "rejected": ["The road graph was empty."]}

    anchors = [ev for ev in evidence if ev.get("kind") in ("anchor", "cluster")]
    global_anchors = [ev for ev in anchors if ev.get("timestamp_seconds") is None]
    start_states = []
    seen = set()
    for lat, lon in seeds:
        near = nearest_nodes(lat, lon, nodes, limit_m=800.0, count=12)
        for node in near:
            if node in seen:
                continue
            seen.add(node)
            start_states.append({
                "node": node,
                "score": 0.0,
                "path": [node],
                "distance_m": 0.0,
                "heading": None,
                "turn_penalty": 0.0,
                "seed_ll": [lat, lon],
                "anchor_best_distances": [None] * len(global_anchors),
                "anchor_terms": [],
            })
    if not start_states:
        return {"routes": [], "rejected": ["No road nodes were within 800 m of the candidate seeds."]}

    step_times: set[float] = set()
    if duration_seconds > step_seconds:
        step_times.update(
            i * step_seconds
            for i in range(1, int(duration_seconds // step_seconds) + 1)
            if i * step_seconds < duration_seconds
        )
    step_times.add(max(duration_seconds, step_seconds))
    for ev in anchors:
        t = ev.get("timestamp_seconds")
        if t is not None and 0 < float(t) <= duration_seconds:
            step_times.add(float(t))
    ordered_times = sorted(step_times)
    states = start_states
    previous_t = 0.0
    for t in ordered_times:
        dt = max(0.1, t - previous_t)
        previous_t = t
        advance = max(30.0, speed_mps * dt)
        next_states = []
        current_anchors = [ev for ev in anchors
                           if ev.get("timestamp_seconds") is not None and
                           abs(float(ev["timestamp_seconds"]) - t) <= 1e-3]
        for state in states:
            minimum = max(0.0, advance * 0.35 - 30.0)
            maximum = advance * 1.8 + 30.0
            reached = reachable_nodes(state["node"], adjacency, minimum, maximum)
            if not reached:
                reached = [(nxt, length, [state["node"], nxt], heading)
                           for nxt, length, heading in adjacency.get(state["node"], [])]
            for node, distance, path, heading in reached:
                ll = nodes[node]
                turn = 0.0
                if state["heading"] is not None:
                    turn = abs(angle_diff(state["heading"], heading))
                score = state["score"] - 0.002 * max(0.0, turn - 45.0)
                anchor_terms = list(state.get("anchor_terms", []))
                best_distances = list(state.get("anchor_best_distances", [None] * len(global_anchors)))
                for j, ev in enumerate(global_anchors):
                    d = haversine_m(ll, (ev["lat"], ev["lon"]))
                    old_d = best_distances[j]
                    if old_d is None or d < old_d:
                        radius = max(float(ev.get("radius_m", 500)), 100.0)
                        new_likelihood = math.exp(-(d / radius) ** 2 / 2)
                        old_likelihood = 1.0 if old_d is None else math.exp(-(old_d / radius) ** 2 / 2)
                        score += (math.log(max(new_likelihood, 1e-9))
                                  - math.log(max(old_likelihood, 1e-9))) * 0.35
                        best_distances[j] = d
                        if new_likelihood > 0.37:
                            anchor_terms.append({
                                "evidence": ev.get("frame"),
                                "distance_m": round(d, 1),
                                "likelihood": round(new_likelihood, 4),
                                "timestamp_seconds": None,
                            })
                for ev in current_anchors:
                    d = haversine_m(ll, (ev["lat"], ev["lon"]))
                    radius = max(float(ev.get("radius_m", 500)), 100.0)
                    likelihood = math.exp(-(d / radius) ** 2 / 2)
                    score += math.log(max(likelihood, 1e-9)) * 0.35
                    if ev.get("heading") is not None:
                        heading_error = abs(angle_diff(float(ev["heading"]), heading))
                        score -= (heading_error / 45.0) ** 2 * 0.5
                    if likelihood > 0.37:
                        anchor_terms.append({"evidence": ev.get("frame"),
                                             "distance_m": round(d, 1),
                                             "likelihood": round(likelihood, 4),
                                             "timestamp_seconds": ev.get("timestamp_seconds")})
                next_states.append({
                    "node": node,
                    "score": score,
                    "path": state["path"] + path[1:],
                    "distance_m": state["distance_m"] + distance,
                    "heading": heading,
                    "turn_penalty": state["turn_penalty"] + max(0.0, turn - 45.0),
                    "seed_ll": state["seed_ll"],
                    "anchor_terms": anchor_terms,
                    "anchor_best_distances": best_distances,
                })
        if not next_states:
            break
        next_states.sort(key=lambda s: s["score"], reverse=True)
        unique = {}
        for state in next_states:
            unique.setdefault(state["node"], state)
        states = list(unique.values())[:beam]

    routes = []
    for state in sorted(states, key=lambda s: s["score"], reverse=True)[:10]:
        coords = [nodes[node] for node in state["path"]]
        if len(coords) < 2:
            continue
        routes.append({
            "rank": len(routes) + 1,
            "score": round(state["score"], 4),
            "seed": state["seed_ll"],
            "start": {"lat": coords[0][0], "lon": coords[0][1]},
            "end": {"lat": coords[-1][0], "lon": coords[-1][1]},
            "distance_m": round(sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1)), 1),
            "coordinates": [{"lat": lat, "lon": lon} for lat, lon in coords],
            "evidence_terms": state.get("anchor_terms", []),
        })
    if routes:
        best = routes[0]["score"]
        for route in routes:
            route["relative_confidence"] = round(math.exp(max(-50.0, (route["score"] - best) / 8)), 4)
    rejected = []
    if len(routes) < 2:
        rejected.append("Only one sufficiently connected road branch survived; collect a timestamped landmark or heading.")
    if not anchors:
        rejected.append("No fixed anchor or cluster evidence was provided; the route is primarily topology-based.")
    return {"routes": routes, "rejected": rejected}


def geojson(routes: list[dict]) -> dict:
    features = []
    for route in routes:
        coords = [[p["lon"], p["lat"]] for p in route["coordinates"]]
        features.append({
            "type": "Feature",
            "properties": {k: v for k, v in route.items() if k != "coordinates"},
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    return {"type": "FeatureCollection", "features": features}


def md_report(result: dict, args) -> str:
    routes = result["routes"]
    L = [
        "# Video route reconstruction",
        "",
        f"- Method: road graph + beam search",
        f"- Assumed speed: {args.speed:.1f} m/s",
        f"- Road graph nodes: {result['road_graph']['nodes']}",
        f"- Road graph edges: {result['road_graph']['edges']}",
        "",
        "## Ranked journeys",
        "",
    ]
    if not routes:
        L.append("- No complete route survived.")
    for route in routes:
        L.append(
            f"- **Rank {route['rank']}**: relative confidence {route['relative_confidence']:.3f}; "
            f"{route['distance_m']:.0f} m; start {route['start']['lat']:.6f},{route['start']['lon']:.6f}; "
            f"end {route['end']['lat']:.6f},{route['end']['lon']:.6f}"
        )
    L += ["", "## Why branches were rejected", ""]
    for reason in result["rejected"]:
        L.append(f"- {reason}")
    L += [
        "",
        "## Verification required",
        "",
        "- Match fixed structures at the start and end against street-level imagery.",
        "- Verify every turn against an actual frame; optical motion does not prove a road bearing.",
        "- Do not present relative confidence as a geographic probability.",
        "- Check OSM coverage and recording edits before using the route as evidence.",
        "",
    ]
    return "\n".join(L)


def cmd_solve(args) -> None:
    analysis = args.analysis.expanduser().resolve(strict=True)
    data = json.loads(analysis.read_text(encoding="utf-8"))
    data["_source"] = str(analysis)

    if args.bbox:
        try:
            bbox = tuple(float(v) for v in args.bbox.split(","))
        except ValueError:
            raise SystemExit("--bbox must be S,W,N,E")
        if len(bbox) != 4:
            raise SystemExit("--bbox must contain four numbers")
    else:
        candidates = [tuple(float(v) for v in c.split(",")) for c in (args.candidate or [])]
        if candidates:
            bbox = merge_bbox([bbox_around(lat, lon, args.radius) for lat, lon in candidates], args.pad)
        else:
            consensus = data.get("consensus", {}) or {}
            clusters = consensus.get("clusters", []) or []
            if not clusters:
                raise SystemExit("Provide --bbox or --candidate, or supply an analysis with GeoCLIP clusters.")
            candidates = [(float(c["lat"]), float(c["lon"])) for c in clusters[:5]]
            bbox = merge_bbox([bbox_around(lat, lon, args.radius) for lat, lon in candidates], args.pad)

    if args.evidence:
        evidence = load_evidence(args.evidence.expanduser().resolve(strict=True), data)
    else:
        evidence, _ = evidence_from_analysis(analysis)
        for candidate in (args.candidate or []):
            lat, lon = (float(v) for v in candidate.split(","))
            evidence.append({"lat": lat, "lon": lon, "radius_m": args.radius,
                             "kind": "anchor", "frame": "user-candidate"})

    if args.road_data:
        overpass = json.loads(args.road_data.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    else:
        overpass = fetch_overpass(bbox, args.proxy)
    nodes, adjacency = build_graph(overpass)
    if args.candidate:
        seeds = [tuple(float(v) for v in c.split(",")) for c in args.candidate]
    else:
        seeds = [(ev["lat"], ev["lon"]) for ev in evidence if ev.get("kind") in ("anchor", "cluster")]
        if not seeds:
            consensus = data.get("consensus", {}) or {}
            seeds = [(float(c["lat"]), float(c["lon"])) for c in consensus.get("clusters", [])[:5]]

    duration = args.duration
    if duration is None:
        duration = float(data.get("media", {}).get("duration_seconds") or 0)
    route = solve_routes(nodes, adjacency, seeds, evidence, args.speed,
                         duration, args.beam, args.step)
    edge_count = sum(len(rows) for rows in adjacency.values())
    result = {
        "schema_version": 1,
        "analysis": str(analysis),
        "bbox": list(bbox),
        "assumed_speed_mps": args.speed,
        "duration_seconds": duration,
        "evidence": evidence,
        "road_graph": {"nodes": len(nodes), "edges": edge_count},
        "routes": route["routes"],
        "rejected": route["rejected"],
        "limitations": [
            "Routes are road-network hypotheses; OSM coverage and edits are not ground truth.",
            "Relative confidence is a branch ranking, not a calibrated geographic probability.",
            "Turns and start/end positions require frame-level fixed-feature verification.",
        ],
    }
    out = args.out_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "route.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "route.geojson").write_text(json.dumps(geojson(result["routes"]), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "route.md").write_text(md_report(result, args), encoding="utf-8")
    print(json.dumps({
        "folder": str(out),
        "road_graph": result["road_graph"],
        "routes": len(result["routes"]),
        "top": result["routes"][0] if result["routes"] else None,
    }, ensure_ascii=False, indent=1))
    print(f"-> {out / 'route.md'}, {out / 'route.geojson'}, {out / 'route.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    solve = sub.add_parser("solve", help="search road-network journeys consistent with video evidence")
    solve.add_argument("analysis", type=Path)
    solve.add_argument("--bbox", help="S,W,N,E search area")
    solve.add_argument("--candidate", action="append", help="lat,lon seed; repeat for several starts")
    solve.add_argument("--evidence", type=Path, help="optional timestamped anchor JSON")
    solve.add_argument("--road-data", type=Path, help="saved Overpass JSON; skips the live query")
    solve.add_argument("--radius", type=float, default=5000.0, help="radius around each seed in metres")
    solve.add_argument("--pad", type=float, default=1500.0, help="padding around candidate seeds")
    solve.add_argument("--speed", type=float, default=13.4, help="assumed travel speed in metres per second")
    solve.add_argument("--duration", type=float, help="recording duration; defaults to analysis media duration")
    solve.add_argument("--step", type=float, default=10.0, help="road-graph beam step in seconds")
    solve.add_argument("--beam", type=int, default=120, help="maximum route states retained per step")
    solve.add_argument("--proxy", help="optional proxy for Overpass")
    solve.add_argument("--out-dir", type=Path, default=Path("route"))
    solve.set_defaults(func=cmd_solve)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    main()

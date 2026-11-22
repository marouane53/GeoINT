#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Offline tests for route graph construction and route ranking."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from argparse import Namespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import route_solver


class GraphTests(unittest.TestCase):
    def make_grid(self):
        nodes = {i: (float(i // 4) * 0.01, float(i % 4) * 0.01) for i in range(16)}
        adjacency = {}
        for i in range(4):
            for j in range(4):
                node = i * 4 + j
                if j < 3:
                    nxt = node + 1
                    a = route_solver.bearing(nodes[node], nodes[nxt])
                    adjacency.setdefault(node, []).append((nxt, 1110.0, a))
                    adjacency.setdefault(nxt, []).append((node, 1110.0, (a + 180) % 360))
                if i < 3:
                    nxt = node + 4
                    a = route_solver.bearing(nodes[node], nodes[nxt])
                    adjacency.setdefault(node, []).append((nxt, 1110.0, a))
                    adjacency.setdefault(nxt, []).append((node, 1110.0, (a + 180) % 360))
        return nodes, adjacency

    def test_haversine_and_angle_diff(self):
        d = route_solver.haversine_m((0, 0), (0, 0.001))
        self.assertGreater(d, 100)
        self.assertLess(d, 120)
        self.assertAlmostEqual(route_solver.angle_diff(359, 1), -2)
        self.assertAlmostEqual(route_solver.angle_diff(1, 359), 2)

    def test_overpass_way_directions_and_grid_geometry(self):
        data = {"elements": [{
            "type": "way", "nodes": [1, 2, 3],
            "geometry": [
                {"lat": 48.0, "lon": 16.0},
                {"lat": 48.001, "lon": 16.001},
                {"lat": 48.002, "lon": 16.002},
            ],
            "tags": {"highway": "primary", "oneway": "yes"},
        }]}
        nodes, adjacency = route_solver.build_graph(data)
        self.assertEqual(set(nodes), {1, 2, 3})
        self.assertIn(2, [row[0] for row in adjacency[1]])
        self.assertNotIn(1, [row[0] for row in adjacency[2]])

    def test_geometry_without_node_ids_still_connects_adjacent_ways(self):
        data = {"elements": [
            {"type": "way", "geometry": [
                {"lat": 48.0, "lon": 16.0}, {"lat": 48.001, "lon": 16.001},
            ], "tags": {"highway": "residential"}},
            {"type": "way", "geometry": [
                {"lat": 48.001, "lon": 16.001}, {"lat": 48.002, "lon": 16.002},
            ], "tags": {"highway": "residential"}},
        ]}
        nodes, adjacency = route_solver.build_graph(data)
        junction = next(node for node, ll in nodes.items() if ll == (48.001, 16.001))
        self.assertEqual(len(adjacency[junction]), 2)

    def test_reverse_oneway_is_only_traversable_in_reverse(self):
        data = {"elements": [{
            "type": "way", "nodes": [1, 2],
            "geometry": [{"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.001}],
            "tags": {"highway": "residential", "oneway": "-1"},
        }]}
        _, adjacency = route_solver.build_graph(data)
        self.assertNotIn(2, [edge[0] for edge in adjacency.get(1, [])])
        self.assertIn(1, [edge[0] for edge in adjacency.get(2, [])])

    def test_reachable_nodes_are_distance_limited(self):
        nodes, adjacency = self.make_grid()
        rows = route_solver.reachable_nodes(0, adjacency, 1000.0, 2500.0)
        self.assertTrue(rows)
        self.assertTrue(all(1000 <= row[1] <= 2500 for row in rows))

    def test_anchors_rank_the_matching_branch_first(self):
        nodes, adjacency = self.make_grid()
        evidence = [{
            "lat": 0.0, "lon": 0.02, "radius_m": 100.0,
            "kind": "anchor", "frame": "f003",
        }]
        result = route_solver.solve_routes(
            nodes, adjacency,
            seeds=[(0.0, 0.0)],
            evidence=evidence,
            speed_mps=111.0,
            duration_seconds=20.0,
            beam=40,
            step_seconds=10.0,
        )
        self.assertTrue(result["routes"])
        self.assertGreaterEqual(result["routes"][0]["rank"], 1)
        self.assertIn("evidence_terms", result["routes"][0])
        self.assertGreaterEqual(len(result["routes"][0]["coordinates"]), 3)
        self.assertIn("f003", [x["evidence"] for x in result["routes"][0]["evidence_terms"]])

    def test_geojson_has_ranked_line_features(self):
        result = {"routes": [{
            "rank": 1, "score": 1.0, "relative_confidence": 0.9,
            "distance_m": 100.0,
            "coordinates": [{"lat": 48.0, "lon": 16.0}, {"lat": 48.1, "lon": 16.1}],
        }]}
        gj = route_solver.geojson(result["routes"])
        self.assertEqual(gj["type"], "FeatureCollection")
        self.assertEqual(gj["features"][0]["geometry"]["type"], "LineString")
        self.assertEqual(gj["features"][0]["geometry"]["coordinates"][0], [16.0, 48.0])


class EvidenceTests(unittest.TestCase):
    def test_explicit_evidence_is_validated_and_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            evidence = d / "evidence.json"
            evidence.write_text(json.dumps({"points": [
                {"timestamp_seconds": 12.5, "ll": [48.2, 16.3],
                 "radius_m": 300, "heading": 90, "kind": "anchor", "frame": "f003"},
            ]}))
            got = route_solver.load_evidence(evidence, {"_source": "analysis.json"})
            self.assertEqual(got[0]["lat"], 48.2)
            self.assertEqual(got[0]["radius_m"], 300)
            self.assertEqual(got[0]["frame"], "f003")

    def test_complete_solver_command_accepts_saved_road_graph(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            analysis = root / "analysis.json"
            analysis.write_text(json.dumps({
                "media": {"duration_seconds": 4.0},
                "consensus": {"clusters": []},
            }))
            road_data = root / "roads.json"
            road_data.write_text(json.dumps({"elements": [{
                "type": "way",
                "nodes": [1, 2, 3, 4],
                "geometry": [
                    {"lat": 48.0, "lon": 16.0},
                    {"lat": 48.0002, "lon": 16.0},
                    {"lat": 48.0004, "lon": 16.0},
                    {"lat": 48.0006, "lon": 16.0},
                ],
                "tags": {"highway": "residential"},
            }]}))
            args = Namespace(
                analysis=analysis, bbox="47.99,15.99,48.01,16.01",
                candidate=["48.0,16.0"], radius=500, pad=100, evidence=None,
                road_data=road_data, proxy=None, duration=4.0,
                speed=5.0, beam=20, step=2.0, out_dir=root / "route",
            )
            route_solver.cmd_solve(args)
            result = json.loads((root / "route" / "route.json").read_text())
            self.assertEqual(result["road_graph"]["nodes"], 4)
            self.assertTrue((root / "route" / "route.geojson").exists())


if __name__ == "__main__":
    unittest.main()

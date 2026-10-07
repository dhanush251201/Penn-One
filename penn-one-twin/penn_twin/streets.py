"""OpenStreetMap street + sidewalk graphs for real-route distances and drawable paths.

Walking distances are shortest paths over sidewalks, footpaths, crossings, steps and
streets (no motorways), so a "2-minute walk" never cuts through a building, the rail
yards or the river. The drive graph (one-way aware) gives on-street geometry for
drawing van legs; van travel *times* still come from the OSRM matrix.

Data: data/cache/osm_ways_nodes.json, an Overpass dump of every highway way in the
service area (`way["highway"](bbox); out body; >; out skel qt;`).
"""
import json
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from .geo import haversine_m

OSM_FILE = Path(__file__).resolve().parent.parent / "data" / "cache" / "osm_ways_nodes.json"

WALK_HW = {"footway", "pedestrian", "path", "steps", "living_street", "residential", "service",
           "unclassified", "tertiary", "tertiary_link", "secondary", "secondary_link",
           "primary", "primary_link", "cycleway", "track", "corridor", "elevator"}
DRIVE_SPEED = {"motorway": 25, "motorway_link": 15, "trunk": 18, "trunk_link": 12,
               "primary": 13, "primary_link": 10, "secondary": 12, "secondary_link": 9,
               "tertiary": 11, "tertiary_link": 9, "unclassified": 9, "residential": 8,
               "living_street": 5, "service": 5}


def _xy(lat, lon, lat0=39.95):
    return np.c_[lon * 111320.0 * np.cos(np.radians(lat0)), lat * 111320.0]


class StreetGraph:
    def __init__(self, path=OSM_FILE):
        els = json.load(open(path))["elements"]
        nodes = {e["id"]: (e["lat"], e["lon"]) for e in els if e["type"] == "node"}
        ids = list(nodes)
        self.idx = {k: i for i, k in enumerate(ids)}
        self.lat = np.array([nodes[k][0] for k in ids])
        self.lon = np.array([nodes[k][1] for k in ids])
        n = len(ids)
        walk, drive_len, drive_cost = [], [], []
        for e in els:
            if e["type"] != "way":
                continue
            tg = e.get("tags", {})
            hw = tg.get("highway", "")
            nd = [self.idx[k] for k in e["nodes"] if k in self.idx]
            if len(nd) < 2:
                continue
            a, b = np.array(nd[:-1]), np.array(nd[1:])
            seg = haversine_m(self.lat[a], self.lon[a], self.lat[b], self.lon[b])
            private = tg.get("access") in ("private", "no")
            if (hw in WALK_HW and tg.get("foot") != "no"
                    and (not private or tg.get("foot") in ("yes", "designated"))):
                walk.append((a, b, seg))
                walk.append((b, a, seg))
            if hw in DRIVE_SPEED and not private and tg.get("service") not in ("parking_aisle", "driveway"):
                ow = tg.get("oneway", "no")
                cost = seg / DRIVE_SPEED[hw]
                if ow == "-1":
                    a, b = b, a
                drive_len.append((a, b, seg)); drive_cost.append((a, b, cost))
                if ow not in ("yes", "-1", "1", "true") and tg.get("junction") != "roundabout":
                    drive_len.append((b, a, seg)); drive_cost.append((b, a, cost))
        self.walk_g = self._csr(walk, n)
        self.drive_g = self._csr(drive_cost, n)
        self.drive_len_g = self._csr(drive_len, n)
        self.walk_nodes = np.unique(np.concatenate([w[0] for w in walk] + [w[1] for w in walk]))
        self.drive_nodes = np.unique(np.concatenate([d[0] for d in drive_len] + [d[1] for d in drive_len]))
        P = _xy(self.lat, self.lon)
        self.walk_tree = cKDTree(P[self.walk_nodes])
        self.drive_tree = cKDTree(P[self.drive_nodes])

    @staticmethod
    def _csr(edges, n):
        a = np.concatenate([e[0] for e in edges])
        b = np.concatenate([e[1] for e in edges])
        w = np.maximum(np.concatenate([e[2] for e in edges]), 0.01)
        g = csr_matrix((w, (a, b)), shape=(n, n))
        g.sum_duplicates()  # parallel edges: keep it simple, duplicates are rare and near-equal
        return g

    def snap(self, lat, lon, mode="walk"):
        tree, pool = (self.walk_tree, self.walk_nodes) if mode == "walk" else (self.drive_tree, self.drive_nodes)
        d, i = tree.query(_xy(np.atleast_1d(lat), np.atleast_1d(lon)))
        return pool[i], d

    def walk_matrix(self, lat, lon, limit_m=800.0):
        """Walking distance (m) between points over the sidewalk network; inf beyond limit."""
        v, snap_d = self.snap(lat, lon, "walk")
        n = len(v)
        out = np.full((n, n), np.inf)
        for k in range(0, n, 64):
            D = dijkstra(self.walk_g, indices=v[k:k + 64], limit=limit_m)
            out[k:k + 64] = D[:, v] + snap_d[k:k + 64, None] + snap_d[None, :]
        np.fill_diagonal(out, 0.0)
        return out

    def paths(self, pairs, lat, lon, mode="drive"):
        """On-network polylines [(lat, lon), ...] for (i, j) index pairs into lat/lon."""
        g = self.drive_g if mode == "drive" else self.walk_g
        v, _ = self.snap(lat, lon, mode)
        by_src = {}
        for i, j in pairs:
            by_src.setdefault(i, []).append(j)
        srcs = list(by_src)
        out = {}
        for k in range(0, len(srcs), 64):
            chunk = srcs[k:k + 64]
            _, pred = dijkstra(g, indices=v[chunk], return_predecessors=True)
            for row, i in enumerate(chunk):
                for j in by_src[i]:
                    seq, cur = [], v[j]
                    while cur >= 0 and cur != v[i] and len(seq) < 5000:
                        seq.append(cur)
                        cur = pred[row, cur]
                    if cur != v[i]:  # unreachable on this graph: straight segment
                        out[(i, j)] = [(lat[i], lon[i]), (lat[j], lon[j])]
                        continue
                    seq.append(v[i])
                    seq.reverse()
                    pts = [(lat[i], lon[i])] + [(self.lat[s], self.lon[s]) for s in seq] + [(lat[j], lon[j])]
                    out[(i, j)] = simplify(pts, 2.5)
        return out


def simplify(pts, tol_m):
    """Ramer-Douglas-Peucker on lat/lon points (tolerance in meters)."""
    if len(pts) < 3:
        return pts
    P = _xy(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
    keep = np.zeros(len(P), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(P) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = P[b] - P[a]
        L = np.hypot(*seg) or 1e-9
        d = np.abs(np.cross(seg, P[a + 1:b] - P[a])) / L
        k = int(d.argmax())
        if d[k] > tol_m:
            keep[a + 1 + k] = True
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return [p for p, k in zip(pts, keep) if k]

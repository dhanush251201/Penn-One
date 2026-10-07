"""Road network as a dense node set + all-pairs travel matrix.

Nodes are a ~150 m grid over the service area snapped to drivable roads via OSRM.
Travel durations/distances come from the OSRM table API (cached to disk). Without
network access we fall back to a haversine approximation (1.3x straight line).
"""
import json
import time
import urllib.request
from pathlib import Path

import numpy as np

from .geo import APPROVED_CORRIDORS, BBOX, haversine_m

OSRM_URL = "https://router.project-osrm.org"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


def _get_json(url, retries=4):
    for k in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.load(resp)
            if data.get("code") == "Ok":
                return data
            raise RuntimeError(data.get("message", data.get("code")))
        except Exception:
            if k == retries - 1:
                raise
            time.sleep(2 * (k + 1))


def _coord_str(lats, lons):
    return ";".join(f"{lo:.6f},{la:.6f}" for la, lo in zip(lats, lons))


class Network:
    def __init__(self, lat, lon, dur, dist, names, approved, source):
        self.lat = np.asarray(lat)
        self.lon = np.asarray(lon)
        self.dur = np.asarray(dur, dtype=float)    # seconds, free-flow
        self.dist = np.asarray(dist, dtype=float)  # meters
        self.names = list(names)
        self.approved = np.asarray(approved, dtype=bool)
        self.source = source
        self.n = len(self.lat)
        self.crow = haversine_m(self.lat[:, None], self.lon[:, None],
                                self.lat[None, :], self.lon[None, :])
        self.walk_m = self._walk_matrix()

    # ---- queries -------------------------------------------------------
    def nearest_node(self, lat, lon):
        lat = np.atleast_1d(lat)
        lon = np.atleast_1d(lon)
        d = haversine_m(lat[:, None], lon[:, None], self.lat[None, :], self.lon[None, :])
        return d.argmin(axis=1)

    def _walk_matrix(self):
        """Walking distance over OSM sidewalks/footpaths/streets (cached). Falls back to
        1.4x straight line only if the OSM extract is missing."""
        from .streets import OSM_FILE, StreetGraph
        cache = CACHE_DIR / f"walk_{self.source}_{self.n}_{self.lat.sum():.4f}.npy"
        if cache.exists():
            return np.load(cache)
        if not OSM_FILE.exists():
            print("[network] WARNING: no OSM sidewalk data; walking = 1.4x straight line")
            return self.crow * 1.4
        W = StreetGraph().walk_matrix(self.lat, self.lon)
        np.save(cache, W)
        return W

    def walk_s(self, i, j, speed=1.3):
        """Walking time over the real sidewalk/footpath network."""
        return self.walk_m[i, j] / speed

    def mark_approved(self, nodes):
        self.approved[np.asarray(list(nodes), dtype=int)] = True

    # ---- construction --------------------------------------------------
    @classmethod
    def build(cls, spacing_m=150, bbox=BBOX, offline=False, osrm_url=OSRM_URL,
              max_snap_m=90, verbose=True):
        src = "haversine" if offline else "osrm"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache = CACHE_DIR / f"net_{src}_{spacing_m}_{'_'.join(map(str, bbox))}.npz"
        if cache.exists():
            z = np.load(cache, allow_pickle=True)
            return cls(z["lat"], z["lon"], z["dur"], z["dist"], z["names"],
                       z["approved"], src)

        s, w, n, e = bbox
        lat0 = (s + n) / 2
        dlat = spacing_m / 111320.0
        dlon = spacing_m / (111320.0 * np.cos(np.radians(lat0)))
        glat, glon = np.meshgrid(np.arange(s, n, dlat), np.arange(w, e, dlon), indexing="ij")
        glat, glon = glat.ravel(), glon.ravel()

        if offline:
            net = cls._build_offline(glat, glon)
        else:
            net = cls._build_osrm(glat, glon, osrm_url, max_snap_m, verbose)
        np.savez(cache, lat=net.lat, lon=net.lon, dur=net.dur, dist=net.dist,
                 names=np.array(net.names, dtype=object), approved=net.approved)
        return net

    @classmethod
    def _build_offline(cls, lat, lon):
        crow = haversine_m(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
        dist = crow * 1.3
        dur = np.where(dist > 0, dist / 6.0 + 20.0, 0.0)  # ~13 mph average + intersection delay
        names = [""] * len(lat)
        return cls(lat, lon, dur, dist, names, np.zeros(len(lat), bool), "haversine")

    @classmethod
    def _build_osrm(cls, glat, glon, url, max_snap_m, verbose):
        # 1) snap grid points to roads (table call returns snapped waypoints + street names)
        snapped = {}
        for k in range(0, len(glat), 100):
            la, lo = glat[k:k + 100], glon[k:k + 100]
            data = _get_json(f"{url}/table/v1/driving/{_coord_str(la, lo)}?destinations=0")
            for wp in data["sources"]:
                if wp["distance"] > max_snap_m:
                    continue
                key = (round(wp["location"][1], 5), round(wp["location"][0], 5))
                snapped.setdefault(key, wp.get("name", ""))
            time.sleep(1.0)
        keys = sorted(snapped)
        lat = np.array([k[0] for k in keys])
        lon = np.array([k[1] for k in keys])
        names = [snapped[k] for k in keys]
        n = len(lat)
        if verbose:
            print(f"[network] {n} road nodes after snapping; fetching {n}x{n} OSRM matrix...")

        # 2) all-pairs durations/distances in 50x50 blocks
        dur = np.full((n, n), np.nan)
        dist = np.full((n, n), np.nan)
        B = 50
        blocks = [(i, j) for i in range(0, n, B) for j in range(0, n, B)]
        for b, (i, j) in enumerate(blocks):
            si, sj = slice(i, min(i + B, n)), slice(j, min(j + B, n))
            ni, nj = si.stop - si.start, sj.stop - sj.start
            coords = _coord_str(np.r_[lat[si], lat[sj]], np.r_[lon[si], lon[sj]])
            q = ("?annotations=duration,distance"
                 f"&sources={';'.join(map(str, range(ni)))}"
                 f"&destinations={';'.join(map(str, range(ni, ni + nj)))}")
            data = _get_json(f"{url}/table/v1/driving/{coords}{q}")
            dur[si, sj] = np.array(data["durations"], dtype=float)
            dist[si, sj] = np.array(data["distances"], dtype=float)
            if verbose and (b + 1) % 10 == 0:
                print(f"[network]   block {b + 1}/{len(blocks)}")
            time.sleep(1.0)

        # unreachable pairs -> large penalty via crow-flies estimate
        crow = haversine_m(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
        bad = ~np.isfinite(dur)
        dur[bad] = crow[bad] * 1.5 / 4.0 + 120
        dist[bad] = crow[bad] * 1.5
        np.fill_diagonal(dur, 0.0)
        np.fill_diagonal(dist, 0.0)

        approved = np.array([any(c in nm for c in APPROVED_CORRIDORS) for nm in names])
        return cls(lat, lon, dur, dist, names, approved, "osrm")

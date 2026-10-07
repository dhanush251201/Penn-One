"""Trip demand: synthetic generator + loader for real Penn Transit trip exports.

Both produce the same trip table schema:
    request_time_s   seconds since midnight of the service day (after-midnight > 86400)
    origin_lat, origin_lon, dest_lat, dest_lon
    party_size       riders travelling together
    accessible       rider needs door-to-door service (never offered walking/bus)
    u_walk, u_bus    uniform draws; a rider opts in if u < policy opt-in rate, so
                     every scenario sees the same riders make the same choices
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .geo import BBOX, LANDMARKS, haversine_m

# Relative request rate per hour of the evening service (18 = 6 pm, 26 = 2 am)
HOURLY_PROFILE = {18: 0.55, 19: 0.8, 20: 1.0, 21: 1.1, 22: 1.15, 23: 1.1, 24: 0.9, 25: 0.6, 26: 0.35}

COLUMNS = ["request_time_s", "origin_lat", "origin_lon", "dest_lat", "dest_lon",
           "party_size", "accessible", "u_walk", "u_bus"]


def _sample_point(rng, idx):
    _, (la, lo), _, _, spread = LANDMARKS[idx]
    s, w, n, e = BBOX
    dlat, dlon = rng.normal(0, spread, 2) / np.array([111320.0, 85000.0])
    return float(np.clip(la + dlat, s, n)), float(np.clip(lo + dlon, w, e))


def synthesize(n_trips=450, seed=0):
    """One evening of on-demand requests, skewed toward campus->home late at night."""
    rng = np.random.default_rng(seed)
    kinds = np.array([lm[2] for lm in LANDMARKS])
    weights = np.array([lm[3] for lm in LANDMARKS], dtype=float)
    away = np.where(kinds != "housing", weights, 0)
    home = np.where(kinds == "housing", weights, 0)
    away, home = away / away.sum(), home / home.sum()
    hours = np.array(list(HOURLY_PROFILE))
    hp = np.array(list(HOURLY_PROFILE.values()))
    hp = hp / hp.sum()

    rows = []
    while len(rows) < n_trips:
        h = rng.choice(hours, p=hp)
        t = h * 3600 + rng.uniform(0, 3600)
        p_home = 0.45 + 0.35 * (h - 18) / 8  # later = more trips heading home
        if rng.random() < p_home:
            oi, di = rng.choice(len(LANDMARKS), p=away), rng.choice(len(LANDMARKS), p=home)
        else:
            oi, di = rng.choice(len(LANDMARKS), p=home), rng.choice(len(LANDMARKS), p=away)
        o, d = _sample_point(rng, oi), _sample_point(rng, di)
        if haversine_m(*o, *d) < 300:
            continue
        party = rng.choice([1, 2, 3], p=[0.85, 0.12, 0.03])
        rows.append((t, *o, *d, party, rng.random() < 0.03, rng.random(), rng.random()))
    df = pd.DataFrame(rows, columns=COLUMNS).sort_values("request_time_s")
    return df.reset_index(drop=True)


def load_trips_csv(path, seed=0):
    """Load real trip data. Needs request time + origin/dest lat/lon; other columns optional.

    request_time may be seconds since midnight or a timestamp string; times before
    noon are treated as after-midnight continuation of the previous evening.
    """
    df = pd.read_csv(path)
    rng = np.random.default_rng(seed)
    if "request_time_s" not in df:
        ts = pd.to_datetime(df["request_time"])
        df["request_time_s"] = ts.dt.hour * 3600 + ts.dt.minute * 60 + ts.dt.second
    df.loc[df["request_time_s"] < 12 * 3600, "request_time_s"] += 24 * 3600
    df["party_size"] = df.get("party_size", 1)
    df["accessible"] = df.get("accessible", False)
    for c in ("u_walk", "u_bus"):
        if c not in df:
            df[c] = rng.random(len(df))
    return df[COLUMNS].sort_values("request_time_s").reset_index(drop=True)


@dataclass(eq=False)
class Request:
    rid: int
    t_req: float
    o: int
    d: int
    pax: int
    accessible: bool
    u_walk: float
    u_bus: float
    direct_s: float = 0.0
    # planned/actual van leg
    pu_node: int = -1
    do_node: int = -1
    earliest_pu: float = 0.0
    latest_pu: float = 0.0
    max_ride_s: float = 0.0
    promised_pu: float = None
    pu_t: float = None
    do_t: float = None
    vid: int = None
    shared: bool = False
    status: str = "pending"            # pending | assigned | done | unserved
    mode: str = "van"                  # van | walk+van | van+bus | bus+van
    walk_s: float = 0.0                # walking before van pickup / to bus
    bus_plan: dict = None
    final_t: float = None              # door-to-door arrival


def to_requests(df, net):
    o = net.nearest_node(df.origin_lat.values, df.origin_lon.values)
    d = net.nearest_node(df.dest_lat.values, df.dest_lon.values)
    reqs = []
    for k, row in enumerate(df.itertuples(index=False)):
        if o[k] == d[k]:
            continue
        reqs.append(Request(k, float(row.request_time_s), int(o[k]), int(d[k]),
                            int(row.party_size), bool(row.accessible),
                            float(row.u_walk), float(row.u_bus)))
    return reqs

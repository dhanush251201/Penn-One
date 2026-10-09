"""Export one simulated evening (baseline vs. a lever scenario) for the replay page.

    python3 -m penn_twin.export_viz --night 0 --compare all_levers
    python3 -m penn_twin.build_page        # inlines the JSON into viz/replay.html
"""
import argparse
import json
from pathlib import Path

from .config import FleetConfig, SimConfig, scenario
from .demand import synthesize, to_requests
from .geo import BBOX, M_PER_MILE, SERVICE_AREA
from .history import pickup_density
from .metrics import summarize
from .network import CACHE_DIR, Network
from .sim import Simulator
from .streets import OSM_FILE, StreetGraph
from .transit import Transit

S0, W0 = BBOX[0], BBOX[1]


def q(lat, lon):
    """Quantize to integer 1e-5 degree offsets from the bbox SW corner."""
    return [round((lon - W0) * 1e5), round((lat - S0) * 1e5)]


def streets():
    src = CACHE_DIR / "streets_osm.json"
    if not src.exists():
        return []
    rank = {"trunk": 3, "primary": 3, "secondary": 2, "tertiary": 2}
    out = []
    for e in json.load(open(src))["elements"]:
        g = e.get("geometry")
        if not g:
            continue
        hw = e["tags"].get("highway", "").replace("_link", "")
        pts = [c for p in g for c in q(p["lat"], p["lon"])]
        out.append([rank.get(hw, 1)] + pts)
    return out


class PathBank:
    """Collects (mode, from_node, to_node) requests, then routes them all on the OSM graphs
    so every van leg, bus segment and walk is drawn along real streets and sidewalks."""

    def __init__(self, net):
        self.net, self.keys = net, {}

    def want(self, mode, i, j):
        return self.keys.setdefault((mode, int(i), int(j)), len(self.keys))

    def build(self):
        out = [None] * len(self.keys)
        g = StreetGraph() if OSM_FILE.exists() else None
        for mode in ("drive", "walk"):
            pairs = [(i, j) for (m, i, j) in self.keys if m == mode]
            if g is not None and pairs:
                lines = g.paths(pairs, self.net.lat, self.net.lon, mode)
            else:
                lines = {(i, j): [(self.net.lat[i], self.net.lon[i]), (self.net.lat[j], self.net.lon[j])]
                         for i, j in pairs}
            for (i, j), pts in lines.items():
                out[self.keys[(mode, i, j)]] = [c for la, lo in pts for c in q(la, lo)]
        return out


def bus_json(transit, bank):
    return [{"name": r.name, "stops": r.stops, "offset": [round(o) for o in r.offset],
             "loop": round(r.loop_s), "headway": r.headway, "start": r.start, "end": r.end,
             "paths": [bank.want("drive", a, b) for a, b in zip(r.stops, r.stops[1:] + r.stops[:1])]}
            for r in transit.routes]


def run_one(net, df, name, fleet, simcfg, transit, density=None, bank=None):
    sim = Simulator(net, to_requests(df, net), scenario(name), fleet, simcfg, transit, density).run()
    vans = []
    for v in sim.fleet:
        kg_per_mi = (simcfg.kg_co2_per_kwh * fleet.kwh_per_mile / fleet.charger_eff if v.is_ev
                     else simcfg.kg_co2_per_gal / fleet.mpg)
        vans.append({
            "ev": v.is_ev,
            "legs": [[round(t0), round(t1), int(a), int(b), int(ld),
                      round(net.dist[a, b] / M_PER_MILE, 3), bank.want("drive", a, b)]
                     for t0, t1, a, b, ld, _ in v.legs],
            "charges": [[round(a), round(b)] for a, b in v.charges],
            "kg_per_mi": round(kg_per_mi, 4),
        })
    riders = []
    for r in sim.reqs:
        riders.append([round(r.t_req), int(r.o), int(r.pu_node), int(r.do_node),
                       None if r.pu_t is None else round(r.pu_t),
                       None if r.do_t is None else round(r.do_t),
                       r.mode, r.status, r.pax, round(r.walk_s), round(r.earliest_pu),
                       bank.want("walk", r.o, r.pu_node) if r.mode == "walk+van" else -1])
    m = summarize(sim)
    return {"vans": vans, "riders": riders, "engine": sim.disp.engine.name, "buses": sim.P.buses,
            "kpis": {k: (None if v != v else round(float(v), 2)) for k, v in m.items()}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--night", type=int, default=0)
    ap.add_argument("--trips-per-night", type=int, default=300)
    ap.add_argument("--base", default="baseline", help="scenario on the left map")
    ap.add_argument("--compare", default="all_levers",
                    help="scenario(s) on the other map(s), comma-separated")
    ap.add_argument("--out", default="viz/replay_data.json")
    args = ap.parse_args(argv)

    net = Network.build()
    fleet, simcfg = FleetConfig(), SimConfig()
    tt = lambda a, b: net.dur[a, b] * simcfg.congestion
    transit = Transit(net, tt)
    density = pickup_density(net)
    df = synthesize(args.trips_per_night, seed=args.night)
    bank = PathBank(net)

    data = {
        "bbox": BBOX,
        "nodes": [q(la, lo) for la, lo in zip(net.lat, net.lon)],
        "approved": [int(i) for i in net.approved.nonzero()[0]],
        "area": [q(la, lo) for la, lo in SERVICE_AREA],
        "depot": int(net.nearest_node(*simcfg.depot)[0]),
        "start": simcfg.service_start_s, "end": simcfg.service_end_s,
        "streets": streets(),
        "bus": bus_json(transit, bank),
        "fleet": {"n_ev": fleet.n_ev, "n_ice": fleet.n_ice,
                  "ev_capacity": fleet.ev_capacity, "ice_capacity": fleet.ice_capacity},
        "demand": {"night": args.night, "requests": len(df)},
        "scenarios": {},
    }
    for name in [args.base, *args.compare.split(",")]:
        data["scenarios"][name] = run_one(net, df, name, fleet, simcfg, transit, density, bank)
        print(name, data["scenarios"][name]["kpis"]["vehicle_miles"], "mi")
    data["paths"] = bank.build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()

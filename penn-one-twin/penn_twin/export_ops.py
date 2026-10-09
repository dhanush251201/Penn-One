"""Export one evening of dispatcher internals for the operator console.

    python3 -m penn_twin.export_ops && python3 -m penn_twin.build_page ops
    python3 -m penn_twin.export_ops --scenario all_levers   # another scenario
"""
import argparse
import json
from pathlib import Path

import numpy as np

from .config import FleetConfig, SimConfig, scenario
from .demand import synthesize, to_requests
from .export_viz import PathBank, bus_json, q, streets
from .geo import BBOX, BUILDINGS, M_PER_MILE, SERVICE_AREA
from .history import peak_hour, pickup_density
from .metrics import summarize
from .network import Network
from .sim import Simulator
from .transit import Transit


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--night", type=int, default=0)
    ap.add_argument("--trips-per-night", type=int, default=300)
    ap.add_argument("--scenario", default="pool+reopt")
    ap.add_argument("--out", default="viz/ops_data.json")
    args = ap.parse_args(argv)

    net = Network.build()
    fleet, simcfg = FleetConfig(), SimConfig()
    transit = Transit(net, lambda a, b: net.dur[a, b] * simcfg.congestion)
    density = pickup_density(net)
    bank = PathBank(net)
    df = synthesize(args.trips_per_night, seed=args.night)

    sims = {}
    for name in ("baseline", args.scenario):
        sims[name] = Simulator(net, to_requests(df, net), scenario(name), fleet, simcfg,
                               transit, density).run()
    sim = sims[args.scenario]
    disp = sim.disp

    bnode = {name: int(net.nearest_node(la, lo)[0]) for name, la, lo in BUILDINGS}

    def place(n):
        """Human label for a road node: nearby building, else street name."""
        best = min(bnode.items(), key=lambda kv: net.crow[n, kv[1]])
        d = net.crow[n, best[1]]
        if d <= 150:
            return best[0]
        street = net.names[n]
        if d <= 350 or not street or "Expressway" in street:
            return f"near {best[0]}"
        return street

    buildings = []
    for name, la, lo in BUILDINGS:
        n = bnode[name]
        ph, _ = peak_hour(net, n)
        cands = disp.walk_cands[n]
        walk = None
        if cands:
            c = cands[0]
            walk = {"node": c, "street": net.names[c] or "approved point",
                    "walk_m": round(float(net.walk_m[n, c])), "path": bank.want("walk", n, c),
                    "straight_m": round(float(net.crow[n, c])),
                    "walk_s": round(float(net.walk_s(n, c))),
                    "pickups": round(float(density[c]), 1)}
        tonight = [r for r in sim.reqs if r.o == n or net.walk_m[r.o, n] <= 150]
        waits = [(r.pu_t - r.earliest_pu) / 60 for r in tonight if r.pu_t is not None]
        buildings.append({
            "name": name, "p": q(la, lo), "node": n,
            "hist_pickups": round(float(density[n]), 1), "peak_hour": ph,
            "walk": walk, "n_cands": len(cands),
            "tonight": len(tonight), "tonight_wait": round(float(np.mean(waits)), 1) if waits else None,
        })

    vans = []
    for v in sim.fleet:
        label = f"E{v.vid + 1}" if v.is_ev else f"G{v.vid - fleet.n_ev + 1}"
        vans.append({
            "id": v.vid, "label": label, "ev": v.is_ev, "cap": v.cap,
            "legs": [[round(t0), round(t1), int(a), int(b), int(ld),
                      round(net.dist[a, b] / M_PER_MILE, 3), round(soc, 1), bank.want("drive", a, b)]
                     for t0, t1, a, b, ld, soc in v.legs],
            "charges": [[round(a), round(b)] for a, b in v.charges],
        })
    vlabel = {v["id"]: v["label"] for v in vans}

    riders = [[round(r.t_req), int(r.o), int(r.pu_node), int(r.do_node),
               None if r.pu_t is None else round(r.pu_t),
               None if r.do_t is None else round(r.do_t),
               r.mode, r.status, r.pax, round(r.walk_s), round(r.earliest_pu), r.rid,
               bank.want("walk", r.o, r.pu_node) if r.mode == "walk+van" else -1] for r in sim.reqs]

    decisions = []
    for d in disp.decisions:
        decisions.append({
            "t": d["t"], "rid": d["rid"], "pax": d["pax"], "mode": d["mode"],
            "from": place(d["o"]), "to": place(d["d"]), "result": d["result"],
            "options": d["options"], "positions": d["positions"],
            "screened": [[vlabel[vid], eta] for vid, eta in d["screened"]],
            "costs": [[vlabel[vid], round(c)] for vid, c in d["costs"]],
            "vid": vlabel.get(d.get("vid")), "walk": d.get("walk", 0),
            "walk_rank": d.get("walk_rank", 0),
            "walk_to": place(d["pu_node"]) if d.get("walk") else None,
            "walk_m": round(float(net.walk_m[d["o"], d["pu_node"]])) if d.get("walk") else 0,
            "promised": d.get("promised"),
        })
    # re-optimization: riders moved to another van, queued riders finally placed
    byrid = {r.rid: r for r in sim.reqs}
    for m in disp.moves:
        r = byrid[m["rid"]]
        decisions.append({
            "t": m["t"], "rid": m["rid"], "pax": r.pax, "mode": r.mode,
            "from": place(r.o), "to": place(r.d),
            "result": "placed" if m["from_vid"] is None else "moved",
            "vid": vlabel[m["to_vid"]], "from_vid": vlabel.get(m["from_vid"]),
            "eta": m["eta"], "promised": round(r.promised_pu),
            "pool": m["pool"], "moved": m["moved"], "saving": m["saving"],
        })
    decisions.sort(key=lambda d: d["t"])  # stable: a tick's assignments stay before its re-plan

    snaps = [[t, vlabel[vid], [[n, k, rid, eta] for n, k, rid, eta in stops]]
             for t, vid, stops in disp.snapshots]

    data = {
        "bbox": BBOX, "nodes": [q(la, lo) for la, lo in zip(net.lat, net.lon)],
        "approved": [[int(i), round(float(density[i]), 1)] for i in net.approved.nonzero()[0]],
        "depot": sim.depot, "start": simcfg.service_start_s, "end": simcfg.service_end_s,
        "streets": streets(),
        "bus": bus_json(transit, bank) if sim.P.buses else [],
        "names": list(net.names), "buildings": buildings, "vans": vans, "riders": riders,
        "decisions": decisions, "snapshots": snaps,
        "battery_kwh": fleet.battery_kwh,
        "area": [q(la, lo) for la, lo in SERVICE_AREA],
        "scenario": {"name": args.scenario, "engine": disp.engine.name, "label": disp.engine.label,
                     "walk_points": sim.P.walk_points, "bus_handoffs": sim.P.bus_handoffs,
                     "buses": sim.P.buses},
        "policy": {k: getattr(sim.P, k) for k in ("screen_k", "walk_max_s", "walk_candidates",
                                                    "pickup_slip_s", "max_wait_s", "reopt_freeze_s",
                                                    "reopt_early_s", "reopt_max_switches")},
        "kpis": {n: {k: (None if v != v else round(float(v), 2)) for k, v in summarize(s).items()}
                 for n, s in sims.items()},
        "demand": {"night": args.night, "requests": len(df)},
    }
    data["paths"] = bank.build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} kB): {len(decisions)} decisions, "
          f"{len(snaps)} schedule snapshots")


if __name__ == "__main__":
    main()

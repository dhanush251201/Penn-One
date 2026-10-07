"""CLI: run scenarios over one or more evenings and compare against baseline.

    python -m penn_twin.run                         # synthetic demand, all scenarios
    python -m penn_twin.run --trips data/penn.csv   # real trip export
    python -m penn_twin.run --offline               # no OSRM (haversine roads)
"""
import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .config import SCENARIOS, FleetConfig, SimConfig, scenario
from .demand import load_trips_csv, synthesize, to_requests
from .history import pickup_density
from .metrics import summarize, trips_frame
from .network import Network
from .sim import Simulator
from .transit import Transit

KEY_METRICS = ["served_pct", "vehicle_miles", "deadhead_pct", "rides_per_veh_hr",
               "pooled_pct", "wait_mean_min", "wait_p90_min", "door_to_door_mean_min",
               "max_pickup_slip_min", "kwh_eq_per_pax_mile", "kg_co2", "ev_mile_share_pct",
               "walked_riders", "bus_handoffs"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--trips", help="CSV of real trips (see demand.load_trips_csv)")
    ap.add_argument("--nights", type=int, default=3, help="synthetic evenings to average over")
    ap.add_argument("--trips-per-night", type=int, default=300)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--n-ev", type=int, default=FleetConfig.n_ev)
    ap.add_argument("--n-ice", type=int, default=FleetConfig.n_ice)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default="results")
    args = ap.parse_args(argv)

    net = Network.build(offline=args.offline)
    print(f"[network] {net.n} nodes ({net.source}), {net.approved.sum()} approved pickup points")
    fleet = FleetConfig(n_ev=args.n_ev, n_ice=args.n_ice)
    simcfg = SimConfig()
    transit = Transit(net, lambda a, b: net.dur[a, b] * simcfg.congestion)
    density = pickup_density(net)  # historical pickups/evening near each node

    if args.trips:
        nights = [load_trips_csv(args.trips)]
    else:
        nights = [synthesize(args.trips_per_night, seed=s) for s in range(args.nights)]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in args.scenarios.split(","):
        pol = scenario(name)
        for k, df in enumerate(nights):
            sim = Simulator(net, to_requests(df, net), pol, fleet, simcfg, transit, density).run()
            m = summarize(sim)
            rows.append({"scenario": name, "night": k, **m})
            trips_frame(sim).to_csv(out / f"trips_{name}_night{k}.csv", index=False)
            print(f"  {name:<11} night {k}: served {m['served_pct']:.1f}%  "
                  f"miles {m['vehicle_miles']:.0f}  deadhead {m['deadhead_pct']:.1f}%  "
                  f"wait {m['wait_mean_min']:.1f} min")

    res = pd.DataFrame(rows)
    res.to_csv(out / "metrics_by_night.csv", index=False)
    mean = res.groupby("scenario", sort=False)[KEY_METRICS].mean()
    if "baseline" in mean.index:
        b = mean.loc["baseline"]
        mean["miles_vs_base_pct"] = 100 * (mean.vehicle_miles / b.vehicle_miles - 1)
        mean["co2_vs_base_pct"] = 100 * (mean.kg_co2 / b.kg_co2 - 1)
    mean.to_csv(out / "summary.csv")
    (out / "config.json").write_text(json.dumps(
        {"fleet": asdict(fleet), "sim": asdict(simcfg),
         "policies": {n: asdict(scenario(n)) for n in args.scenarios.split(",")},
         "network": {"source": net.source, "nodes": int(net.n)},
         "demand": args.trips or f"synthetic x{args.nights} nights"}, indent=2))
    with pd.option_context("display.width", 200, "display.precision", 2):
        print("\n=== mean over nights ===")
        print(mean.T)
    print(f"\nwrote {out}/")


if __name__ == "__main__":
    main()

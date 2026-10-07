# Penn-One digital twin (Phase 1)

Simulation of Penn Transit's evening on-demand van service, used to measure the
baseline and test the three levers from the proposal.

```
python3 -m penn_twin.run                        # 3 synthetic evenings, all scenarios
python3 -m penn_twin.run --nights 5 --out results
python3 -m penn_twin.run --trips data/penn_trips.csv   # real Penn export
python3 -m penn_twin.run --offline              # no internet: approximate roads
python3 -m penn_twin.run --scenarios baseline,pooling --n-ev 5 --n-ice 3
```

Outputs: `summary.csv` (mean KPIs per scenario, % change vs. baseline),
`metrics_by_night.csv`, `trips_<scenario>_night<k>.csv` (one row per request), `config.json`.

## Model

| Piece | File | What it does |
|---|---|---|
| Road network | `network.py` | 150 m grid over University City snapped to roads; all-pairs drive time/distance from OSRM (cached in `data/cache/`). |
| Walking + drawn routes | `streets.py` | OpenStreetMap sidewalks, footpaths, crossings and streets. Walking distance is the shortest path on this network, never a straight line. Van, bus and walk paths on the maps are routed on it. |
| History | `history.py` | Pickups per evening near each point over 30 past evenings. Walk-to points are ranked by this. |
| Demand | `demand.py` | Synthetic evenings (6 pm–3 am, campus→home skew late at night) **or** `load_trips_csv` for real data. |
| Dispatcher | `sim.py: Dispatcher` | Cheapest insertion with hard constraints: max wait, ride-time cap, **confirmed pickup never slips > 5 min**, seat capacity, EV range (must finish route + return to depot above reserve). |
| Simulator | `sim.py: Simulator` | 30 s dispatch loop, vehicle motion, EV charging at depot, energy and emissions accounting. |
| Fixed routes | `transit.py` | Two evening bus loops with a fixed headway. |
| KPIs | `metrics.py` | Deadhead miles, rides (pooled) per vehicle-hour, waits, energy per passenger-mile, CO2. |

Scenarios (`config.py`):

* `baseline`: no pooling; each van serves one party at a time.
* `pooling`: lever 1, dynamic pooling.
* `pool+walk`: lever 2. Opt-in walks of 2 minutes or less on real sidewalks, to approved, well-lit points only, ranked by historical pickups. Never offered to riders with accessibility needs.
* `pool+bus`: lever 3. First- or last-mile handoff to a bus. The bus is only the first or last leg, so there is at most one transfer.
* `all_levers`: all three levers together.

The Dispatcher is separate from the Simulator so the Phase 2 shadow pilot can feed it
live requests and van positions instead of simulated ones.

## Real data drop-in

CSV columns: `request_time` (timestamp) or `request_time_s`, `origin_lat`, `origin_lon`,
`dest_lat`, `dest_lon`; optional `party_size`, `accessible`. Fleet size and mix go
in `FleetConfig`; service hours and emission factors go in `SimConfig`.

## Placeholders to replace with Penn's real values

* **Baseline policy.** Penn's live dispatch may already pool rides. If it does, `baseline` overstates the savings. Calibrate it against real deadhead and wait numbers.
* **Fleet.** 5 electric vans with 6 seats and 1 gas van with 8 seats. Energy figures assume Ford E-Transit-class EVs and a 15 mpg gas van.
* **Demand.** About 300 requests per night, with an assumed hourly profile and landmark weights.
* **Approved walk points.** Currently any road node on a major corridor (`geo.APPROVED_CORRIDORS`) plus the bus stops. Public Safety needs to supply the real list.
* **Bus loops and stops.** `geo.BUS_ROUTES` holds approximate coordinates with a 15-minute headway from 6 pm to midnight.
* **Simplifications.** Vans travel between road nodes about 150 m apart, a leg is locked once the van departs, idle vans don't rebalance, and the 1.15× congestion factor is a flat guess. The 150 m spacing also limits which walk-to points can be found. Penn's real list of approved points will fix that.

## Pages

```
python3 -m penn_twin.export_viz && python3 -m penn_twin.export_ops && python3 -m penn_twin.build_page
```
`viz/replay.html` compares two scenarios side by side. `viz/ops.html` is the operator console: a map with labeled buildings, the optimizer's step-by-step decisions, and every van's schedule.

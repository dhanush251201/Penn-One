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
| Service area | `geo.py: SERVICE_AREA` | The evening van boundary: University City, Powelton, West Philadelphia to 52nd St, part of Kingsessing, the Grays Ferry depot, and Center City from the Schuylkill to 20th St. Every simulated request starts and ends inside it. |
| Road network | `network.py` | 150 m grid over the service area snapped to roads; all-pairs drive time/distance from OSRM (cached in `data/cache/`, rebuilt when the boundary changes). |
| Walking + drawn routes | `streets.py` | OpenStreetMap sidewalks, footpaths, crossings and streets. Walking distance is the shortest path on this network, never a straight line. Van, bus and walk paths on the maps are routed on it. |
| History | `history.py` | Pickups per evening near each point over 30 past evenings. Walk-to points are ranked by this. |
| Demand | `demand.py` | Synthetic evenings (6 pm–3 am, campus→home skew late at night) **or** `load_trips_csv` for real data. |
| Dispatcher | `sim.py: Dispatcher` | Route evaluation with hard constraints shared by every engine: max wait, ride-time cap, **confirmed pickup never slips > 5 min**, seat capacity, EV range (must finish route + return to depot above reserve). Also the walk and bus levers, quoting, and decision logs. |
| Optimization engines | `engines/` | Decide which van serves each request and in what stop order. Picked per scenario by `Policy.engine`; see below. |
| Simulator | `sim.py: Simulator` | 30 s dispatch loop, vehicle motion, EV charging at depot, energy and emissions accounting. |
| Fixed routes | `transit.py` | The three evening Penn Bus loops (Rittenhouse Square, Baltimore/Spruce, Powelton/Spruce) run to Penn Transportation's published timetable: every 20 min from 4 PM, last trip 11 PM. A rider handed to a bus is always dropped at the stop before the last bus leaves. |
| KPIs | `metrics.py` | Deadhead miles, rides (pooled) per vehicle-hour, waits, energy per passenger-mile, CO2. |

Optimization engines (`engines/`):

| Engine | File | Method |
|---|---|---|
| `direct` | `engines/direct.py` | No pooling: each van carries one party at a time. The no-optimization reference. |
| `greedy` | `engines/greedy.py` | Dynamic pooling: cheapest insertion into routes with riders on board. A placement is final. |
| `reopt` | `engines/reopt.py` | `greedy`, then on each new request re-plans every rider still waiting for pickup across all vans (regret insertion, then relocate/swap local search). Riders on board keep their van. A waiting rider's pickup stays between 2 minutes before and 5 minutes after the quote; they are locked once their van is 3 minutes away and change van at most once. Applied only if the fleet-wide cost drops and nobody loses their seat. |

All three share the cheapest-insertion search in `engines/base.py`. To add an engine,
subclass `Engine` and register it in `engines/__init__.py`.

Scenarios (`config.py`):

* `baseline`: `direct` engine; each van serves one party at a time.
* `pooling`: lever 1, `greedy` engine.
* `pool+reopt`: `reopt` engine. Compare against `pooling` to see what re-optimization adds.
* `pool+walk`: lever 2 on `greedy`. Opt-in walks of 2 minutes or less on real sidewalks, to approved, well-lit points only, ranked by historical pickups. Never offered to riders with accessibility needs.
* `pool+bus`: lever 3 on `greedy`. First- or last-mile handoff to a bus. The bus is only the first or last leg, so there is at most one transfer.
* `all_levers`: all three levers on `greedy`.
* `baseline-nobus`, `pooling-nobus`, `pool+reopt-nobus`, `pool+bus-nobus`: the same with no Penn Bus at all (`Policy.buses=False`). Buses only matter through handoffs, so the first three match their twins exactly and `pool+bus-nobus` matches `pooling`.

The Dispatcher and engines are separate from the Simulator so the Phase 2 shadow pilot
can feed them live requests and van positions instead of simulated ones.

## Real data drop-in

CSV columns: `request_time` (timestamp) or `request_time_s`, `origin_lat`, `origin_lon`,
`dest_lat`, `dest_lon`; optional `party_size`, `accessible`. Fleet size and mix go
in `FleetConfig`; service hours and emission factors go in `SimConfig`.

## Placeholders to replace with Penn's real values

* **Baseline policy.** Penn's live dispatch may already pool rides. If it does, `baseline` overstates the savings. Calibrate it against real deadhead and wait numbers.
* **Fleet.** 5 electric vans with 6 seats and 1 gas van with 8 seats. Energy figures assume Ford E-Transit-class EVs and a 15 mpg gas van.
* **Demand.** About 300 requests per night, with an assumed hourly profile and landmark weights. The Center City landmark (Fitler Sq / Rittenhouse west) and its weight are a guess.
* **Service boundary.** `geo.SERVICE_AREA` was traced by hand from the PennTransit app's service map, not from a boundary file.
* **Approved walk points.** Currently any road node on a major corridor (`geo.APPROVED_CORRIDORS`) plus the bus stops. Public Safety needs to supply the real list.
* **Bus stops.** `geo.BUS_ROUTES` uses the real routes and timetable from [transportation.upenn.edu/schedules-and-stops](https://transportation.upenn.edu/schedules-and-stops) (retrieved 2026-10-07), but stop positions are placed from OpenStreetMap buildings and intersections, not Penn's stop list. The timetable gives minutes past the hour only, so the drive from the last timepoint back to the first stop uses road time.
* **Simplifications.** Vans travel between road nodes about 150 m apart, a leg is locked once the van departs, idle vans don't rebalance, and the 1.15× congestion factor is a flat guess. The 150 m spacing also limits which walk-to points can be found. Penn's real list of approved points will fix that.

## Pages

```
python3 -m penn_twin.export_viz --compare pooling,pool+reopt,pool+bus,baseline-nobus,pooling-nobus,pool+reopt-nobus
python3 -m penn_twin.export_ops && python3 -m penn_twin.build_page
```
`viz/replay.html` compares scenarios side by side on synced maps. The command above gives two rows: no optimization, pooling, pooling + re-optimization, and pooling + bus handoffs with Penn Bus, then the first three again with no buses at all. `--compare pooling,pool+reopt` shows no optimization, pooling, and pooling + re-optimization instead. `viz/ops.html` is the operator console for one evening of `pool+reopt` (`export_ops --scenario` picks another): a map of the service area with labeled buildings, the optimizer's step-by-step decisions including every re-plan, and every van's schedule. On both pages a red tint marks everything outside the on-demand van area.

Both pages are published to GitHub Pages at https://dhanush251201.github.io/Penn-One/ (the replay) and https://dhanush251201.github.io/Penn-One/ops.html by `.github/workflows/pages.yml` whenever a push to `main` changes them. Rebuild them with the commands above, commit, and push to update the site.

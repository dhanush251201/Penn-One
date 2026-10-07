"""Baseline KPIs named in the proposal + per-trip export."""
import numpy as np
import pandas as pd

from .geo import M_PER_MILE


def trips_frame(sim):
    rows = []
    for r in sim.reqs:
        rows.append(dict(
            rid=r.rid, t_req=r.t_req, status=r.status, mode=r.mode, vid=r.vid,
            party=r.pax, accessible=r.accessible, shared=r.shared,
            direct_min=r.direct_s / 60,
            direct_mi=sim.net.dist[r.o, r.d] / M_PER_MILE,
            walk_min=r.walk_s / 60,
            van_wait_min=None if r.pu_t is None else (r.pu_t - r.earliest_pu) / 60,
            pickup_slip_min=(None if r.pu_t is None or r.promised_pu is None
                             else (r.pu_t - r.promised_pu) / 60),
            door_to_door_min=None if r.final_t is None else (r.final_t - r.t_req) / 60,
        ))
    return pd.DataFrame(rows)


def summarize(sim):
    S, F = sim.S, sim.F
    T = trips_frame(sim)
    served = T[T.status == "done"]
    fleet = sim.fleet
    veh_hours = len(fleet) * (S.service_end_s - S.service_start_s) / 3600
    miles = sum(v.miles for v in fleet)
    dead = sum(v.deadhead_miles for v in fleet)
    pax_mi = sum(v.pax_miles for v in fleet)
    gal = sum(v.gallons for v in fleet)
    # EV energy at the wall: battery draw / charger efficiency
    ev_kwh = sum(v.kwh for v in fleet) / F.charger_eff
    energy_kwh_eq = ev_kwh + gal * S.kwh_per_gal
    co2 = gal * S.kg_co2_per_gal + ev_kwh * S.kg_co2_per_kwh
    riders = served.party.sum()
    return {
        "requests": len(T),
        "served_pct": 100 * len(served) / max(len(T), 1),
        "vehicle_miles": miles,
        "deadhead_miles": dead,
        "deadhead_pct": 100 * dead / max(miles, 1e-9),
        "rides_per_veh_hr": len(served) / veh_hours,
        "pooled_rides_per_veh_hr": served.shared.sum() / veh_hours,
        "pooled_pct": 100 * served.shared.mean() if len(served) else 0,
        "wait_mean_min": served.van_wait_min.mean(),
        "wait_p90_min": served.van_wait_min.quantile(0.9),
        "door_to_door_mean_min": served.door_to_door_min.mean(),
        "max_pickup_slip_min": served.pickup_slip_min.max(),
        "kwh_eq_per_pax_mile": energy_kwh_eq / max(pax_mi, 1e-9),
        "kg_co2": co2,
        "kg_co2_per_rider": co2 / max(riders, 1),
        "ev_mile_share_pct": 100 * sum(v.miles for v in fleet if v.is_ev) / max(miles, 1e-9),
        "ev_charges": sum(v.n_charges for v in fleet),
        "walked_riders": int((served["mode"] == "walk+van").sum()),
        "bus_handoffs": int(served["mode"].isin(["van+bus", "bus+van"]).sum()),
        "stranded_after_bus": int(served.door_to_door_min.isna().sum()),
    }

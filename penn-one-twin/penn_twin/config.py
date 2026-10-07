"""All tunable parameters. Defaults are placeholders until Penn supplies real values."""
from dataclasses import dataclass, field, replace

from .geo import DEPOT


@dataclass
class FleetConfig:
    n_ev: int = 5
    n_ice: int = 1
    ev_capacity: int = 6              # seats per electric van
    ice_capacity: int = 8             # seats in the gas van
    battery_kwh: float = 68.0         # usable, Ford E-Transit class
    kwh_per_mile: float = 0.55
    reserve_frac: float = 0.15        # never dispatch below this state of charge
    charge_below_frac: float = 0.30   # idle EV goes to charge below this
    charge_to_frac: float = 0.85
    charger_kw: float = 19.2          # Level 2 at depot
    charger_eff: float = 0.90
    mpg: float = 15.0                 # combustion van


@dataclass
class SimConfig:
    service_start_s: int = 18 * 3600  # 6 pm
    service_end_s: int = 27 * 3600    # 3 am (next day)
    congestion: float = 1.15          # multiplier on OSRM free-flow durations
    dwell_pickup_s: int = 60
    dwell_drop_s: int = 30
    dispatch_interval_s: int = 30
    depot: tuple = DEPOT
    kg_co2_per_gal: float = 8.887
    kg_co2_per_kwh: float = 0.37      # PJM grid average (approx.)
    kwh_per_gal: float = 33.7         # gasoline energy equivalent


@dataclass
class Policy:
    name: str = "baseline"
    pooling: bool = False             # lever 1: insert into routes with riders onboard
    walk_points: bool = False         # lever 2: opt-in short walks to approved points
    bus_handoffs: bool = False        # lever 3: first/last-mile handoff to fixed route

    # rider guarantees
    max_wait_s: int = 1800            # request dropped if no pickup possible within this
    pickup_slip_s: int = 300          # a confirmed pickup never slips more than this
    max_ride_factor: float = 1.5      # ride time <= factor * direct + extra
    max_ride_extra_s: int = 300
    wait_weight: float = 0.5          # cost weight on new rider's wait vs. added van time

    # lever 2
    walk_max_s: int = 120
    walk_opt_in: float = 0.5
    walk_weight: float = 1.0
    walk_candidates: int = 3          # walk-to points tried per rider, ranked by historical pickups

    # optimizer
    screen_k: int = 4                 # vans given a full insertion search after the road-ETA screen

    # lever 3
    bus_opt_in: float = 0.4
    bus_walk_max_s: int = 300         # walk to/from bus stop
    bus_max_extra_s: int = 600        # accept handoff only if <= this slower than direct van
    van_leg_max_frac: float = 0.6     # van leg must be <= this fraction of the direct trip


SCENARIOS = {
    "baseline":      Policy("baseline"),
    "pooling":       Policy("pooling", pooling=True),
    "pool+walk":     Policy("pool+walk", pooling=True, walk_points=True),
    "pool+bus":      Policy("pool+bus", pooling=True, bus_handoffs=True),
    "all_levers":    Policy("all_levers", pooling=True, walk_points=True, bus_handoffs=True),
}


def scenario(name, **overrides):
    return replace(SCENARIOS[name], **overrides)

"""Plan primitives shared by the simulator and the dispatch engines: a van and its stops."""
from dataclasses import dataclass, field


@dataclass(eq=False)
class Stop:
    req: object
    kind: str  # "P" pickup | "D" dropoff

    @property
    def node(self):
        return self.req.pu_node if self.kind == "P" else self.req.do_node


@dataclass(eq=False)
class Vehicle:
    vid: int
    is_ev: bool
    cap: int
    node: int
    avail_t: float
    soc_kwh: float = 0.0
    route: list = field(default_factory=list)
    onboard: dict = field(default_factory=dict)
    charging_until: float = -1.0
    miles: float = 0.0
    deadhead_miles: float = 0.0
    pax_miles: float = 0.0
    kwh: float = 0.0       # drawn from battery
    gallons: float = 0.0
    n_charges: int = 0
    charge_kwh_wall: float = 0.0
    legs: list = field(default_factory=list)     # (depart, arrive, from, to, load, soc_after)
    charges: list = field(default_factory=list)  # (start, end)

    @property
    def load(self):
        return sum(r.pax for r in self.onboard.values())

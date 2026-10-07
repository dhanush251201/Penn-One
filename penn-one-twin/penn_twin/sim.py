"""Discrete-time digital twin of the on-demand van service.

The simulator owns time and vehicle motion; the Dispatcher only decides where new
requests go. That split is deliberate: in the Phase 2 shadow pilot the same
Dispatcher is fed live requests and fleet positions instead of simulated ones.

Simplifications: vehicles move node-to-node using matrix travel times, a leg is
locked once the van departs (no mid-leg diversion), and idle vans wait where they
dropped off (no rebalancing).
"""
from dataclasses import dataclass, field

import numpy as np

from .geo import M_PER_MILE


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


class Dispatcher:
    """Cheapest-insertion dispatcher with hard rider guarantees and range checks."""

    def __init__(self, net, policy, fleet_cfg, sim_cfg, depot_node, transit=None, density=None):
        self.net, self.P, self.F, self.S = net, policy, fleet_cfg, sim_cfg
        self.depot = depot_node
        self.transit = transit
        self.tt_mat = net.dur * sim_cfg.congestion
        self.density = np.zeros(net.n) if density is None else density
        # Walk-to candidates per node: approved points within walk_max_s along real
        # sidewalks/footpaths, ranked by historical pickup density, then shorter walk.
        W = net.walk_m / 1.3
        self.walk_cands = []
        for i in range(net.n):
            c = np.flatnonzero((W[i] <= policy.walk_max_s) & net.approved & (np.arange(net.n) != i))
            c = sorted(c, key=lambda j: (-self.density[j], W[i, j]))
            self.walk_cands.append([int(j) for j in c[:policy.walk_candidates]])
        self.decisions = []   # one entry per dispatch decision (for the operator console)
        self.snapshots = []   # (time, vid, [(node, kind, rid, eta), ...]) after each change

    def tt(self, a, b):
        return self.tt_mat[a, b]

    # ---- route evaluation ---------------------------------------------
    def evaluate(self, v, route, now, etas=None):
        """Simulate a candidate stop list. Returns (end_time, meters, pickup_times) or None."""
        t = max(v.avail_t, now)
        node, load, meters = v.node, v.load, 0.0
        pu = {r.rid: r.pu_t for r in v.onboard.values()}
        for s in route:
            r = s.req
            t += self.tt_mat[node, s.node]
            meters += self.net.dist[node, s.node]
            node = s.node
            if s.kind == "P":
                t = max(t, r.earliest_pu)
                if t > r.latest_pu:
                    return None
                load += r.pax
                if load > v.cap:
                    return None
                pu[r.rid] = t
                if etas is not None:
                    etas.append(t)
                t += self.S.dwell_pickup_s
            else:
                if t - pu[r.rid] > r.max_ride_s:
                    return None
                load -= r.pax
                if etas is not None:
                    etas.append(t)
                t += self.S.dwell_drop_s
        if v.is_ev:
            need = (meters + self.net.dist[node, self.depot]) / M_PER_MILE * self.F.kwh_per_mile
            if need > v.soc_kwh - self.F.reserve_frac * self.F.battery_kwh:
                return None
        return t, meters, pu

    def best_insertion(self, v, req, now):
        base = self.evaluate(v, v.route, now)
        if base is None:  # shouldn't happen, but never make a bad route worse
            return None
        base_t = base[0]
        R = v.route
        L = len(R)
        positions = ([(i, j) for i in range(L + 1) for j in range(i, L + 1)]
                     if self.P.pooling else [(L, L)])
        P, D = Stop(req, "P"), Stop(req, "D")
        best = None
        for i, j in positions:
            cand = R[:i] + [P] + R[i:j] + [D] + R[j:]
            ev = self.evaluate(v, cand, now)
            if ev is None:
                continue
            end_t, _, pu = ev
            cost = (end_t - base_t) + self.P.wait_weight * (pu[req.rid] - req.t_req)
            if best is None or cost < best[0]:
                best = (cost, cand, pu)
        self._positions += len(positions)
        return best

    def screen(self, fleet, node, now):
        """Pre-screen: rank vans by road-network ETA to `node` (OSRM drive time from where the
        van is next free), keep the closest screen_k for the full insertion search."""
        avail = [v for v in fleet if v.charging_until <= now]
        def eta(v):
            anchor = v.route[-1].node if (v.route and not self.P.pooling) else v.node
            return max(v.avail_t - now, 0) + self.tt_mat[anchor, node]
        avail.sort(key=eta)
        return avail[:self.P.screen_k], [(v.vid, round(eta(v))) for v in avail]

    def snapshot(self, v, now):
        etas = []
        self.evaluate(v, v.route, now, etas)
        self.snapshots.append((now, v.vid, [(s.node, s.kind, s.req.rid, round(e))
                                            for s, e in zip(v.route, etas)]))

    # ---- assignment ----------------------------------------------------
    def _set_leg(self, req, o, d, earliest, walk=0.0):
        req.pu_node, req.do_node = o, d
        req.earliest_pu = earliest
        req.latest_pu = req.t_req + self.P.max_wait_s + walk
        direct = self.tt_mat[o, d]
        req.max_ride_s = self.P.max_ride_factor * direct + self.P.max_ride_extra_s

    def plan_new(self, req):
        """First time a request is seen: fill in direct time and (lever 3) a bus plan."""
        req.direct_s = self.tt_mat[req.o, req.d]
        self._set_leg(req, req.o, req.d, req.t_req)
        if (self.P.bus_handoffs and self.transit and not req.accessible
                and req.u_bus < self.P.bus_opt_in):
            plan = self.transit.plan(req, self.net, self.tt, self.P)
            if plan:
                req.bus_plan = plan
                req.mode = plan["kind"]
                if plan["kind"] == "van+bus":
                    self._set_leg(req, plan["van_o"], plan["van_d"], req.t_req)
                else:
                    self._set_leg(req, plan["van_o"], plan["van_d"], plan["bus_arrival"])
                    req.walk_s = plan["walk_start"]

    def revert_to_direct(self, req):
        req.bus_plan, req.mode, req.walk_s = None, "van", 0.0
        self._set_leg(req, req.o, req.d, req.t_req)

    def assign(self, req, fleet, now):
        options = [(req.pu_node, 0.0)]
        if (self.P.walk_points and req.mode == "van" and not req.accessible
                and req.u_walk < self.P.walk_opt_in):
            options += [(int(c), self.net.walk_s(req.o, c)) for c in self.walk_cands[req.o]]

        best = None
        self._positions = 0
        per_van = {}
        orig = (req.pu_node, req.earliest_pu, req.latest_pu, req.max_ride_s)
        screened = None
        for node, walk in options:
            if walk > 0:
                self._set_leg(req, node, req.do_node, req.t_req + walk, walk)
            vans, ranking = self.screen(fleet, node, now)
            if screened is None:
                screened = ranking
            for v in vans:
                ins = self.best_insertion(v, req, now)
                if ins is None:
                    continue
                cost = ins[0] + self.P.walk_weight * walk
                if v.vid not in per_van or cost < per_van[v.vid]:
                    per_van[v.vid] = cost
                if best is None or cost < best[0]:
                    best = (cost, v, ins[1], ins[2], node, walk)
            req.pu_node, req.earliest_pu, req.latest_pu, req.max_ride_s = orig

        log = dict(t=now, rid=req.rid, o=req.o, d=req.d, pax=req.pax, mode=req.mode,
                   options=len(options), screened=screened or [], positions=self._positions,
                   costs=sorted(per_van.items(), key=lambda kv: kv[1])[:3])
        if best is None:
            if not getattr(req, "_queued_logged", False):  # log the first miss only
                req._queued_logged = True
                log["result"] = "queued"
                self.decisions.append(log)
            return False

        cost, v, route, pu, node, walk = best
        if walk > 0:
            self._set_leg(req, node, req.do_node, req.t_req + walk, walk)
            req.mode, req.walk_s = "walk+van", walk
        v.route = route
        v.avail_t = max(v.avail_t, now)
        req.vid, req.status = v.vid, "assigned"
        req.promised_pu = pu[req.rid]
        # the guarantee: once quoted, this pickup can't slip more than pickup_slip_s
        req.latest_pu = min(req.latest_pu, req.promised_pu + self.P.pickup_slip_s)
        log.update(result="assigned", vid=v.vid, pu_node=node, walk=round(walk),
                   walk_rank=(self.walk_cands[req.o].index(node) + 1 if walk > 0 else 0),
                   promised=round(req.promised_pu), cost=round(cost), mode=req.mode)
        self.decisions.append(log)
        self.snapshot(v, now)
        return True


class Simulator:
    def __init__(self, net, requests, policy, fleet_cfg, sim_cfg, transit=None, density=None):
        self.net, self.P, self.F, self.S = net, policy, fleet_cfg, sim_cfg
        self.reqs = sorted(requests, key=lambda r: r.t_req)
        self.depot = int(net.nearest_node(*sim_cfg.depot)[0])
        self.disp = Dispatcher(net, policy, fleet_cfg, sim_cfg, self.depot, transit, density)
        start = sim_cfg.service_start_s
        self.fleet = ([Vehicle(k, True, fleet_cfg.ev_capacity, self.depot, start,
                               soc_kwh=fleet_cfg.battery_kwh * 0.95)
                       for k in range(fleet_cfg.n_ev)]
                      + [Vehicle(fleet_cfg.n_ev + k, False, fleet_cfg.ice_capacity, self.depot, start)
                         for k in range(fleet_cfg.n_ice)])

    # ---- vehicle motion -----------------------------------------------
    def _drive(self, v, to, load, depart):
        m = self.net.dist[v.node, to]
        mi = m / M_PER_MILE
        v.miles += mi
        v.pax_miles += mi * load
        if load == 0:
            v.deadhead_miles += mi
        if v.is_ev:
            kwh = mi * self.F.kwh_per_mile
            v.kwh += kwh
            v.soc_kwh -= kwh
        else:
            v.gallons += mi / self.F.mpg
        t = self.disp.tt(v.node, to)
        v.legs.append((depart, depart + t, v.node, to, load, v.soc_kwh))
        v.node = to
        return t

    def advance(self, v, now):
        """Commit every leg that has started by `now`."""
        while True:
            if v.charging_until > now:
                return
            if v.route:
                s = v.route[0]
                r = s.req
                lead = self.disp.tt(v.node, s.node)
                depart = v.avail_t
                if s.kind == "P":  # wait in place rather than at the curb if pickup is far off
                    depart = max(depart, r.earliest_pu - lead)
                if depart > now:
                    return
                v.route.pop(0)
                arr = depart + self._drive(v, s.node, v.load, depart)
                if s.kind == "P":
                    start = max(arr, r.earliest_pu)
                    if v.onboard:
                        r.shared = True
                        for o in v.onboard.values():
                            o.shared = True
                    r.pu_t = start
                    v.onboard[r.rid] = r
                    v.avail_t = start + self.S.dwell_pickup_s
                else:
                    r.do_t = arr
                    r.status = "done"
                    del v.onboard[r.rid]
                    v.avail_t = arr + self.S.dwell_drop_s
                    self._finish(r)
            elif v.is_ev and v.soc_kwh < self.F.charge_below_frac * self.F.battery_kwh:
                depart = v.avail_t
                if depart > now:
                    return
                arr = depart + self._drive(v, self.depot, 0, depart)
                add = self.F.charge_to_frac * self.F.battery_kwh - v.soc_kwh
                hrs = add / (self.F.charger_kw * self.F.charger_eff)
                v.charge_kwh_wall += add / self.F.charger_eff
                v.soc_kwh += add
                v.n_charges += 1
                v.charging_until = v.avail_t = arr + hrs * 3600
                v.charges.append((arr, v.charging_until))
            else:
                return

    def _finish(self, r):
        plan = r.bus_plan
        if r.mode == "van+bus":
            route = plan["route"]
            dep = route.next_departure(plan["k1"], r.do_t + 30)
            r.final_t = (None if dep is None
                         else dep + route.ride_s(plan["k1"], plan["k2"]) + plan["walk_end"])
        else:
            r.final_t = r.do_t

    # ---- main loop ------------------------------------------------------
    def run(self):
        S, P = self.S, self.P
        t = S.service_start_s
        idx, pending = 0, []
        while True:
            while idx < len(self.reqs) and self.reqs[idx].t_req <= t:
                r = self.reqs[idx]
                self.disp.plan_new(r)
                pending.append(r)
                idx += 1
            for v in self.fleet:
                self.advance(v, t)
            still = []
            for r in pending:
                if self.disp.assign(r, self.fleet, t):
                    continue
                if r.bus_plan is not None:  # handoff plan not servable -> fall back to direct
                    self.disp.revert_to_direct(r)
                    if self.disp.assign(r, self.fleet, t):
                        continue
                if t - r.t_req >= P.max_wait_s:
                    r.status = "unserved"
                else:
                    still.append(r)
            pending = still
            done = (idx == len(self.reqs) and not pending
                    and all(not v.route for v in self.fleet))
            if t >= S.service_end_s and done:
                break
            t += S.dispatch_interval_s
        self.end_t = t
        return self

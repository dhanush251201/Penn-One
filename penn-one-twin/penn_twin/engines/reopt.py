"""Greedy insertion plus protected re-optimization on every new request.

New requests are first placed greedily. Then every rider still waiting for pickup, plus
any queued request, is re-planned across the whole fleet:

* Riders on board stay on their van and their drop-offs keep their order, but new stops
  may go between them within their ride-time cap.
* A waiting rider may move to another van or position as long as the pickup stays within
  [promised - reopt_early_s, promised + pickup_slip_s]. Once their van is within
  reopt_freeze_s they are locked, and nobody changes van more than reopt_max_switches.
* Two starting points are improved by local search (relocate, swap): the current plan
  and a regret-insertion rebuild. The winner is applied only if it beats the current plan
  on the fleet-wide cost and no assigned rider loses their seat.
"""
from ..model import Stop
from .greedy import GreedyEngine

EPS = 1.0  # s: improvements smaller than this are ignored


class ReoptEngine(GreedyEngine):
    name = "reopt"
    label = "Re-optimizing"

    def __init__(self, disp):
        super().__init__(disp)
        self.stats = {"runs": 0, "improved": 0}

    def committed(self, req):  # re-plans may bring the pickup forward, but not by much
        req.not_before = req.promised_pu - self.P.reopt_early_s

    def new_requests(self, fleet, now, queued):
        return self.reoptimize(fleet, now, queued)

    # ---- objective ------------------------------------------------------------
    def _route_cost(self, v, route, now):
        """One van's share of the fleet-wide objective: time until the van is free, plus
        weighted waits (request to pickup) and trip times (request to drop-off) of its riders."""
        etas = []
        ev = self.disp.evaluate(v, route, now, etas)
        if ev is None:
            return None
        cost = ev[0] - now
        for s, t in zip(route, etas):
            w = self.P.wait_weight if s.kind == "P" else self.P.reopt_ride_weight
            cost += w * (t - s.req.t_req)
        return cost

    def _best_insert(self, v, route, req, now, base):
        """Cheapest feasible pickup/drop-off positions for req: (added cost, new route) or None."""
        P, D = Stop(req, "P"), Stop(req, "D")
        best = None
        L = len(route)
        for i in range(L + 1):
            for j in range(i, L + 1):
                cand = route[:i] + [P] + route[i:j] + [D] + route[j:]
                c = self._route_cost(v, cand, now)
                if c is not None and (best is None or c - base < best[0]):
                    best = (c - base, cand)
        return best

    # ---- re-plan ----------------------------------------------------------------
    def reoptimize(self, fleet, now, queued=()):
        """Re-plan waiting riders and queued requests (see module docstring). Returns the
        queued requests that got placed."""
        P, disp = self.P, self.disp
        vans = {v.vid: v for v in fleet if v.charging_until <= now}
        pool = list(queued)
        for v in vans.values():
            etas = []
            if disp.evaluate(v, v.route, now, etas) is None:
                return []  # current plan already infeasible: leave it alone
            pool += [s.req for s, t in zip(v.route, etas)
                     if s.kind == "P" and t - now > P.reopt_freeze_s]
        if not pool:
            return []
        self.stats["runs"] += 1

        def switch(r, vid):
            return P.reopt_switch_s if r.vid is not None and r.vid != vid else 0.0

        def allowed(r):
            if r.vid is not None and r.switches >= P.reopt_max_switches:
                return [r.vid]
            return list(vans)

        def score(plan):  # (queued riders left without a van, fleet-wide cost)
            _, cost, where = plan
            return (sum(r.rid not in where for r in queued),
                    sum(cost.values()) + sum(switch(r, where[r.rid]) for r in pool if r.rid in where))

        current = ({vid: v.route for vid, v in vans.items()},
                   {vid: self._route_cost(v, v.route, now) for vid, v in vans.items()},
                   {r.rid: r.vid for r in pool if r.vid is not None})
        incumbent = score(current)
        best = None
        for plan in (current, self._regret_rebuild(vans, pool, now, allowed, switch)):
            if plan is None:
                continue
            self._local_search(plan, vans, pool, now, allowed, switch)
            if best is None or score(plan) < score(best):
                best = plan
        new = score(best)
        if not (new[0] < incumbent[0] or (new[0] == incumbent[0] and new[1] < incumbent[1] - EPS)):
            return []
        self.stats["improved"] += 1

        routes, _, where = best
        sig = lambda route: [(s.req.rid, s.kind) for s in route]
        eta = {}
        for vid, v in vans.items():
            if sig(routes[vid]) != sig(v.route):
                v.route = routes[vid]
                v.avail_t = max(v.avail_t, now)
                etas = []
                disp.evaluate(v, v.route, now, etas)
                eta.update((s.req.rid, t) for s, t in zip(v.route, etas) if s.kind == "P")
                disp.snapshot(v, now)
        placed, moves = [], []
        for r in pool:
            vid = where.get(r.rid)
            if vid is None or vid == r.vid:
                continue
            moves.append(dict(t=now, rid=r.rid, from_vid=r.vid, to_vid=vid, eta=round(eta[r.rid])))
            if r.vid is None:  # a queued request finally fits: quote it like assign() does
                r.vid, r.status = vid, "assigned"
                r.promised_pu = eta[r.rid]
                r.latest_pu = min(r.latest_pu, r.promised_pu + P.pickup_slip_s)
                self.committed(r)
                placed.append(r)
            else:  # the quote is kept; only the van changes
                r.vid = vid
                r.switches += 1
        # context for the operator console: riders considered, riders moved, cost saved (s)
        saving = round(incumbent[1] - new[1]) if new[0] == incumbent[0] else None
        for m in moves:
            m.update(pool=len(pool), moved=len(moves), saving=saving)
        disp.moves += moves
        return placed

    def _regret_rebuild(self, vans, pool, now, allowed, switch):
        """Pull every pool rider out, then reinsert the one with the most to lose first
        (largest gap between their best and second-best van). Returns a plan or None."""
        moving = {r.rid for r in pool}
        routes = {vid: [s for s in v.route if s.req.rid not in moving] for vid, v in vans.items()}
        cost = {vid: self._route_cost(v, routes[vid], now) for vid, v in vans.items()}
        if any(c is None for c in cost.values()):
            return None
        where, opts, todo = {}, {}, list(pool)
        while todo:
            pick = None
            for r in todo:
                cands = []
                for vid in allowed(r):
                    if (r.rid, vid) not in opts:
                        ins = self._best_insert(vans[vid], routes[vid], r, now, cost[vid])
                        opts[r.rid, vid] = None if ins is None else (ins[0] + switch(r, vid), ins[1])
                    if opts[r.rid, vid] is not None:
                        cands.append((opts[r.rid, vid][0], vid))
                if not cands:
                    continue
                cands.sort()
                regret = cands[1][0] - cands[0][0] if len(cands) > 1 else float("inf")
                if pick is None or (regret, -cands[0][0]) > pick[0]:
                    pick = ((regret, -cands[0][0]), r, cands[0][1])
            if pick is None:
                break
            _, r, vid = pick
            routes[vid] = opts[r.rid, vid][1]
            cost[vid] = self._route_cost(vans[vid], routes[vid], now)
            where[r.rid] = vid
            todo.remove(r)
            for o in todo:  # that van's route changed
                opts.pop((o.rid, vid), None)
        if any(r.vid is not None for r in todo):
            return None  # an assigned rider would lose their seat
        return routes, cost, where

    def _local_search(self, plan, vans, pool, now, allowed, switch, max_passes=10):
        """Improve a plan in place: place queued riders, then relocate single riders and swap
        pairs between vans while the fleet-wide cost drops."""
        routes, cost, where = plan

        def drop(vid, r):
            rest = [s for s in routes[vid] if s.req is not r]
            return rest, self._route_cost(vans[vid], rest, now)

        for _ in range(max_passes):
            improved = False
            for r in pool:  # insert: queued riders still without a van
                if r.rid in where:
                    continue
                cands = [(ins[0], vid, ins[1]) for vid in allowed(r)
                         if (ins := self._best_insert(vans[vid], routes[vid], r, now, cost[vid]))]
                if cands:
                    d, vid, route = min(cands, key=lambda c: c[0])
                    routes[vid], cost[vid], where[r.rid] = route, cost[vid] + d, vid
                    improved = True
            placed = [r for r in pool if r.rid in where]
            for r in placed:  # relocate: one rider to their best spot on any allowed van
                a = where[r.rid]
                rest, c_rest = drop(a, r)
                if c_rest is None:
                    continue
                for b in allowed(r):
                    route_b, cost_b = (rest, c_rest) if b == a else (routes[b], cost[b])
                    ins = self._best_insert(vans[b], route_b, r, now, cost_b)
                    if ins is None:
                        continue
                    if c_rest - cost[a] + ins[0] + switch(r, b) - switch(r, a) < -EPS:
                        routes[a], cost[a] = rest, c_rest
                        routes[b], cost[b] = ins[1], cost_b + ins[0]
                        where[r.rid] = b
                        improved = True
                        break
            for i, r1 in enumerate(placed):  # swap: two riders trade vans
                for r2 in placed[i + 1:]:
                    a, b = where[r1.rid], where[r2.rid]
                    if a == b or b not in allowed(r1) or a not in allowed(r2):
                        continue
                    ra, ca = drop(a, r1)
                    rb, cb = drop(b, r2)
                    if ca is None or cb is None:
                        continue
                    ia = self._best_insert(vans[a], ra, r2, now, ca)
                    ib = self._best_insert(vans[b], rb, r1, now, cb)
                    if ia is None or ib is None:
                        continue
                    delta = (ca + ia[0] - cost[a] + cb + ib[0] - cost[b] + switch(r1, b)
                             + switch(r2, a) - switch(r1, a) - switch(r2, b))
                    if delta < -EPS:
                        routes[a], cost[a] = ia[1], ca + ia[0]
                        routes[b], cost[b] = ib[1], cb + ib[0]
                        where[r1.rid], where[r2.rid] = b, a
                        improved = True
            if not improved:
                break

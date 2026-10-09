"""Penn Bus fixed routes on the same road network, run to the published timetable."""
import math

from .geo import BUS_FIRST_S, BUS_HEADWAY_S, BUS_LAST_S, BUS_ROUTES


class BusRoute:
    def __init__(self, name, nodes, offsets_s, tt, headway_s=BUS_HEADWAY_S,
                 start_s=BUS_FIRST_S, end_s=BUS_LAST_S, dwell_s=20):
        self.stops, self.offset = [], []
        for n, off in zip(nodes, offsets_s):  # drop stops that snapped to the same road node
            if int(n) not in self.stops:
                self.stops.append(int(n))
                self.offset.append(float(off))
        self.name = name
        self.headway, self.start, self.end = headway_s, start_s, end_s
        # The timetable ends at the last timepoint; the drive back to the first stop is not
        # published, so it comes from road travel time.
        self.loop_s = self.offset[-1] + tt(self.stops[-1], self.stops[0]) + dwell_s

    def next_departure(self, k, t):
        """Earliest time >= t a bus leaves stop index k, or None if service is over."""
        m = max(0, math.ceil((t - self.offset[k] - self.start) / self.headway))
        dep0 = self.start + m * self.headway
        return None if dep0 > self.end else dep0 + self.offset[k]

    def last_departure(self, k):
        """Time the last bus of the evening leaves stop index k."""
        return self.start + (self.end - self.start) // self.headway * self.headway + self.offset[k]

    def ride_s(self, k1, k2):
        r = self.offset[k2] - self.offset[k1]
        return r if r > 0 else r + self.loop_s


class Transit:
    def __init__(self, net, tt, headway_s=BUS_HEADWAY_S, start_s=BUS_FIRST_S, end_s=BUS_LAST_S):
        self.routes = []
        for name, stops in BUS_ROUTES.items():
            nodes = net.nearest_node([p[0] for _, p, _ in stops], [p[1] for _, p, _ in stops])
            self.routes.append(BusRoute(name, nodes, [m * 60 for _, _, m in stops], tt,
                                        headway_s, start_s, end_s))
        net.mark_approved({s for r in self.routes for s in r.stops})  # stops are lit, staffed corridors

    def plan(self, req, net, tt, policy, van_wait_est=300):
        """Best first/last-mile plan for a request, or None if a direct van is better.

        Bus is only ever the first or last leg (never van-bus-van), so riders face at
        most one transfer.
        """
        direct = req.direct_s
        best = None
        for route in self.routes:
            S = route.stops
            for k1, A in enumerate(S):
                for k2, B in enumerate(S):
                    if k1 == k2:
                        continue
                    ride = route.ride_s(k1, k2)
                    # van -> bus: van o->A, bus A->B, walk B->d
                    w_end = net.walk_s(B, req.d)
                    van = tt(req.o, A)
                    if w_end <= policy.bus_walk_max_s and van <= policy.van_leg_max_frac * direct:
                        dep = route.next_departure(k1, req.t_req + van_wait_est + van + 60)
                        if dep is not None:
                            total = dep + ride + w_end - req.t_req
                            if best is None or total < best["total"]:
                                best = dict(kind="van+bus", route=route, k1=k1, k2=k2,
                                            van_o=req.o, van_d=A, walk_end=w_end, total=total)
                    # bus -> van: walk o->A, bus A->B, van B->d
                    w_start = net.walk_s(req.o, A)
                    van = tt(B, req.d)
                    if w_start <= policy.bus_walk_max_s and van <= policy.van_leg_max_frac * direct:
                        dep = route.next_departure(k1, req.t_req + w_start)
                        if dep is not None:
                            arr_b = dep + ride
                            total = arr_b + 120 + van - req.t_req
                            if best is None or total < best["total"]:
                                best = dict(kind="bus+van", route=route, k1=k1, k2=k2,
                                            van_o=B, van_d=req.d, walk_start=w_start,
                                            bus_arrival=arr_b, total=total)
        if best and best["total"] <= direct + van_wait_est + policy.bus_max_extra_s:
            return best
        return None

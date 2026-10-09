"""Engine interface plus the cheapest-insertion search every engine builds on."""
from ..model import Stop


class Engine:
    """Places a request by cheapest insertion: screen vans by road ETA, try the allowed
    pickup/drop-off positions in each, keep the lowest cost. Subclasses choose the
    positions (`positions`) and where a van is measured from (`anchor`), and may react
    once a request is quoted (`committed`) or after each batch of arrivals (`new_requests`).
    """

    name = ""
    label = ""

    def __init__(self, disp):
        self.disp, self.P = disp, disp.P
        self.stats = {}
        self.positions_tried = 0

    # ---- what subclasses choose ---------------------------------------------
    def positions(self, L):
        """(pickup index, drop-off index) pairs to try in a route of L stops."""
        raise NotImplementedError

    def anchor(self, v):
        """Node the road-ETA screen measures a van from."""
        return v.node

    def committed(self, req):
        """Called once a request has been quoted a pickup time."""

    def new_requests(self, fleet, now, queued):
        """Called after each batch of new requests is dispatched. Returns the queued
        requests it placed, which then leave the retry queue."""
        return []

    # ---- shared search --------------------------------------------------------
    def screen(self, fleet, node, now):
        """Pre-screen: rank vans by road-network ETA to `node` (OSRM drive time from where the
        van is next free), keep the closest screen_k for the full insertion search."""
        avail = [v for v in fleet if v.charging_until <= now]
        def eta(v):
            return max(v.avail_t - now, 0) + self.disp.tt_mat[self.anchor(v), node]
        avail.sort(key=eta)
        return avail[:self.P.screen_k], [(v.vid, round(eta(v))) for v in avail]

    def best_insertion(self, v, req, now):
        base = self.disp.evaluate(v, v.route, now)
        if base is None:  # shouldn't happen, but never make a bad route worse
            return None
        base_t = base[0]
        R = v.route
        positions = self.positions(len(R))
        P, D = Stop(req, "P"), Stop(req, "D")
        best = None
        for i, j in positions:
            cand = R[:i] + [P] + R[i:j] + [D] + R[j:]
            ev = self.disp.evaluate(v, cand, now)
            if ev is None:
                continue
            end_t, _, pu = ev
            cost = (end_t - base_t) + self.P.wait_weight * (pu[req.rid] - req.t_req)
            if best is None or cost < best[0]:
                best = (cost, cand, pu)
        self.positions_tried += len(positions)
        return best

    def search(self, req, options, fleet, now):
        """Cheapest placement over pickup options and screened vans.

        Returns ((cost, van, route, pickup_times, pickup_node, walk_s) or None, trace),
        where trace is the screen ranking, positions tried and top van costs for the log.
        """
        best = None
        self.positions_tried = 0
        per_van = {}
        orig = (req.pu_node, req.earliest_pu, req.latest_pu, req.max_ride_s)
        screened = None
        for node, walk in options:
            if walk > 0:
                self.disp.set_leg(req, node, req.do_node, req.t_req + walk, walk)
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
        trace = dict(screened=screened or [], positions=self.positions_tried,
                     costs=sorted(per_van.items(), key=lambda kv: kv[1])[:3])
        return best, trace

"""No pooling: each van carries one party at a time."""
from .base import Engine


class DirectEngine(Engine):
    """Appends the pickup and drop-off to the end of a van's route, so a van finishes one
    party before starting the next. The van is still chosen by cheapest insertion; this is
    the no-optimization reference the other engines are measured against."""

    name = "direct"
    label = "Direct trips"

    def positions(self, L):
        return [(L, L)]

    def anchor(self, v):  # a van is next free where its last drop-off is
        return v.route[-1].node if v.route else v.node

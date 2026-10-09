"""Dynamic pooling by cheapest insertion."""
from .base import Engine


class GreedyEngine(Engine):
    """Tries every pickup/drop-off position in each screened van, including between riders
    already on board, and takes the cheapest. Greedy: once placed, a rider stays put."""

    name = "greedy"
    label = "Greedy insertion"

    def positions(self, L):
        return [(i, j) for i in range(L + 1) for j in range(i, L + 1)]

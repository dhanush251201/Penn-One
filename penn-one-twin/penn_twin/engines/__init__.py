"""Dispatch optimization engines.

An engine decides which van serves each request and where its stops go. What engines
share comes from the Dispatcher they are attached to: route evaluation (feasibility,
timing, rider guarantees, EV range), the pickup options the levers generate, and the
decision logs. Engines never move vans or advance time, so the same engine runs inside
the simulator or behind a live feed (Phase 2 shadow pilot).

    direct   one party per van at a time (no pooling): the no-optimization reference
    greedy   cheapest insertion into routes with riders on board (dynamic pooling)
    reopt    greedy, then on each new request re-plans every rider still waiting

To add one, subclass Engine and register it in ENGINES; scenarios select it by name
(`Policy.engine`).
"""
from .base import Engine
from .direct import DirectEngine
from .greedy import GreedyEngine
from .reopt import ReoptEngine

ENGINES = {e.name: e for e in (DirectEngine, GreedyEngine, ReoptEngine)}


def make_engine(name, dispatcher):
    if name not in ENGINES:
        raise ValueError(f"unknown engine {name!r}; choose from {', '.join(ENGINES)}")
    return ENGINES[name](dispatcher)

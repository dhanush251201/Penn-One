"""Historical pickup density, used to rank walk-to points.

A walk-to point that vans already visit often is the best place to send a rider:
the van is likely nearby and the stop pools with other pickups. Density is the
number of past pickups within a short walk (real sidewalk route) of each road node.

Built from past evenings: synthetic for now, or real exports via `trips_csvs`.
"""
import numpy as np

from .demand import load_trips_csv, synthesize


def pickup_density(net, nights=30, seed0=1000, trips_per_night=300, radius_m=150,
                   trips_csvs=None):
    """Average pickups per evening within `radius_m` walking distance of each node."""
    frames = ([load_trips_csv(p) for p in trips_csvs] if trips_csvs
              else [synthesize(trips_per_night, seed=seed0 + k) for k in range(nights)])
    counts = np.zeros(net.n)
    for df in frames:
        o = net.nearest_node(df.origin_lat.values, df.origin_lon.values)
        np.add.at(counts, o, 1)
    counts /= len(frames)
    near = net.walk_m <= radius_m
    return near.astype(float) @ counts


def peak_hour(net, node, nights=30, seed0=1000, trips_per_night=300, radius_m=150):
    """Hour of the evening with the most historical pickups near `node`."""
    hours = np.zeros(48)
    for k in range(nights):
        df = synthesize(trips_per_night, seed=seed0 + k)
        o = net.nearest_node(df.origin_lat.values, df.origin_lon.values)
        m = net.walk_m[node, o] <= radius_m
        np.add.at(hours, (df.request_time_s.values[m] // 3600).astype(int), 1)
    return int(hours.argmax()), hours / nights

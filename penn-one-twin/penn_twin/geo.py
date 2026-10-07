"""Service area, landmarks and small geo helpers for University City.

Coordinates are approximate (good to ~50-100 m). Points are snapped to the
road graph before use, so small errors only shift which grid node they land on.
Replace with surveyed coordinates / Penn's real stop list when available.
"""
import numpy as np

# south, west, north, east  — roughly Schuylkill River to ~50th St, Pine/Baltimore to Powelton
BBOX = (39.940, -75.226, 39.963, -75.176)

EARTH_R = 6371000.0
M_PER_MILE = 1609.344


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R * np.arcsin(np.sqrt(a))


# University City street grid as a tilted lattice, calibrated against OSRM street
# names at 33rd, 40th and 46th St. Blocks east of 38th are wider than west of it.
_CROSS_OFFSET = {  # degrees of latitude south of Market St
    "Market": 0.0, "Chestnut": 0.0017, "Sansom": 0.0025, "Walnut": 0.0032,
    "Locust": 0.0043, "Spruce": 0.0057, "Pine": 0.0068,
}
_STREET_LON = {30: -75.1825, 33: -75.1900, 34: -75.1922, 36: -75.1957, 38: -75.1988, 40: -75.2020}


def ucity(street_no: float, cross: str):
    """Approximate lat/lon of '<street_no>th St & <cross> St'."""
    if street_no <= 40:
        lon = float(np.interp(street_no, list(_STREET_LON), list(_STREET_LON.values())))
    else:
        lon = -75.2020 - (street_no - 40) * 0.00195
    lat_market = 39.9550 + (-75.182 - lon) * 0.114
    return (round(lat_market - _CROSS_OFFSET[cross], 5), round(lon, 5))


# name, (lat, lon), kind, demand weight, spread (m)
# kind: campus = academic/late-night study, housing = dorms + off-campus, hub = transit/hospital
LANDMARKS = [
    ("Van Pelt Library",        (39.9527, -75.1932), "campus", 3.0, 80),
    ("Huntsman Hall",           (39.9530, -75.1982), "campus", 2.0, 80),
    ("Engineering (Towne/Levine)", (39.9521, -75.1907), "campus", 2.0, 80),
    ("Houston Hall",            (39.9508, -75.1940), "campus", 1.2, 80),
    ("Penn Bookstore / 36th & Walnut", ucity(36, "Walnut"), "campus", 1.0, 80),
    ("Pennovation Center",      (39.9415, -75.2005), "campus", 0.4, 100),
    ("Quad",                    (39.9502, -75.1975), "housing", 2.0, 80),
    ("High Rises / Harnwell",   (39.9525, -75.2010), "housing", 2.0, 80),
    ("Gutmann / NCH West",      ucity(39, "Chestnut"), "housing", 1.5, 100),
    ("Hill College House",      (39.9531, -75.1903), "housing", 1.0, 60),
    ("40th & Pine",             ucity(40, "Pine"), "housing", 2.0, 200),
    ("42nd & Spruce",           ucity(42, "Spruce"), "housing", 2.0, 220),
    ("Clark Park / 43rd & Baltimore", (39.9488, -75.2100), "housing", 1.5, 250),
    ("45th & Locust",           ucity(45, "Locust"), "housing", 1.5, 250),
    ("47th & Pine",             ucity(47, "Pine"), "housing", 1.0, 300),
    ("41st & Chestnut",         ucity(41, "Chestnut"), "housing", 1.2, 200),
    ("Powelton Village",        (39.9605, -75.1930), "housing", 0.8, 250),
    ("30th St Station",         (39.9557, -75.1820), "hub", 1.2, 60),
    ("HUP / Perelman",          (39.9490, -75.1935), "hub", 1.5, 100),
    ("Drexel / 33rd & Market",  (39.9563, -75.1895), "hub", 0.6, 100),
]

# Streets treated as well-lit, Public-Safety-approved corridors for walk-to pickups.
# Placeholder until Public Safety supplies the real list (see data/approved_points.csv).
APPROVED_CORRIDORS = [
    "Walnut Street", "Chestnut Street", "Spruce Street", "Market Street",
    "Baltimore Avenue", "33rd Street", "34th Street", "36th Street",
    "38th Street", "40th Street",
]

# Penn Transportation depot / charging location (approximate)
DEPOT = (39.9455, -75.1925)

# Fixed-route evening loops (synthetic, modeled on Penn Bus East/West style loops).
# Walnut is westbound and Spruce/Chestnut eastbound in University City, so loops go
# out on one and back on the other.
BUS_ROUTES = {
    "West Loop": [ucity(30, "Market"), ucity(33, "Walnut"), ucity(36, "Walnut"),
                  ucity(38, "Walnut"), ucity(40, "Walnut"), ucity(43, "Walnut"),
                  ucity(46, "Walnut"), ucity(46, "Spruce"), ucity(43, "Spruce"),
                  ucity(40, "Spruce"), ucity(38, "Spruce"), ucity(34, "Spruce")],
    "Market Loop": [ucity(30, "Market"), ucity(34, "Market"), ucity(38, "Market"),
                    ucity(40, "Market"), ucity(46, "Market"), ucity(46, "Chestnut"),
                    ucity(40, "Chestnut"), ucity(36, "Chestnut"), ucity(33, "Chestnut")],
}

# Labeled buildings for the operator console (approximate coordinates).
BUILDINGS = [
    ("Van Pelt Library", 39.9527, -75.1932),
    ("Huntsman Hall", 39.9530, -75.1982),
    ("Towne / Levine (Engineering)", 39.9521, -75.1907),
    ("Houston Hall", 39.9508, -75.1940),
    ("Irvine Auditorium", 39.9512, -75.1925),
    ("Penn Museum", 39.9493, -75.1913),
    ("HUP / Perelman", 39.9490, -75.1935),
    ("The Quad", 39.9502, -75.1975),
    ("Harnwell College House", 39.9525, -75.2010),
    ("Gutmann College House", 39.9562, -75.1996),
    ("Hill College House", 39.9531, -75.1903),
    ("30th St Station", 39.9557, -75.1820),
    ("Pennovation Center", 39.9415, -75.2005),
    ("Clark Park", 39.9488, -75.2100),
]

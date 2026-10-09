"""Service area, landmarks and small geo helpers for University City.

Coordinates are approximate (good to ~50-100 m). Points are snapped to the
road graph before use, so small errors only shift which grid node they land on.
Replace with surveyed coordinates / Penn's real stop list when available.
"""
import numpy as np

# Evening van service boundary, [lat, lon] corners, traced from the PennTransit app's
# service map (2026-10-07) and Penn's Evening Safety Shuttle map. Covers University City,
# Powelton, 30th Street Station, West Philadelphia to about 52nd St, part of Kingsessing,
# the Grays Ferry depot and Center City between the Schuylkill and 20th St, Market to South St.
SERVICE_AREA = [
    (39.96005, -75.22299), (39.95838, -75.20966), (39.95904, -75.20671), (39.96124, -75.20668),
    (39.96338, -75.18743), (39.96041, -75.18703), (39.95566, -75.18817), (39.95540, -75.18733),
    (39.95812, -75.18429), (39.95737, -75.18085), (39.95468, -75.18115), (39.95356, -75.17292),
    (39.94474, -75.17484), (39.94641, -75.18706), (39.94623, -75.18862), (39.94435, -75.19507),
    (39.94366, -75.19826), (39.94138, -75.19864), (39.94132, -75.20303), (39.94361, -75.20243),
    (39.94429, -75.20035), (39.94578, -75.19892), (39.94757, -75.19904), (39.95015, -75.20112),
    (39.94835, -75.20512), (39.94658, -75.20701), (39.94404, -75.20982), (39.94187, -75.21127),
    (39.93912, -75.21539), (39.94787, -75.22555)]

# south, west, north, east: the service area plus ~150 m
BBOX = (39.9375, -75.2295, 39.9650, -75.1710)

EARTH_R = 6371000.0
M_PER_MILE = 1609.344


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R * np.arcsin(np.sqrt(a))


def in_service_area(lat, lon):
    """True where (lat, lon) lies inside SERVICE_AREA (ray casting; works on arrays)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    inside = np.zeros(np.broadcast(lat, lon).shape, bool)
    P = SERVICE_AREA
    for (la1, lo1), (la2, lo2) in zip(P, P[1:] + P[:1]):
        if la1 == la2:
            continue
        crosses = (la1 > lat) != (la2 > lat)
        inside ^= crosses & (lon < lo1 + (lat - la1) * (lo2 - lo1) / (la2 - la1))
    return inside


def service_area_gap_m(lat, lon):
    """Distance (m) from points to the SERVICE_AREA boundary; 0 for points inside."""
    lat, lon = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
    kx, ky = 111320.0 * np.cos(np.radians(39.95)), 111320.0
    X = np.c_[lon * kx, lat * ky]
    best = np.full(len(X), np.inf)
    P = SERVICE_AREA
    for (la1, lo1), (la2, lo2) in zip(P, P[1:] + P[:1]):
        a, b = np.array([lo1 * kx, la1 * ky]), np.array([lo2 * kx, la2 * ky])
        t = np.clip(((X - a) @ (b - a)) / max((b - a) @ (b - a), 1e-9), 0, 1)
        best = np.minimum(best, np.hypot(*(a + t[:, None] * (b - a) - X).T))
    return np.where(in_service_area(lat, lon), 0.0, best)


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
    ("Fitler Sq / Rittenhouse west", (39.94862, -75.17784), "housing", 1.5, 250),  # assumed weight
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

# Penn Bus evening routes (Mon-Fri) from Penn Transportation's published timetable,
# transportation.upenn.edu/schedules-and-stops (retrieved 2026-10-07). A trip leaves each
# route's first stop every 20 min from 4 PM; the last trip starts at 11 PM. Minutes are
# after the trip leaves its first stop, from the timetable's first column (later columns
# on the page have typos). The Pennovation Works Day Loop ends at 6 PM, before the
# evening service modeled here, so it is left out.
# Stop positions are from OpenStreetMap: the named building, or the street intersection.
BUS_FIRST_S, BUS_LAST_S, BUS_HEADWAY_S = 16 * 3600, 23 * 3600, 20 * 60
_FRANKLINS = (39.95310, -75.19258)   # Franklin's Table, 3405 Walnut St
_BOOKSTORE = (39.95338, -75.19507)   # Penn Bookstore, 3601 Walnut St
_POTTRUCK = (39.95384, -75.19712)    # Pottruck, 3701 Walnut St
_SCHATTNER = (39.95356, -75.20273)   # Dental School, 40th St between Walnut and Locust
_ROSENTHAL = (39.95116, -75.20001)   # Vet School, Spruce St between 38th and 39th
_QUAD = (39.95108, -75.19755)        # Spruce St at 37th
_GATES = (39.95046, -75.19258)       # HUP, 34th & Spruce
_DRL = (39.95208, -75.18920)         # David Rittenhouse Laboratory, 33rd & Walnut

# route -> [(stop, (lat, lon), minutes after the first stop)]
BUS_ROUTES = {
    "Rittenhouse Square Loop": [
        ("Franklin's Table", _FRANKLINS, 0), ("Penn Bookstore", _BOOKSTORE, 1),
        ("Pottruck Gym", _POTTRUCK, 2), ("Schattner Building", _SCHATTNER, 4),
        ("Rosenthal Building", _ROSENTHAL, 5), ("The Quad", _QUAD, 6),
        ("Gates Pavilion", _GATES, 7), ("22nd & South", (39.94528, -75.17857), 13),
        ("20th & Locust", (39.94940, -75.17406), 20), ("30th & Walnut", (39.95183, -75.18407), 25)],
    "Baltimore/Spruce Loop": [
        ("Rosenthal Building", _ROSENTHAL, 0), ("The Quad", _QUAD, 1),
        ("Gates Pavilion", _GATES, 3), ("DRL", _DRL, 5), ("Franklin's Table", _FRANKLINS, 7),
        ("Penn Bookstore", _BOOKSTORE, 8), ("Pottruck Gym", _POTTRUCK, 9),
        ("Schattner Building", _SCHATTNER, 11), ("48th & Springfield", (39.94607, -75.21647), 20)],
    "Powelton/Spruce Loop": [
        ("The Quad", _QUAD, 0), ("Gates Pavilion", _GATES, 1), ("DRL", _DRL, 3),
        ("Franklin's Table", _FRANKLINS, 5), ("Penn Bookstore", _BOOKSTORE, 6),
        ("Pottruck Gym", _POTTRUCK, 7), ("40th & Walnut", (39.95413, -75.20261), 11),
        ("48th & Walnut", (39.95600, -75.21764), 20), ("38th & Powelton", (39.95957, -75.19712), 30)],
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

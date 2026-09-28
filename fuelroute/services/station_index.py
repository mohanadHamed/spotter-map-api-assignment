"""
In-memory spatial index of fuel stations.

All stations (~6.7k rows) are loaded from the database once per process into
numpy arrays. For each route a KD-tree is built over the (densified) route
line and every station within the corridor is matched to its nearest point on
the route, giving its "mile marker" and how far off the route it is. This is
a few milliseconds even for coast-to-coast routes.
"""

import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from ..exceptions import StationDataMissing
from .geo import densify, to_cartesian

ROUTE_SAMPLE_STEP_MILES = 0.5
MILES_PER_DEGREE_LAT = 69.0


@dataclass(frozen=True)
class CorridorStation:
    station_id: int
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: float
    latitude: float
    longitude: float
    mile_marker: float
    off_route_miles: float


class StationIndex:
    def __init__(self, records):
        """``records``: iterable of (id, opis_id, name, address, city, state, price, lat, lon)."""
        records = list(records)
        self.size = len(records)
        self.meta = [record[:6] for record in records]
        self.price = np.array([float(record[6]) for record in records], dtype=float)
        self.lat = np.array([record[7] for record in records], dtype=float)
        self.lon = np.array([record[8] for record in records], dtype=float)
        self.xyz = to_cartesian(self.lat, self.lon) if records else np.empty((0, 3))
        self.average_price = float(self.price.mean()) if records else 0.0

    @classmethod
    def from_database(cls):
        from ..models import FuelStation

        rows = FuelStation.objects.values_list(
            "id", "opis_id", "name", "address", "city", "state", "price", "latitude", "longitude"
        )
        return cls(rows)

    def stations_along_route(self, coords, route_miles, corridor_miles):
        """Stations within ``corridor_miles`` of the route, sorted by mile marker.

        When several stations share the same coordinates (stations are geocoded
        to their town), only the cheapest one is kept: it dominates the others.
        Returns ``(stations, total_matches_before_dedup)``.
        """
        if self.size == 0 or len(coords) < 2:
            return [], 0

        dense, dense_cum = densify(coords, ROUTE_SAMPLE_STEP_MILES)
        # Scale geometric mileage to the provider's reported road distance so markers add up.
        if dense_cum[-1] > 0:
            dense_cum = dense_cum * (route_miles / dense_cum[-1])

        # Cheap bounding-box prefilter before the KD-tree query.
        lat_pad = corridor_miles / MILES_PER_DEGREE_LAT
        max_abs_lat = min(89.0, float(np.abs(dense[:, 0]).max()) + lat_pad)
        lon_pad = corridor_miles / (MILES_PER_DEGREE_LAT * np.cos(np.radians(max_abs_lat)))
        candidates = np.flatnonzero(
            (self.lat >= dense[:, 0].min() - lat_pad)
            & (self.lat <= dense[:, 0].max() + lat_pad)
            & (self.lon >= dense[:, 1].min() - lon_pad)
            & (self.lon <= dense[:, 1].max() + lon_pad)
        )
        if candidates.size == 0:
            return [], 0

        tree = cKDTree(to_cartesian(dense[:, 0], dense[:, 1]))
        distances, nearest = tree.query(self.xyz[candidates], k=1, distance_upper_bound=corridor_miles)
        hit = np.isfinite(distances)
        hits, distances, nearest = candidates[hit], distances[hit], nearest[hit]
        total_matches = int(hits.size)

        # Keep the cheapest station per location, then order along the route.
        order = np.lexsort((self.price[hits], self.lon[hits], self.lat[hits]))
        seen, stations = set(), []
        for position in order:
            station = hits[position]
            location = (self.lat[station], self.lon[station])
            if location in seen:
                continue
            seen.add(location)
            station_id, opis_id, name, address, city, state = self.meta[station]
            stations.append(
                CorridorStation(
                    station_id=station_id,
                    opis_id=opis_id,
                    name=name,
                    address=address,
                    city=city,
                    state=state,
                    price=float(self.price[station]),
                    latitude=float(self.lat[station]),
                    longitude=float(self.lon[station]),
                    mile_marker=float(dense_cum[nearest[position]]),
                    off_route_miles=float(distances[position]),
                )
            )
        stations.sort(key=lambda s: (s.mile_marker, s.price))
        return stations, total_matches


_index = None
_lock = threading.Lock()


def get_station_index():
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                index = StationIndex.from_database()
                if index.size == 0:
                    raise StationDataMissing(
                        "No fuel stations loaded. Run `python manage.py load_fuel_stations` first."
                    )
                _index = index
    return _index


def reset_station_index():
    global _index
    with _lock:
        _index = None

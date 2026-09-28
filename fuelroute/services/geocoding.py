"""
Resolve free-text start/finish locations to coordinates.

Most inputs are handled offline, with no external call:
  * "41.8781,-87.6298"           -> coordinates as given
  * "60601" / "Chicago, IL 60601" -> ZIP code centroid (Census ZCTA)
  * "Chicago, IL" / "Chicago, Illinois" / "Chicago IL" -> Census Gazetteer place
Anything else (street addresses, landmarks) falls back to Nominatim, cached.
"""

import csv
import hashlib
import json
import re
import threading
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache
from scipy.spatial import cKDTree

from ..exceptions import ExternalServiceError, ExternalServiceTimeout, LocationNotFound, OutsideUSA
from .geo import to_cartesian
from .places import city_lookup_keys, resolve_state_code

# The 1:20M outline generalises coastlines; allow points this close to it (piers, small islands).
BOUNDARY_TOLERANCE_MILES = 3.0

_COORDINATES = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\s*$")
_COUNTRY_SUFFIX = re.compile(r",?\s*(USA|U\.S\.A\.|US|U\.S\.|United States(?: of America)?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Location:
    query: str
    latitude: float
    longitude: float
    label: str
    source: str

    def as_dict(self):
        return {
            "query": self.query,
            "label": self.label,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "source": self.source,
        }


class Gazetteer:
    """Offline lookup tables built by ``manage.py geocode_fuel_stations``."""

    def __init__(self, places_csv, zipcodes_csv, boundary_json):
        self.places = {}
        names, coords = [], []
        with open(places_csv, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                lat, lon = float(row["latitude"]), float(row["longitude"])
                self.places.setdefault((city_lookup_keys(row["name"])[0], row["state"]), (row["name"], lat, lon))
                names.append(f"{row['name']}, {row['state']}")
                coords.append((lat, lon))
        self._place_names = names
        coords = np.array(coords, dtype=float)
        self._place_tree = cKDTree(to_cartesian(coords[:, 0], coords[:, 1]))

        self.zipcodes = {}
        with open(zipcodes_csv, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                self.zipcodes[row["zip"]] = (float(row["latitude"]), float(row["longitude"]))

        with open(boundary_json, encoding="utf-8") as handle:
            rings = [np.array(ring, dtype=float) for ring in json.load(handle)["rings"]]
        self._rings = [(ring, ring.min(axis=0), ring.max(axis=0)) for ring in rings]
        vertices = np.vstack(rings)
        self._boundary_tree = cKDTree(to_cartesian(vertices[:, 1], vertices[:, 0]))

    def find_place(self, city, state):
        for key in city_lookup_keys(city):
            match = self.places.get((key, state))
            if match:
                return match
        return None

    def nearest_place(self, lat, lon):
        """Label of the closest Census place, e.g. "Joliet, IL"."""
        _, index = self._place_tree.query(to_cartesian([lat], [lon])[0])
        return self._place_names[index]

    def in_usa(self, lat, lon):
        for ring, lower, upper in self._rings:
            if lower[0] <= lon <= upper[0] and lower[1] <= lat <= upper[1] and _point_in_ring(lon, lat, ring):
                return True
        distance, _ = self._boundary_tree.query(to_cartesian([lat], [lon])[0])
        return distance <= BOUNDARY_TOLERANCE_MILES


def _point_in_ring(x, y, ring):
    """Even-odd ray casting against a closed ring of [lon, lat] vertices."""
    xi, yi = ring[:, 0], ring[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)
    crosses = (yi > y) != (yj > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at_y = (xj - xi) * (y - yi) / (yj - yi) + xi
    return bool(np.count_nonzero(crosses & (x < x_at_y)) % 2)


_gazetteer = None
_gazetteer_lock = threading.Lock()


def get_gazetteer():
    global _gazetteer
    if _gazetteer is None:
        with _gazetteer_lock:
            if _gazetteer is None:
                _gazetteer = Gazetteer(settings.US_PLACES_CSV, settings.US_ZIPCODES_CSV, settings.US_BOUNDARY_JSON)
    return _gazetteer


def geocode(query):
    """Resolve ``query`` to a :class:`Location` in the USA. Returns ``(location, external_calls)``."""
    text = " ".join((query or "").split())
    if not text:
        raise LocationNotFound("Location must not be empty.")
    location, calls = _resolve(text)
    if not get_gazetteer().in_usa(location.latitude, location.longitude):
        raise OutsideUSA(
            f"'{location.query}' ({location.latitude:.4f}, {location.longitude:.4f}) is not within the USA."
        )
    return location, calls


def _resolve(text):
    gazetteer = get_gazetteer()

    match = _COORDINATES.match(text)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise LocationNotFound(f"'{text}' is not a valid 'latitude,longitude' pair.")
        return Location(text, lat, lon, f"{lat:.5f}, {lon:.5f} (near {gazetteer.nearest_place(lat, lon)})", "coordinates"), 0

    cleaned = _COUNTRY_SUFFIX.sub("", text).strip(" ,")

    zip_match = _ZIP.search(cleaned)
    if zip_match and zip_match.group(1) in gazetteer.zipcodes:
        lat, lon = gazetteer.zipcodes[zip_match.group(1)]
        return Location(text, lat, lon, f"{zip_match.group(1)} ({gazetteer.nearest_place(lat, lon)})", "zipcode"), 0

    city, state = _split_city_state(cleaned)
    if city and state:
        found = gazetteer.find_place(city, state)
        if found:
            name, lat, lon = found
            return Location(text, lat, lon, f"{name}, {state}", "gazetteer"), 0

    return _nominatim(text), 1


def _split_city_state(text):
    """Split "Chicago, IL", "Chicago, Illinois" or "Chicago IL" into (city, state_code)."""
    if "," in text:
        city, _, state = text.rpartition(",")
        state = re.sub(r"\s*\d{5}(-\d{4})?$", "", state).strip()
        code = resolve_state_code(state)
        if code:
            # Drop any street part: "123 Main St, Chicago, IL" -> "Chicago".
            return city.split(",")[-1].strip(), code
        return None, None
    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and len(parts[1]) == 2 and resolve_state_code(parts[1]):
        return parts[0].strip(), resolve_state_code(parts[1])
    return None, None


def _nominatim(text):
    cache_key = "geocode:" + hashlib.sha1(text.lower().encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        if cached == "":
            raise LocationNotFound(f"Could not find a US location matching '{text}'.")
        return cached

    try:
        response = requests.get(
            f"{settings.NOMINATIM_URL}/search",
            params={"q": text, "countrycodes": "us", "format": "jsonv2", "limit": 1, "addressdetails": 1},
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=(settings.HTTP_CONNECT_TIMEOUT, settings.HTTP_READ_TIMEOUT),
        )
        response.raise_for_status()
        results = response.json()
    except requests.Timeout as exc:
        raise ExternalServiceTimeout("Geocoding service timed out.") from exc
    except (requests.RequestException, ValueError) as exc:
        raise ExternalServiceError(f"Geocoding service error: {exc}") from exc

    if not results:
        cache.set(cache_key, "", 60 * 60)
        raise LocationNotFound(f"Could not find a US location matching '{text}'.")
    best = results[0]
    location = Location(text, float(best["lat"]), float(best["lon"]), _nominatim_label(best, text), "nominatim")
    cache.set(cache_key, location, 60 * 60 * 24 * 7)
    return location


def _nominatim_label(result, fallback):
    """Short "Name, City, ST" label from a Nominatim result."""
    address = result.get("address") or {}
    state = (address.get("ISO3166-2-lvl4") or "").removeprefix("US-") or address.get("state", "")
    city = next((address[key] for key in ("city", "town", "village", "hamlet", "county") if address.get(key)), "")
    parts = [result.get("name") or "", city, state]
    label = ", ".join(dict.fromkeys(part for part in parts if part))
    return label or result.get("display_name") or fallback

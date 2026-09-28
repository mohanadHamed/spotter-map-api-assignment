"""
One-time data preparation.

The OPIS price list only has city/state for each truck stop, so every stop is
geocoded to its city using the public-domain US Census Gazetteer files
(places + county subdivisions). The few communities missing from the Gazetteer
are resolved with Nominatim (OpenStreetMap), throttled to 1 request/second and
cached on disk.

Outputs (committed to the repo so the API never geocodes stations at runtime):
  data/fuel_stations_geocoded.csv  - price list rows (US only) + latitude/longitude
  data/us_places.csv               - compact offline gazetteer for request geocoding
  data/us_zipcodes.csv             - ZIP code centroids for request geocoding
  data/us_boundary.json            - US outline (Census 1:20M) for the "within the USA" check
"""

import csv
import json
import re
import time
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fuelroute.services.places import (
    US_STATES,
    census_name_aliases,
    city_lookup_keys,
    normalize_place_name,
    strip_census_suffix,
)

GAZETTEER_BASE = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer"
PLACES_FILE = "2023_Gaz_place_national.zip"
COUSUBS_FILE = "2023_Gaz_cousubs_national.zip"
ZCTA_FILE = "2023_Gaz_zcta_national.zip"
BOUNDARY_URL = "https://www2.census.gov/geo/tiger/GENZ2023/kml/cb_2023_us_nation_20m.zip"


def _is_statistical_division(name):
    """Census county divisions and unorganized territories are too large to stand in for a town."""
    return name.endswith((" CCD", " UT", " unorganized territory"))


class Command(BaseCommand):
    help = "Geocode the fuel price CSV to city level and build the offline US gazetteer files."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(settings.FUEL_PRICES_CSV))
        parser.add_argument("--output", default=str(settings.GEOCODED_STATIONS_CSV))
        parser.add_argument("--places-output", default=str(settings.US_PLACES_CSV))
        parser.add_argument("--zip-output", default=str(settings.US_ZIPCODES_CSV))
        parser.add_argument("--boundary-output", default=str(settings.US_BOUNDARY_JSON))
        parser.add_argument("--cache-dir", default=str(settings.DATA_DIR / "cache"))
        parser.add_argument(
            "--no-nominatim",
            action="store_true",
            help="Skip the Nominatim fallback; unmatched stations are dropped.",
        )

    def handle(self, *args, **options):
        cache_dir = Path(options["cache_dir"])
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.HTTP_USER_AGENT

        gazetteer = self.build_gazetteer(cache_dir)
        self.stdout.write(f"Gazetteer entries: {len(gazetteer)}")

        rows, skipped = self.read_price_list(Path(options["source"]))
        self.stdout.write(f"US price rows: {len(rows)} (skipped {skipped} non-US/invalid rows)")

        for row in rows:
            row["_key"] = next((key for key in row["_keys"] if (key, row["state"]) in gazetteer), row["_keys"][0])
        misses = sorted({(row["_key"], row["state"]) for row in rows if (row["_key"], row["state"]) not in gazetteer})
        self.stdout.write(f"City/state pairs not in the Gazetteer: {len(misses)}")
        if misses and not options["no_nominatim"]:
            self.resolve_with_nominatim(rows, misses, gazetteer, cache_dir / "nominatim.json")

        written, unresolved = self.write_stations(rows, gazetteer, Path(options["output"]))
        self.write_places(gazetteer, Path(options["places_output"]))
        self.write_zipcodes(cache_dir, Path(options["zip_output"]))
        self.write_boundary(cache_dir, Path(options["boundary_output"]))

        self.stdout.write(self.style.SUCCESS(f"Wrote {written} geocoded station rows to {options['output']}"))
        if unresolved:
            self.stdout.write(self.style.WARNING(f"Unresolved city/state pairs ({len(unresolved)}): {unresolved}"))

    # ------------------------------------------------------------------ input

    def download(self, filename, cache_dir, url=None):
        path = cache_dir / filename
        if not path.exists():
            self.stdout.write(f"Downloading {filename} ...")
            response = self.session.get(url or f"{GAZETTEER_BASE}/{filename}", timeout=120)
            response.raise_for_status()
            path.write_bytes(response.content)
        return path

    def read_gazetteer_file(self, path):
        with zipfile.ZipFile(path) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
        lines = text.splitlines()
        header = [column.strip() for column in lines[0].split("\t")]
        for line in lines[1:]:
            yield dict(zip(header, (value.strip() for value in line.split("\t"))))

    def build_gazetteer(self, cache_dir):
        """(normalized name, state) -> (display name, lat, lon, source).

        Primary names always win over aliases, and places win over county subdivisions.
        """
        sources = []
        for filename, source in ((PLACES_FILE, "census_place"), (COUSUBS_FILE, "census_cousub")):
            records = [
                record
                for record in self.read_gazetteer_file(self.download(filename, cache_dir))
                if record["USPS"] in US_STATES and not _is_statistical_division(record["NAME"])
            ]
            # Larger land area first, so the main "Springfield" wins over a tiny namesake.
            records.sort(key=lambda record: -float(record.get("ALAND") or 0))
            sources.append((records, source))

        gazetteer = {}
        for use_aliases in (False, True):
            for records, source in sources:
                for record in records:
                    if use_aliases:
                        names = census_name_aliases(record["NAME"])
                    else:
                        names = [strip_census_suffix(record["NAME"])]
                    lat, lon = float(record["INTPTLAT"]), float(record["INTPTLONG"])
                    for name in names:
                        gazetteer.setdefault((normalize_place_name(name), record["USPS"]), (name, lat, lon, source))
        return gazetteer

    def read_price_list(self, path):
        if not path.exists():
            raise CommandError(f"Price list not found: {path}")
        rows, skipped = [], 0
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for record in csv.DictReader(handle):
                state = record["State"].strip().upper()
                try:
                    price = Decimal(record["Retail Price"].strip())
                    opis_id = int(record["OPIS Truckstop ID"])
                except (InvalidOperation, ValueError):
                    skipped += 1
                    continue
                if state not in US_STATES or price <= 0:
                    skipped += 1
                    continue
                city = " ".join(record["City"].split()).title()
                rows.append(
                    {
                        "opis_id": opis_id,
                        "name": " ".join(record["Truckstop Name"].split()),
                        "address": " ".join(record["Address"].split()),
                        "city": city,
                        "state": state,
                        "rack_id": record["Rack ID"].strip(),
                        "price": price,
                        "_keys": city_lookup_keys(city),
                    }
                )
        return rows, skipped

    # -------------------------------------------------------------- fallback

    def resolve_with_nominatim(self, rows, misses, gazetteer, cache_path):
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        display_names = {(row["_key"], row["state"]): row["city"] for row in rows}
        for index, (key, state) in enumerate(misses, start=1):
            city = display_names[(key, state)]
            cache_key = f"{city}|{state}"
            if cache_key not in cache:
                try:
                    cache[cache_key] = self.nominatim_lookup(city, state)
                    cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True))
                except requests.RequestException as exc:
                    self.stderr.write(f"  Nominatim failed for {city}, {state}: {exc}")
                time.sleep(1.0)  # Nominatim usage policy: max 1 request per second.
            result = cache.get(cache_key)
            if result:
                gazetteer[(key, state)] = (city, result[0], result[1], "nominatim")
            if index % 25 == 0:
                self.stdout.write(f"  Nominatim: {index}/{len(misses)}")

    def nominatim_lookup(self, city, state):
        """Return [lat, lon], or None when Nominatim has no match. Raises after repeated errors."""
        queries = [
            {"city": city, "state": US_STATES[state]},
            # Some hamlets are not tagged as cities; retry as free text.
            {"q": f"{city}, {US_STATES[state]}"},
        ]
        for query in queries:
            results = self.nominatim_get({**query, "countrycodes": "us", "format": "jsonv2", "limit": 1})
            if results:
                return [float(results[0]["lat"]), float(results[0]["lon"])]
            time.sleep(1.0)
        return None

    def nominatim_get(self, params):
        for attempt in range(3):
            try:
                response = self.session.get(f"{settings.NOMINATIM_URL}/search", params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                if attempt == 2:
                    raise
                self.stderr.write(f"  Nominatim error, retrying: {exc}")
                time.sleep(2 ** (attempt + 2))
        return None

    # ---------------------------------------------------------------- output

    def write_stations(self, rows, gazetteer, path):
        fieldnames = [
            "opis_id", "name", "address", "city", "state", "rack_id",
            "price", "latitude", "longitude", "geocode_source",
        ]
        written, unresolved = 0, set()
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                match = gazetteer.get((row["_key"], row["state"]))
                if not match:
                    unresolved.add(f"{row['city']}, {row['state']}")
                    continue
                _, lat, lon, source = match
                writer.writerow(
                    {
                        **{field: row[field] for field in fieldnames[:7]},
                        "latitude": f"{lat:.6f}",
                        "longitude": f"{lon:.6f}",
                        "geocode_source": source,
                    }
                )
                written += 1
        return written, sorted(unresolved)

    def write_places(self, gazetteer, path):
        entries = sorted(
            {(name, state, lat, lon) for (_, state), (name, lat, lon, _) in gazetteer.items()},
            key=lambda entry: (entry[1], entry[0]),
        )
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["name", "state", "latitude", "longitude"])
            for name, state, lat, lon in entries:
                writer.writerow([name, state, f"{lat:.5f}", f"{lon:.5f}"])
        self.stdout.write(f"Wrote {len(entries)} places to {path}")

    def write_zipcodes(self, cache_dir, path):
        records = self.read_gazetteer_file(self.download(ZCTA_FILE, cache_dir))
        count = 0
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["zip", "latitude", "longitude"])
            for record in records:
                writer.writerow([record["GEOID"], f"{float(record['INTPTLAT']):.5f}", f"{float(record['INTPTLONG']):.5f}"])
                count += 1
        self.stdout.write(f"Wrote {count} ZIP codes to {path}")

    def write_boundary(self, cache_dir, path):
        archive_path = self.download(BOUNDARY_URL.rsplit("/", 1)[-1], cache_dir, url=BOUNDARY_URL)
        with zipfile.ZipFile(archive_path) as archive:
            kml = archive.read(next(n for n in archive.namelist() if n.endswith(".kml"))).decode("utf-8")
        rings = []
        for block in re.findall(r"<outerBoundaryIs>.*?<coordinates>(.*?)</coordinates>", kml, re.S):
            ring = []
            for point in block.split():
                lon, lat = point.split(",")[:2]
                ring.append([round(float(lon), 4), round(float(lat), 4)])
            rings.append(ring)
        path.write_text(json.dumps({"source": "US Census Bureau cb_2023_us_nation_20m", "rings": rings}))
        self.stdout.write(f"Wrote {len(rings)} boundary rings to {path}")

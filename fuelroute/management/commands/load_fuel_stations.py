"""Load the geocoded fuel station CSV into the database."""

import csv
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuelroute.models import FuelStation
from fuelroute.services.station_index import reset_station_index


class Command(BaseCommand):
    help = "Load data/fuel_stations_geocoded.csv into the FuelStation table (replaces existing rows)."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(settings.GEOCODED_STATIONS_CSV))

    def handle(self, *args, **options):
        path = Path(options["source"])
        if not path.exists():
            raise CommandError(f"{path} not found. Run `python manage.py geocode_fuel_stations` first.")

        # The price list repeats some stations (one row per rack/supplier price).
        # Keep one row per OPIS ID with the lowest retail price.
        stations = {}
        rows = 0
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                rows += 1
                opis_id = int(row["opis_id"])
                price = Decimal(row["price"])
                if opis_id in stations and stations[opis_id].price <= price:
                    continue
                stations[opis_id] = FuelStation(
                    opis_id=opis_id,
                    name=row["name"],
                    address=row["address"],
                    city=row["city"],
                    state=row["state"],
                    rack_id=int(row["rack_id"]) if row["rack_id"] else None,
                    price=price,
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    geocode_source=row["geocode_source"],
                )

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations.values(), batch_size=1000)
        reset_station_index()

        self.stdout.write(self.style.SUCCESS(f"Loaded {len(stations)} stations from {rows} price rows."))

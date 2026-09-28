from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from fuelroute.exceptions import LocationNotFound, OutsideUSA
from fuelroute.services.geocoding import geocode
from fuelroute.services.places import normalize_place_name, strip_census_suffix


def fake_nominatim(results):
    response = mock.Mock(status_code=200)
    response.json.return_value = results
    response.raise_for_status.return_value = None
    return response


class OfflineGeocodingTests(SimpleTestCase):
    def test_coordinates(self):
        location, calls = geocode("41.8781, -87.6298")
        self.assertEqual(calls, 0)
        self.assertEqual((location.latitude, location.longitude), (41.8781, -87.6298))
        self.assertEqual(location.source, "coordinates")
        self.assertIn("IL", location.label)

    def test_city_state_variants(self):
        for query in ("Chicago, IL", "chicago, illinois", "Chicago IL", "Chicago, IL, USA"):
            with self.subTest(query=query):
                location, calls = geocode(query)
                self.assertEqual(calls, 0)
                self.assertEqual(location.label, "Chicago, IL")
                self.assertAlmostEqual(location.latitude, 41.84, delta=0.1)

    def test_abbreviations_are_normalised(self):
        location, _ = geocode("Saint Louis, MO")
        self.assertEqual(location.label, "St. Louis, MO")
        location, _ = geocode("Kansas City, MO")
        self.assertEqual(location.label, "Kansas City, MO")

    def test_zip_code(self):
        location, calls = geocode("90210")
        self.assertEqual(calls, 0)
        self.assertEqual(location.source, "zipcode")
        self.assertAlmostEqual(location.latitude, 34.1, delta=0.1)

    def test_outside_usa_is_rejected(self):
        for query in ("43.6532,-79.3832", "19.4326,-99.1332", "49.2827,-123.1207"):
            with self.subTest(query=query), self.assertRaises(OutsideUSA):
                geocode(query)

    def test_alaska_and_hawaii_are_inside(self):
        for query in ("61.2181,-149.9003", "21.3069,-157.8583"):
            with self.subTest(query=query):
                geocode(query)

    def test_invalid_coordinates(self):
        with self.assertRaises(LocationNotFound):
            geocode("123.0,-500.0")

    def test_empty(self):
        with self.assertRaises(LocationNotFound):
            geocode("   ")


class NominatimFallbackTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("fuelroute.services.geocoding.requests.get")
    def test_address_falls_back_to_nominatim_once(self, get):
        get.return_value = fake_nominatim(
            [
                {
                    "lat": "40.748",
                    "lon": "-73.985",
                    "name": "Empire State Building",
                    "display_name": "Empire State Building, 350, 5th Avenue, Manhattan, New York, United States",
                    "address": {"city": "New York", "ISO3166-2-lvl4": "US-NY"},
                }
            ]
        )
        location, calls = geocode("Empire State Building")
        self.assertEqual(calls, 1)
        self.assertEqual(location.label, "Empire State Building, New York, NY")
        self.assertEqual(location.source, "nominatim")

        # Second lookup is served from the cache.
        _, calls = geocode("Empire State Building")
        self.assertEqual(calls, 1)
        self.assertEqual(get.call_count, 1)

    @mock.patch("fuelroute.services.geocoding.requests.get")
    def test_unknown_place(self, get):
        get.return_value = fake_nominatim([])
        with self.assertRaises(LocationNotFound):
            geocode("Nowhere Special Qwxz")


class NormalisationTests(SimpleTestCase):
    def test_normalize_place_name(self):
        self.assertEqual(normalize_place_name("Mc Alpin"), normalize_place_name("McAlpin"))
        self.assertEqual(normalize_place_name("La Salle"), normalize_place_name("LaSalle"))
        self.assertEqual(normalize_place_name("Saint Louis"), normalize_place_name("St. Louis"))
        self.assertEqual(normalize_place_name("O'Neill"), normalize_place_name("ONeill"))
        self.assertEqual(normalize_place_name("S Coffeyville"), normalize_place_name("South Coffeyville"))

    def test_strip_census_suffix(self):
        self.assertEqual(strip_census_suffix("Chicago city"), "Chicago")
        self.assertEqual(strip_census_suffix("Kansas City city"), "Kansas City")
        self.assertEqual(strip_census_suffix("Breezewood CDP"), "Breezewood")
        self.assertEqual(strip_census_suffix("Indianapolis city (balance)"), "Indianapolis")

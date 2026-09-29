from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from fuelroute.models import FuelStation
from fuelroute.services.geo import METERS_PER_MILE, encode_polyline, haversine_miles
from fuelroute.services.station_index import reset_station_index

CHICAGO = (41.8375, -87.6866)
DENVER = (39.7621, -104.8759)


def straight_line(start, end, points=200):
    return np.column_stack((np.linspace(start[0], end[0], points), np.linspace(start[1], end[1], points)))


def osrm_response(coords, distance_miles):
    response = mock.Mock(status_code=200, text="")
    response.json.return_value = {
        "code": "Ok",
        "routes": [
            {
                "distance": distance_miles * METERS_PER_MILE,
                "duration": distance_miles / 60 * 3600,
                "geometry": encode_polyline(coords),
            }
        ],
    }
    return response


@override_settings(ROUTING_PROVIDER="osrm", DEFAULT_STOP_PENALTY_USD=10)
class RoutePlanApiTests(TestCase):
    def setUp(self):
        cache.clear()
        reset_station_index()
        self.addCleanup(reset_station_index)
        self.line = straight_line(CHICAGO, DENVER)
        self.distance = float(haversine_miles(*CHICAGO, *DENVER))
        # Stations along the straight line at 30% / 55% / 80% of the way, plus one far away.
        for opis_id, fraction, price in [(1, 0.30, 3.20), (2, 0.55, 2.95), (3, 0.80, 3.40)]:
            lat, lon = self.line[int(fraction * (len(self.line) - 1))]
            FuelStation.objects.create(
                opis_id=opis_id, name=f"Station {opis_id}", address="I-80", city=f"Town {opis_id}",
                state="NE", price=price, latitude=lat, longitude=lon,
            )
        FuelStation.objects.create(
            opis_id=99, name="Far away", city="Miami", state="FL", price=1.00, latitude=25.76, longitude=-80.19,
        )
        patcher = mock.patch("fuelroute.services.routing._session.request")
        self.routing_request = patcher.start()
        self.addCleanup(patcher.stop)
        self.routing_request.return_value = osrm_response(self.line, self.distance)

    def test_plan_route_get(self):
        response = self.client.get(reverse("route-plan"), {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()

        self.assertEqual(data["start"]["label"], "Chicago, IL")
        self.assertEqual(data["finish"]["label"], "Denver, CO")
        self.assertAlmostEqual(data["route"]["distance_miles"], self.distance, delta=0.1)
        self.assertEqual(data["meta"]["routing_api_calls"], 1)
        self.assertEqual(data["meta"]["geocoding_api_calls"], 0)
        self.assertEqual(self.routing_request.call_count, 1)

        stops = data["fuel_stops"]
        self.assertEqual([s["opis_id"] for s in stops], [2])  # the cheapest station, bought once
        self.assertAlmostEqual(data["summary"]["total_fuel_cost_usd"], sum(s["cost_usd"] for s in stops), places=1)
        self.assertEqual(data["summary"]["number_of_stops"], 1)
        self.assertAlmostEqual(data["summary"]["total_gallons_used"], self.distance / 10, places=1)

        kinds = [f["properties"]["kind"] for f in data["map"]["geojson"]["features"]]
        self.assertEqual(kinds, ["route", "start", "finish", "fuel_stop"])
        self.assertIn("/api/route/map/?", data["map"]["map_url"])

    def test_route_is_cached(self):
        params = {"start": "Chicago, IL", "finish": "Denver, CO"}
        self.client.get(reverse("route-plan"), params)
        data = self.client.get(reverse("route-plan"), params).json()
        self.assertEqual(data["meta"]["routing_api_calls"], 0)
        self.assertTrue(data["meta"]["route_cached"])
        self.assertEqual(self.routing_request.call_count, 1)

    def test_plan_route_post(self):
        response = self.client.post(
            reverse("route-plan"),
            {"start": "Chicago, IL", "finish": "Denver, CO", "corridor_miles": 5, "stop_penalty_usd": 0},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["summary"]["corridor_miles"], 5)

    def test_missing_finish(self):
        response = self.client.get(reverse("route-plan"), {"start": "Chicago, IL"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_request")
        self.assertIn("finish", response.json()["detail"])

    def test_outside_usa(self):
        response = self.client.get(reverse("route-plan"), {"start": "Chicago, IL", "finish": "43.6532,-79.3832"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "outside_usa")
        self.routing_request.assert_not_called()

    def test_no_route(self):
        self.routing_request.return_value.json.return_value = {"code": "NoRoute", "message": "Impossible route"}
        response = self.client.get(reverse("route-plan"), {"start": "Chicago, IL", "finish": "Honolulu, HI"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "no_route")

    def test_routing_service_down(self):
        import requests

        self.routing_request.side_effect = requests.ConnectionError("boom")
        with self.assertLogs("fuelroute.services.routing", level="WARNING"):
            response = self.client.get(reverse("route-plan"), {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "upstream_error")
        self.assertEqual(self.routing_request.call_count, 2)  # one retry

    def test_infeasible_route(self):
        FuelStation.objects.filter(opis_id__in=[1, 2, 3]).delete()
        reset_station_index()
        response = self.client.get(reverse("route-plan"), {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["error"], "infeasible_route")
        self.assertIn("gap_start_mile", body)

    def test_map_page(self):
        response = self.client.get(reverse("route-map"), {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "leaflet")
        self.assertContains(response, "Station 2")
        self.assertContains(response, 'id="route-geojson"')
        # Tiles must be requested with a Referer, or OpenStreetMap blocks them.
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertContains(response, 'referrerPolicy: "strict-origin-when-cross-origin"')

    def test_map_page_error(self):
        response = self.client.get(reverse("route-map"), {"start": "Chicago, IL"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Could not plan this route", status_code=400)


class StationDataMissingTests(TestCase):
    def setUp(self):
        reset_station_index()
        self.addCleanup(reset_station_index)
        patcher = mock.patch("fuelroute.services.routing._session.request")
        self.routing_request = patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_database(self):
        response = self.client.get(reverse("route-plan"), {"start": "Chicago, IL", "finish": "Denver, CO"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "station_data_missing")
        self.routing_request.assert_not_called()

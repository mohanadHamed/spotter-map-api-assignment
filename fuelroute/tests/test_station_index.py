import numpy as np
from django.test import SimpleTestCase

from fuelroute.services.geo import cumulative_miles
from fuelroute.services.station_index import StationIndex


class StationIndexTests(SimpleTestCase):
    def setUp(self):
        # A straight east-west route along the 40th parallel, ~530 miles, with sparse vertices.
        self.route = np.array([[40.0, -100.0], [40.0, -90.0]])
        self.route_miles = float(cumulative_miles(self.route)[-1])
        records = [
            (1, 101, "On route", "", "Mid", "KS", 3.10, 40.0, -95.0),
            (2, 102, "Five miles north", "", "North", "KS", 3.00, 40.0 + 5 / 69.0, -97.0),
            (3, 103, "Thirty miles north", "", "Far", "NE", 2.50, 40.0 + 30 / 69.0, -96.0),
            (4, 104, "Same town, pricier", "", "Mid", "KS", 3.50, 40.0, -95.0),
            (5, 105, "Past the end", "", "East", "IL", 2.00, 40.0, -85.0),
        ]
        self.index = StationIndex(records)

    def test_corridor_filter_and_mile_markers(self):
        stations, matches = self.index.stations_along_route(self.route, self.route_miles, corridor_miles=10)
        self.assertEqual([s.opis_id for s in stations], [102, 101])
        self.assertEqual(matches, 3)  # 101, 102 and 104 are in the corridor; 104 is deduplicated

        north, mid = stations
        self.assertAlmostEqual(north.off_route_miles, 5.0, delta=0.26)
        # Distances are measured to the nearest route sample (every 0.5 mi), so within 0.25 mi.
        self.assertAlmostEqual(mid.off_route_miles, 0.0, delta=0.26)
        # Mile markers along the route, even though the route has only two vertices.
        self.assertAlmostEqual(north.mile_marker, self.route_miles * 0.3, delta=1.0)
        self.assertAlmostEqual(mid.mile_marker, self.route_miles * 0.5, delta=1.0)

    def test_keeps_cheapest_station_per_location(self):
        stations, _ = self.index.stations_along_route(self.route, self.route_miles, corridor_miles=10)
        mid = next(s for s in stations if s.city == "Mid")
        self.assertEqual((mid.opis_id, mid.price), (101, 3.10))

    def test_wider_corridor_includes_more_stations(self):
        stations, _ = self.index.stations_along_route(self.route, self.route_miles, corridor_miles=35)
        self.assertIn(103, [s.opis_id for s in stations])

    def test_mile_markers_scale_to_road_distance(self):
        stations, _ = self.index.stations_along_route(self.route, self.route_miles * 1.2, corridor_miles=10)
        mid = next(s for s in stations if s.opis_id == 101)
        self.assertAlmostEqual(mid.mile_marker, self.route_miles * 1.2 * 0.5, delta=1.0)

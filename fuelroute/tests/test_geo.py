import numpy as np
from django.test import SimpleTestCase

from fuelroute.services.geo import (
    cumulative_miles,
    decode_polyline,
    densify,
    encode_polyline,
    haversine_miles,
    simplify,
)


class HaversineTests(SimpleTestCase):
    def test_new_york_to_los_angeles(self):
        distance = haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
        self.assertAlmostEqual(float(distance), 2445, delta=5)

    def test_zero_distance(self):
        self.assertEqual(float(haversine_miles(35.0, -100.0, 35.0, -100.0)), 0.0)


class PolylineTests(SimpleTestCase):
    def test_decodes_reference_example(self):
        # Example from Google's encoded polyline documentation (precision 5).
        coords = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        np.testing.assert_allclose(coords, [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]])

    def test_round_trip_precision_6(self):
        coords = np.array([[41.878113, -87.629799], [39.739235, -104.99025], [34.052235, -118.243683]])
        np.testing.assert_allclose(decode_polyline(encode_polyline(coords)), coords, atol=1e-6)


class LineTests(SimpleTestCase):
    def setUp(self):
        # Two long segments along a parallel: ~52 and ~105 miles.
        self.coords = np.array([[40.0, -100.0], [40.0, -99.0], [40.0, -97.0]])

    def test_cumulative_miles(self):
        cum = cumulative_miles(self.coords)
        self.assertEqual(cum[0], 0.0)
        self.assertAlmostEqual(cum[-1], float(haversine_miles(40, -100, 40, -99) + haversine_miles(40, -99, 40, -97)))

    def test_densify_limits_step_and_preserves_length(self):
        dense, dense_cum = densify(self.coords, 0.5)
        steps = np.diff(cumulative_miles(dense))
        self.assertLessEqual(steps.max(), 0.5 + 1e-6)
        np.testing.assert_allclose(dense[[0, -1]], self.coords[[0, -1]])
        self.assertAlmostEqual(dense_cum[-1], cumulative_miles(self.coords)[-1], places=6)

    def test_simplify_drops_collinear_points(self):
        line = np.column_stack((np.full(100, 40.0), np.linspace(-100, -90, 100)))
        simplified = simplify(line)
        self.assertEqual(len(simplified), 2)
        np.testing.assert_allclose(simplified, line[[0, -1]])

    def test_simplify_keeps_corners(self):
        line = np.array([[0.0, 0.0], [0.0, 1.0], [0.0, 2.0], [1.0, 2.0], [2.0, 2.0]])
        np.testing.assert_allclose(simplify(line), [[0, 0], [0, 2], [2, 2]])

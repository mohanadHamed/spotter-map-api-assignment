import random
from types import SimpleNamespace

from django.test import SimpleTestCase

from fuelroute.exceptions import InfeasibleRoute
from fuelroute.services.fuel_optimizer import plan_fuel_stops

RANGE = 500
MPG = 10


def station(mile, price):
    return SimpleNamespace(mile_marker=mile, price=price)


def reference_greedy_cost(stations, route_miles, range_miles, mpg):
    """Exact cheapest cost (no stop penalty) via the classic greedy algorithm, for cross-checking."""
    nodes = [(0.0, 0.0)] + sorted((s.mile_marker, s.price) for s in stations) + [(route_miles, float("-inf"))]
    current, fuel, cost, last = 0, range_miles, 0.0, len(nodes) - 1
    while current != last:
        mile, price = nodes[current]
        reachable = [i for i in range(current + 1, last + 1) if nodes[i][0] - mile <= range_miles]
        if not reachable:
            return None
        cheaper = next((i for i in reachable if nodes[i][1] < price), None)
        if cheaper is not None:
            target, buy = cheaper, max(0.0, nodes[cheaper][0] - mile - fuel)
        else:
            target, buy = min(reachable, key=lambda i: (nodes[i][1], -nodes[i][0])), range_miles - fuel
        if current:
            cost += buy / mpg * price
        fuel = min(range_miles, fuel + buy) - (nodes[target][0] - mile)
        current = target
    return cost


class FuelOptimizerTests(SimpleTestCase):
    def test_short_trip_needs_no_stop(self):
        plan = plan_fuel_stops([station(100, 3.0)], 450, RANGE, MPG)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0)
        self.assertAlmostEqual(plan.total_gallons_used, 45)
        self.assertAlmostEqual(plan.starting_tank_gallons_used, 45)
        self.assertAlmostEqual(plan.fuel_at_destination_gallons, 5)

    def test_skips_expensive_station_for_cheaper_one_in_range(self):
        plan = plan_fuel_stops([station(400, 4.0), station(450, 3.0)], 900, RANGE, MPG, stop_penalty=0)
        self.assertEqual(len(plan.stops), 1)
        stop = plan.stops[0]
        self.assertEqual(stop.station.mile_marker, 450)
        self.assertAlmostEqual(stop.fuel_on_arrival_gallons, 5)
        self.assertAlmostEqual(stop.gallons, 40)  # just enough to reach the destination
        self.assertAlmostEqual(plan.total_cost, 120.0)
        self.assertAlmostEqual(plan.fuel_at_destination_gallons, 0)

    def test_fills_up_where_fuel_is_cheapest(self):
        plan = plan_fuel_stops([station(300, 2.5), station(700, 4.0)], 1100, RANGE, MPG, stop_penalty=0)
        self.assertEqual([(s.station.mile_marker, round(s.gallons, 2)) for s in plan.stops], [(300, 30.0), (700, 30.0)])
        self.assertAlmostEqual(plan.total_cost, 30 * 2.5 + 30 * 4.0)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        plan = plan_fuel_stops([station(400, 3.0), station(520, 2.99)], 900, RANGE, MPG, stop_penalty=0)
        self.assertEqual([(s.station.mile_marker, round(s.gallons, 2)) for s in plan.stops], [(400, 2.0), (520, 38.0)])
        self.assertAlmostEqual(plan.total_cost, 2 * 3.0 + 38 * 2.99)

    def test_stop_penalty_avoids_tiny_top_ups(self):
        plan = plan_fuel_stops([station(400, 3.0), station(520, 2.99)], 900, RANGE, MPG, stop_penalty=10)
        self.assertEqual([(s.station.mile_marker, round(s.gallons, 2)) for s in plan.stops], [(400, 40.0)])
        self.assertAlmostEqual(plan.total_cost, 120.0)

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaises(InfeasibleRoute) as ctx:
            plan_fuel_stops([station(300, 3.0), station(850, 3.0)], 1200, RANGE, MPG)
        self.assertEqual(ctx.exception.extra, {"gap_start_mile": 300.0, "gap_end_mile": 850.0})

    def test_no_stations_on_long_route_is_infeasible(self):
        with self.assertRaises(InfeasibleRoute):
            plan_fuel_stops([], 600, RANGE, MPG)

    def test_totals_are_consistent(self):
        stations = [station(m, p) for m, p in [(150, 3.4), (420, 3.1), (610, 3.6), (880, 2.9), (1250, 3.3), (1500, 3.0)]]
        plan = plan_fuel_stops(stations, 1800, RANGE, MPG)
        self.assertAlmostEqual(plan.total_cost, sum(s.gallons * s.station.price for s in plan.stops))
        self.assertAlmostEqual(
            plan.total_gallons_purchased + plan.tank_capacity_gallons,
            plan.total_gallons_used + plan.fuel_at_destination_gallons,
            places=6,
        )
        for stop in plan.stops:
            self.assertLessEqual(stop.fuel_on_arrival_gallons + stop.gallons, plan.tank_capacity_gallons + 1e-9)

    def test_matches_exact_greedy_on_random_routes(self):
        rng = random.Random(42)
        for _ in range(150):
            route_miles = rng.uniform(600, 3000)
            stations = [station(rng.uniform(0, route_miles), round(rng.uniform(2.7, 4.2), 3)) for _ in range(rng.randint(5, 250))]
            expected = reference_greedy_cost(stations, route_miles, RANGE, MPG)
            if expected is None:
                with self.assertRaises(InfeasibleRoute):
                    plan_fuel_stops(stations, route_miles, RANGE, MPG, stop_penalty=0)
                continue
            actual = plan_fuel_stops(stations, route_miles, RANGE, MPG, stop_penalty=0).total_cost
            # The DP buys fuel in 0.05 gallon steps, so it can be a few cents above the exact optimum.
            self.assertGreaterEqual(actual, expected - 1e-6)
            self.assertLess(actual - expected, 0.5)

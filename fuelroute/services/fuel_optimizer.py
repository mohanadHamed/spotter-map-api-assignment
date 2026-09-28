"""
Cheapest refuelling plan along a fixed route.

Model
-----
* The truck leaves with a full tank (``range_miles`` of range) at no cost to
  this trip, and must reach the destination; it never needs to arrive with
  spare fuel.
* Along the route there are stations at known mile markers with known prices.
  At each one the truck may buy any amount, up to a full tank.
* Every stop optionally costs a fixed ``stop_penalty`` (USD). With a penalty of
  0 the plan is the pure cheapest one, which tends to add 1-2 gallon "top-ups"
  whenever a slightly cheaper station is ahead. A small penalty (e.g. $10) only
  allows an extra stop when it saves at least that much.

Algorithm
---------
Dynamic programming over (station, reach), where *reach* is the absolute mile
marker at which the tank would run dry. Driving between stations does not
change reach, buying fuel raises it, and reach is capped at
``position + range_miles``. Reach is discretised to ``step_miles`` (0.5 mile =
0.05 gal at 10 MPG) on a global grid, so no rounding error accumulates along
the route. Each station is processed with a handful of vectorised numpy
operations over a window of ``range_miles / step_miles`` grid points, so
coast-to-coast routes with hundreds of candidate stations take a few ms.
"""

import math
from dataclasses import dataclass, field

import numpy as np

from ..exceptions import InfeasibleRoute

DEFAULT_STEP_MILES = 0.5


@dataclass
class FuelStop:
    station: object  # CorridorStation (or anything with mile_marker and price)
    fuel_on_arrival_gallons: float
    gallons: float
    cost: float


@dataclass
class FuelPlan:
    route_miles: float
    mpg: float
    tank_capacity_gallons: float
    stops: list = field(default_factory=list)
    fuel_at_destination_gallons: float = 0.0

    @property
    def total_cost(self):
        return sum(stop.cost for stop in self.stops)

    @property
    def total_gallons_purchased(self):
        return sum(stop.gallons for stop in self.stops)

    @property
    def total_gallons_used(self):
        return self.route_miles / self.mpg

    @property
    def starting_tank_gallons_used(self):
        return max(0.0, min(self.tank_capacity_gallons, self.total_gallons_used))


def plan_fuel_stops(stations, route_miles, range_miles, mpg, stop_penalty=0.0, step_miles=DEFAULT_STEP_MILES):
    """Return the cheapest :class:`FuelPlan` (fuel cost + ``stop_penalty`` per stop).

    ``stations`` must expose ``mile_marker`` and ``price`` (USD/gallon).
    Raises :class:`InfeasibleRoute` if some stretch of the route is longer than
    the truck's range with no station in between.
    """
    if range_miles <= 0 or mpg <= 0 or step_miles <= 0:
        raise ValueError("range_miles, mpg and step_miles must be positive")

    plan = FuelPlan(route_miles=route_miles, mpg=mpg, tank_capacity_gallons=range_miles / mpg)
    stations = sorted(
        (s for s in stations if 0.0 <= s.mile_marker <= route_miles),
        key=lambda s: (s.mile_marker, s.price),
    )
    _check_gaps(stations, route_miles, range_miles)

    if route_miles <= range_miles:
        plan.fuel_at_destination_gallons = (range_miles - route_miles) / mpg
        return plan

    # Grid of reach values: index k <=> reach = k * step_miles (absolute mile marker).
    size = int(math.ceil((route_miles + range_miles) / step_miles)) + 2
    cost = np.full(size, np.inf)
    start_reach = int(math.floor(range_miles / step_miles + 1e-9))
    cost[start_reach] = 0.0
    grid = np.arange(size, dtype=float)

    decisions = []  # per station: (lo, src) where src[j] = reach index before buying, or -1 for "no stop"
    for station in stations:
        lo = int(math.ceil(station.mile_marker / step_miles - 1e-9))
        hi = min(size - 1, int(math.floor((station.mile_marker + range_miles) / step_miles + 1e-9)))
        cost[:lo] = np.inf  # could not have reached this station

        window = cost[lo:hi + 1]
        per_step = step_miles / mpg * station.price
        # Buying from reach k to reach k' (k < k') costs (k' - k) * per_step + penalty.
        shifted = window - grid[lo:hi + 1] * per_step
        best_prior = np.minimum.accumulate(shifted)
        best_prior_idx = _running_argmin(shifted)
        candidate = np.full(window.shape, np.inf)
        candidate[1:] = best_prior[:-1] + grid[lo + 1:hi + 1] * per_step + stop_penalty

        buy = candidate < window
        src = np.where(buy, np.concatenate(([-1], best_prior_idx[:-1] + lo)), -1)
        cost[lo:hi + 1] = np.where(buy, candidate, window)
        decisions.append((lo, src))

    need = int(math.ceil(route_miles / step_miles - 1e-9))
    final = int(np.argmin(cost[need:])) + need
    if not np.isfinite(cost[final]):
        raise InfeasibleRoute("The destination cannot be reached with the available fuel stations.")

    # Walk back through the decisions to recover purchases.
    reach = final
    purchases = []
    for station, (lo, src) in zip(reversed(stations), reversed(decisions)):
        offset = reach - lo
        if 0 <= offset < len(src) and src[offset] >= 0:
            before = int(src[offset])
            purchases.append((station, before, reach))
            reach = before
    for station, before, after in reversed(purchases):
        gallons = (after - before) * step_miles / mpg
        plan.stops.append(
            FuelStop(
                station=station,
                fuel_on_arrival_gallons=max(0.0, before * step_miles - station.mile_marker) / mpg,
                gallons=gallons,
                cost=gallons * station.price,
            )
        )
    plan.fuel_at_destination_gallons = max(0.0, final * step_miles - route_miles) / mpg
    return plan


def _running_argmin(values):
    """Index of the running minimum of ``values`` (first occurrence), vectorised."""
    is_new_min = np.empty(values.shape, dtype=bool)
    running = np.minimum.accumulate(values)
    is_new_min[0] = True
    is_new_min[1:] = values[1:] < running[:-1]
    indices = np.where(is_new_min, np.arange(values.size), 0)
    return np.maximum.accumulate(indices)


def _check_gaps(stations, route_miles, range_miles):
    positions = [0.0] + [s.mile_marker for s in stations] + [route_miles]
    for before, after in zip(positions, positions[1:]):
        if after - before > range_miles:
            raise InfeasibleRoute(
                f"No fuel station within {range_miles:.0f} miles between mile {before:.1f} "
                f"and mile {after:.1f} of the route.",
                gap_start_mile=round(before, 1),
                gap_end_mile=round(after, 1),
            )

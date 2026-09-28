"""Glue: geocode -> route (1 external call, cached) -> stations in corridor -> optimal fuel plan."""

import time

from django.conf import settings

from .fuel_optimizer import plan_fuel_stops
from .geocoding import geocode
from .routing import get_routing_client
from .station_index import get_station_index


def plan_route(start, finish, corridor_miles=None, stop_penalty_usd=None):
    started = time.perf_counter()
    corridor_miles = float(corridor_miles or settings.DEFAULT_CORRIDOR_MILES)
    if stop_penalty_usd is None:
        stop_penalty_usd = settings.DEFAULT_STOP_PENALTY_USD
    range_miles = settings.VEHICLE_RANGE_MILES
    mpg = settings.VEHICLE_MPG

    index = get_station_index()  # fail fast, before any external call, if station data is missing
    origin, origin_calls = geocode(start)
    destination, destination_calls = geocode(finish)

    route, routing_calls = get_routing_client().get_route(origin, destination)

    stations, corridor_matches = index.stations_along_route(route.coords, route.distance_miles, corridor_miles)
    plan = plan_fuel_stops(stations, route.distance_miles, range_miles, mpg, stop_penalty=stop_penalty_usd)

    # Price the starting tank at the cheapest station the truck passes on its first tank,
    # to give an estimate of what the whole trip's fuel is worth.
    first_leg = [s.price for s in stations if s.mile_marker <= range_miles]
    reference_price = min(first_leg) if first_leg else index.average_price
    starting_tank_cost = plan.starting_tank_gallons_used * reference_price

    fuel_stops = [_stop_dict(number, stop) for number, stop in enumerate(plan.stops, start=1)]
    total_cost = plan.total_cost
    gallons_bought = plan.total_gallons_purchased

    return {
        "start": origin.as_dict(),
        "finish": destination.as_dict(),
        "route": {
            "provider": route.provider,
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_seconds / 3600, 2),
            "polyline6": route.display_polyline,
        },
        "vehicle": {
            "range_miles": range_miles,
            "mpg": mpg,
            "tank_capacity_gallons": round(range_miles / mpg, 2),
            "start_fuel": "full",
        },
        "fuel_stops": fuel_stops,
        "summary": {
            "total_fuel_cost_usd": round(total_cost, 2),
            "total_gallons_purchased": round(gallons_bought, 2),
            "number_of_stops": len(fuel_stops),
            "average_price_paid_usd": round(total_cost / gallons_bought, 3) if gallons_bought else None,
            "total_gallons_used": round(plan.total_gallons_used, 2),
            "starting_tank_gallons_used": round(plan.starting_tank_gallons_used, 2),
            "starting_tank_reference_price_usd": round(reference_price, 3),
            "estimated_full_trip_cost_usd": round(total_cost + starting_tank_cost, 2),
            "fuel_left_at_destination_gallons": round(plan.fuel_at_destination_gallons, 2),
            "corridor_miles": corridor_miles,
            "stop_penalty_usd": stop_penalty_usd,
            "stations_in_corridor": corridor_matches,
        },
        "map": {
            "geojson": _feature_collection(origin, destination, route.display_coords, fuel_stops),
        },
        "meta": {
            "routing_api_calls": routing_calls,
            "geocoding_api_calls": origin_calls + destination_calls,
            "route_cached": routing_calls == 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    }


def _stop_dict(number, stop):
    station = stop.station
    return {
        "stop_number": number,
        "opis_id": station.opis_id,
        "name": station.name,
        "address": station.address,
        "city": station.city,
        "state": station.state,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "price_per_gallon_usd": round(station.price, 3),
        "mile_marker": round(station.mile_marker, 1),
        "off_route_miles": round(station.off_route_miles, 1),
        "fuel_on_arrival_gallons": round(stop.fuel_on_arrival_gallons, 2),
        "gallons_purchased": round(stop.gallons, 2),
        "cost_usd": round(stop.cost, 2),
    }


def _point(lat, lon, properties):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": properties,
    }


def _feature_collection(origin, destination, line, fuel_stops):
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": line[:, ::-1].round(5).tolist(),
            },
            "properties": {"kind": "route"},
        },
        _point(origin.latitude, origin.longitude, {"kind": "start", "label": origin.label}),
        _point(destination.latitude, destination.longitude, {"kind": "finish", "label": destination.label}),
    ]
    for stop in fuel_stops:
        features.append(
            _point(
                stop["latitude"],
                stop["longitude"],
                {
                    "kind": "fuel_stop",
                    "stop_number": stop["stop_number"],
                    "name": stop["name"],
                    "city": stop["city"],
                    "state": stop["state"],
                    "price_per_gallon_usd": stop["price_per_gallon_usd"],
                    "gallons_purchased": stop["gallons_purchased"],
                    "cost_usd": stop["cost_usd"],
                    "mile_marker": stop["mile_marker"],
                },
            )
        )
    return {"type": "FeatureCollection", "features": features}

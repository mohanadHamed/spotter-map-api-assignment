"""
Routing providers.

Each request needs exactly one routing call: the full route geometry and its
distance come back in a single response. Results are cached (Django cache)
by provider + rounded coordinates, so repeated requests - including the map
page for a route that was just planned - make no external call at all.
"""

import logging
from dataclasses import dataclass, field

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from ..exceptions import ExternalServiceError, ExternalServiceTimeout, NoRouteFound
from .geo import METERS_PER_MILE, decode_polyline, encode_polyline, simplify

logger = logging.getLogger(__name__)

_session = requests.Session()


@dataclass
class Route:
    distance_miles: float
    duration_seconds: float
    coords: np.ndarray  # (N, 2) [lat, lon], full resolution
    provider: str
    # Simplified geometry for responses/maps, computed once and cached with the route.
    display_coords: np.ndarray = field(default=None, repr=False)
    display_polyline: str = field(default="", repr=False)

    def __post_init__(self):
        if self.display_coords is None:
            self.display_coords = simplify(self.coords)
            self.display_polyline = encode_polyline(self.display_coords)


class RoutingClient:
    name = "base"

    def get_route(self, start, finish):
        """Return ``(route, api_calls_made)``; served from cache when possible."""
        key = "route:{}:{:.5f},{:.5f}:{:.5f},{:.5f}".format(
            self.name, start.latitude, start.longitude, finish.latitude, finish.longitude
        )
        route = cache.get(key)
        if route is not None:
            return route, 0
        route = self.fetch_route(start, finish)
        cache.set(key, route, settings.ROUTE_CACHE_SECONDS)
        return route, 1

    def fetch_route(self, start, finish):
        raise NotImplementedError

    def _request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (settings.HTTP_CONNECT_TIMEOUT, settings.HTTP_READ_TIMEOUT))
        headers = {"User-Agent": settings.HTTP_USER_AGENT, **kwargs.pop("headers", {})}
        # One retry on dropped connections; public routing servers occasionally reset them.
        for attempt in range(2):
            try:
                return _session.request(method, url, headers=headers, **kwargs)
            except requests.Timeout as exc:
                raise ExternalServiceTimeout(f"{self.name} routing service timed out.") from exc
            except requests.ConnectionError as exc:
                if attempt == 1:
                    raise ExternalServiceError(f"Could not reach the {self.name} routing service.") from exc
                logger.warning("Routing connection error, retrying: %s", exc)


class OSRMClient(RoutingClient):
    """Open Source Routing Machine - public FOSSGIS server, no API key needed."""

    name = "osrm"

    def fetch_route(self, start, finish):
        url = "{}/route/v1/driving/{:.6f},{:.6f};{:.6f},{:.6f}".format(
            settings.OSRM_BASE_URL, start.longitude, start.latitude, finish.longitude, finish.latitude
        )
        params = {"overview": "full", "geometries": "polyline6", "steps": "false", "alternatives": "false"}
        response = self._request("GET", url, params=params)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        code = payload.get("code")
        if code in {"NoRoute", "NoSegment"}:
            raise NoRouteFound("No drivable route was found between the two locations.")
        if response.status_code != 200 or code != "Ok":
            raise ExternalServiceError(
                f"OSRM returned HTTP {response.status_code}: {payload.get('message', response.text[:200])}"
            )
        best = payload["routes"][0]
        return Route(
            distance_miles=best["distance"] / METERS_PER_MILE,
            duration_seconds=best["duration"],
            coords=decode_polyline(best["geometry"], precision=6),
            provider=self.name,
        )


class OpenRouteServiceClient(RoutingClient):
    """openrouteservice.org - free API key, supports a heavy-goods-vehicle profile."""

    name = "ors"

    def fetch_route(self, start, finish):
        if not settings.ORS_API_KEY:
            raise ExternalServiceError("ROUTING_PROVIDER is 'ors' but ORS_API_KEY is not set.")
        url = f"{settings.ORS_BASE_URL}/v2/directions/{settings.ORS_PROFILE}/geojson"
        body = {
            "coordinates": [[start.longitude, start.latitude], [finish.longitude, finish.latitude]],
            "radiuses": [5000, 5000],
            "instructions": False,
        }
        response = self._request("POST", url, json=body, headers={"Authorization": settings.ORS_API_KEY})
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code != 200:
            error = payload.get("error", {})
            message = error.get("message", response.text[:200]) if isinstance(error, dict) else str(error)
            if response.status_code == 404 or (isinstance(error, dict) and error.get("code") in {2009, 2010}):
                raise NoRouteFound(f"No drivable route was found: {message}")
            raise ExternalServiceError(f"OpenRouteService returned HTTP {response.status_code}: {message}")
        feature = payload["features"][0]
        summary = feature["properties"]["summary"]
        coords = np.array(feature["geometry"]["coordinates"], dtype=float)[:, [1, 0]]
        return Route(
            distance_miles=summary["distance"] / METERS_PER_MILE,
            duration_seconds=summary["duration"],
            coords=coords,
            provider=self.name,
        )


_CLIENTS = {OSRMClient.name: OSRMClient, OpenRouteServiceClient.name: OpenRouteServiceClient}


def get_routing_client():
    try:
        return _CLIENTS[settings.ROUTING_PROVIDER]()
    except KeyError:
        raise ExternalServiceError(
            f"Unknown ROUTING_PROVIDER '{settings.ROUTING_PROVIDER}'. Use one of: {', '.join(_CLIENTS)}."
        ) from None

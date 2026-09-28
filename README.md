# Fuel Route Planner API

A Django REST API that takes a start and finish location in the USA and returns:

- the driving route, as GeoJSON, an encoded polyline and an interactive map page,
- the most cost-effective places to buy fuel along the way, for a truck with a **500-mile range**,
- how many gallons to buy at each stop, and the **total fuel cost** at **10 MPG**.

Fuel prices come from the provided OPIS truck-stop price list (`data/fuel-prices-for-be-assessment.csv`).

Each new route makes **one** call to the external routing API. Repeated routes, and the map page for a route that was just planned, make **none**.

| Scenario | Response time |
| --- | --- |
| New route (dominated by the routing API round trip) | ~0.3–1.5 s |
| Cached route | ~25 ms |

---

## Quick start

Requires Python 3.12+ (Django 6.1).

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py load_fuel_stations  # loads the pre-geocoded stations into SQLite
python manage.py runserver
```

Then open:

- JSON: http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA
- Map: http://127.0.0.1:8000/api/route/map/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA

No API keys are needed. A Postman collection is included in `postman_collection.json`.

## API

### `GET /api/route/` or `POST /api/route/`

| Parameter | Required | Description |
| --- | --- | --- |
| `start` | yes | Start location in the USA (formats below). |
| `finish` | yes | Finish location in the USA. |
| `corridor_miles` | no | Maximum distance from the route for a station to be considered. 1–50, default `10`. |
| `stop_penalty_usd` | no | An extra stop is only made if it saves at least this much. 0–500, default `10`. `0` gives the absolute cheapest fuel bill. |

Accepted location formats:

| Format | Example | Resolved |
| --- | --- | --- |
| City and state | `"Chicago, IL"`, `"Chicago, Illinois"`, `"chicago il"` | offline |
| ZIP code | `"60601"` | offline |
| Coordinates | `"41.8781,-87.6298"` | offline |
| Address or landmark | `"1600 Pennsylvania Ave NW, Washington, DC"` | Nominatim |

The first three are resolved offline. Anything else falls back to Nominatim (OpenStreetMap), and the result is cached.

POST takes the same fields as a JSON body:

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
     -H "Content-Type: application/json" \
     -d '{"start": "Dallas, TX", "finish": "Miami, FL"}'
```

Response (geometry shortened):

```json
{
  "start":  {"query": "Dallas, TX", "label": "Dallas, TX", "latitude": 32.79333, "longitude": -96.76651, "source": "gazetteer"},
  "finish": {"query": "Miami, FL",  "label": "Miami, FL",  "latitude": 25.77516, "longitude": -80.20861, "source": "gazetteer"},
  "route": {"provider": "osrm", "distance_miles": 1305.8, "duration_hours": 24.0, "polyline6": "aipp}@`|cqwD..."},
  "vehicle": {"range_miles": 500.0, "mpg": 10.0, "tank_capacity_gallons": 50.0, "start_fuel": "full"},
  "fuel_stops": [
    {
      "stop_number": 1, "opis_id": 65198, "name": "CIRCLE K #2723437", "address": "US-49",
      "city": "Florence", "state": "MS", "latitude": 32.155615, "longitude": -90.122454,
      "price_per_gallon_usd": 2.896, "mile_marker": 410.3, "off_route_miles": 0.1,
      "fuel_on_arrival_gallons": 8.97, "gallons_purchased": 41.0, "cost_usd": 118.72
    },
    {
      "stop_number": 2, "opis_id": 66799, "name": "SHELL DBA ONE9 #1403", "address": "I-75/SR-93, EXIT 451 & US-129",
      "city": "Jasper", "state": "FL", "latitude": 30.455935, "longitude": -82.934214,
      "price_per_gallon_usd": 3.244, "mile_marker": 906.1, "off_route_miles": 8.2,
      "fuel_on_arrival_gallons": 0.39, "gallons_purchased": 39.6, "cost_usd": 128.46
    }
  ],
  "summary": {
    "total_fuel_cost_usd": 247.18,
    "total_gallons_purchased": 80.6,
    "number_of_stops": 2,
    "average_price_paid_usd": 3.067,
    "total_gallons_used": 130.58,
    "starting_tank_gallons_used": 50.0,
    "starting_tank_reference_price_usd": 2.776,
    "estimated_full_trip_cost_usd": 385.97,
    "fuel_left_at_destination_gallons": 0.02,
    "corridor_miles": 10.0,
    "stop_penalty_usd": 10.0,
    "stations_in_corridor": 179
  },
  "map": {
    "geojson": {"type": "FeatureCollection", "features": ["route LineString, start/finish and fuel stop Points"]},
    "map_url": "http://127.0.0.1:8000/api/route/map/?start=Dallas%2C+TX&finish=Miami%2C+FL"
  },
  "meta": {"routing_api_calls": 1, "geocoding_api_calls": 0, "route_cached": false, "elapsed_ms": 283.0}
}
```

Key fields:

- **`summary.total_fuel_cost_usd`**: the money spent on fuel during the trip. The truck starts with a full tank, so this is the fuel bought at the stops.
- **`summary.estimated_full_trip_cost_usd`**: also counts the fuel burned from the starting tank. That fuel is priced at the cheapest station on the first 500 miles of the route.
- **`map.geojson`**: can be dropped straight into geojson.io, Leaflet, Mapbox and similar tools.
- **`map.map_url`**: an HTML page showing the route and stops on a Leaflet/OpenStreetMap map. Postman shows it under *Preview*.

### `GET /api/route/map/`

Takes the same query parameters as `/api/route/` and returns the interactive HTML map. It reuses the cached route, so it makes no extra routing call.

### Errors

Errors use the shape `{"error": "<code>", "detail": "..."}`.

| HTTP | `error` | When |
| --- | --- | --- |
| 400 | `invalid_request` | Missing or invalid parameters. |
| 422 | `location_not_found` | A location could not be resolved. |
| 422 | `outside_usa` | A location is outside the USA (checked against the Census US outline). |
| 422 | `no_route` | No drivable route exists (e.g. to Hawaii). |
| 422 | `infeasible_route` | A stretch of the route is longer than 500 miles with no station. The gap's start and end mile are included. |
| 502 / 504 | `upstream_error` / `upstream_timeout` | The routing or geocoding service failed or timed out. |
| 503 | `station_data_missing` | `load_fuel_stations` has not been run. |

## How it works

```
start, finish ──► geocode (offline) ──► route (1 OSRM call, cached 24 h)
                                              │
                   stations KD-tree ◄─────────┘  match stations within the corridor → mile markers
                          │
                          └──► DP optimiser ──► stops, gallons, cost ──► JSON / GeoJSON / map page
```

1. **Station data, prepared once and committed.** The price list only has city and state for each station. `manage.py geocode_fuel_stations` places every US station at its town's coordinates:
   - It uses the public-domain US Census 2023 Gazetteer (places and county subdivisions), with name normalisation such as `Mc Alpin`→`McAlpin`, `Saint`→`St` and `Kansas City city`→`Kansas City`.
   - The ~140 towns missing from the Gazetteer are looked up with Nominatim.
   - All 7,531 US rows were resolved: 7,323 offline and 208 via Nominatim.
   - Canadian rows are dropped.
   - `load_fuel_stations` keeps one row per OPIS ID, at its lowest listed price, which leaves 6,626 stations.
2. **Geocoding the request** normally makes no external call. City/state, ZIP codes and coordinates are resolved from compact offline tables in `data/`.
3. **Routing** makes one call to OSRM's `/route` endpoint, which returns the full geometry as `polyline6`. The route, plus a simplified display version of it, is cached by rounded coordinates.
4. **Stations along the route.**
   - All stations are kept in memory as numpy arrays.
   - The route is densified to 0.5-mile samples and put in a `scipy` KD-tree.
   - Each station within `corridor_miles` gets a mile marker (its position along the route) and its off-route distance.
   - Stations in the same town are collapsed to the cheapest one.
5. **Optimisation** is a dynamic programme over *(station, reach)*, where reach is the mile at which the tank would run dry. Buying fuel raises reach, capped at the station's position plus 500 miles. Each stop costs `stop_penalty_usd`. With a penalty of 0 the result matches the classic optimal greedy algorithm for the fixed-route gas-station problem ("fill up if nothing cheaper is in range, otherwise buy just enough to reach the next cheaper station"); the tests cross-check the two on random routes.

   The penalty exists because the absolute cheapest plan for New York → Los Angeles has 15 stops, several of them 1-gallon top-ups, and costs $698.65. With the default $10 penalty the plan has 6 stops and costs $705.68.

## Assumptions

- The truck starts with a **full tank** (500 miles of range) and should arrive with as little fuel as possible. Tank capacity is 500 mi ÷ 10 MPG = 50 gallons.
- A station's location is its **town's centroid**, because the price list has no coordinates. `off_route_miles` is therefore approximate, and the detour to a station is not counted in the distance.
- Where the price list has several prices for the same station, the lowest one is used.
- Only US stations are used, and both endpoints must be in the USA.

## Configuration

Settings can be overridden with environment variables or a `.env` file; see `.env.example`.

| Variable | Default | |
| --- | --- | --- |
| `ROUTING_PROVIDER` | `osrm` | `osrm` (no key) or `ors` (OpenRouteService). |
| `OSRM_BASE_URL` | `https://routing.openstreetmap.de/routed-car` | Any OSRM server. |
| `ORS_API_KEY` / `ORS_PROFILE` | – / `driving-hgv` | Used when `ROUTING_PROVIDER=ors`. |
| `VEHICLE_RANGE_MILES` / `VEHICLE_MPG` | `500` / `10` | |
| `DEFAULT_CORRIDOR_MILES` | `10` | |
| `DEFAULT_STOP_PENALTY_USD` | `10` | |
| `ROUTE_CACHE_SECONDS` | `86400` | |
| `CACHE_BACKEND` / `CACHE_LOCATION` | local memory | E.g. Redis or file-based, to share the cache between workers. |

## Tests

```bash
python manage.py test
```

The external services are mocked. The tests cover:

- geometry helpers and polyline encoding,
- corridor matching,
- the optimiser, including a random cross-check against the exact greedy algorithm,
- offline geocoding and the USA boundary check,
- the API: responses, caching, validation and error codes, and the map page.

## Rebuilding the station data (optional)

The generated files in `data/` are committed. To rebuild them from the original price list:

```bash
python manage.py geocode_fuel_stations   # downloads Census files to data/cache/, ~3 min with Nominatim throttling
python manage.py load_fuel_stations
```

## Project layout

```
config/                      Django settings and root URLs
fuelroute/
  models.py                  FuelStation
  views.py, serializers.py   REST endpoint and map page
  exceptions.py              domain errors -> JSON error responses
  services/
    geocoding.py             offline gazetteer, ZIP, coordinates, Nominatim fallback, USA check
    routing.py               OSRM / OpenRouteService clients (+ caching)
    station_index.py         in-memory stations + KD-tree corridor matching
    fuel_optimizer.py        refuelling DP
    planner.py               orchestration and response building
    geo.py, places.py        geometry and place-name helpers
  management/commands/       geocode_fuel_stations, load_fuel_stations
  templates/fuelroute/       Leaflet map page
  tests/
data/                        price list + generated station/gazetteer files
```

## Data sources

- Fuel prices: the provided OPIS truck-stop price list.
- Place, ZIP and US outline data: US Census Bureau 2023 Gazetteer and cartographic boundary files (public domain).
- Routing: [OSRM](https://project-osrm.org/) on the FOSSGIS public server. Map data © OpenStreetMap contributors (ODbL).
- Geocoding fallback: [Nominatim](https://nominatim.org/), used within its usage policy (≤1 request/s, cached).
- Map page: [Leaflet](https://leafletjs.com/) with OpenStreetMap tiles.

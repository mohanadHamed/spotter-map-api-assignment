"""Small, dependency-free (numpy only) geometry helpers for route processing."""

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles. Works on scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def to_cartesian(lat, lon):
    """Project lat/lon (degrees) onto a sphere of Earth's radius, in miles.

    Straight-line (chord) distance between two projected points is within 0.01%
    of the great-circle distance for the short ranges used here (< 100 miles),
    which lets a KD-tree answer "which stations are near this route" directly.
    """
    lat = np.radians(np.asarray(lat, dtype=float))
    lon = np.radians(np.asarray(lon, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat))) * EARTH_RADIUS_MILES


def decode_polyline(encoded, precision=6):
    """Decode a Google encoded polyline into an (N, 2) array of [lat, lon]."""
    factor = 10 ** precision
    coords = []
    index = lat = lon = 0
    length = len(encoded)
    while index < length:
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        coords.append((lat / factor, lon / factor))
    return np.array(coords, dtype=float).reshape(-1, 2)


def encode_polyline(coords, precision=6):
    """Encode an iterable of [lat, lon] pairs as a Google encoded polyline."""
    factor = 10 ** precision
    output = []
    prev_lat = prev_lon = 0
    for lat, lon in coords:
        lat_i, lon_i = int(round(lat * factor)), int(round(lon * factor))
        for delta in (lat_i - prev_lat, lon_i - prev_lon):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                output.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            output.append(chr(value + 63))
        prev_lat, prev_lon = lat_i, lon_i
    return "".join(output)


def segment_lengths(coords):
    """Length in miles of each consecutive segment of an (N, 2) [lat, lon] array."""
    return haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])


def cumulative_miles(coords):
    """Distance in miles from the first point to every point along the line."""
    if len(coords) < 2:
        return np.zeros(len(coords))
    return np.concatenate(([0.0], np.cumsum(segment_lengths(coords))))


def densify(coords, max_step_miles):
    """Insert points so no segment is longer than ``max_step_miles``.

    Returns ``(dense_coords, dense_cumulative_miles)``. Long straight highway
    segments often have vertices many miles apart; densifying keeps the
    nearest-vertex distance a good approximation of the distance to the road.
    """
    coords = np.asarray(coords, dtype=float)
    if len(coords) < 2:
        return coords, np.zeros(len(coords))
    seg = segment_lengths(coords)
    cum = np.concatenate(([0.0], np.cumsum(seg)))
    pieces = np.maximum(1, np.ceil(seg / max_step_miles)).astype(int)
    seg_index = np.repeat(np.arange(len(seg)), pieces)
    offsets = np.arange(seg_index.size) - np.repeat(np.cumsum(pieces) - pieces, pieces)
    fraction = offsets / pieces[seg_index]
    start, end = coords[seg_index], coords[seg_index + 1]
    dense = start + (end - start) * fraction[:, None]
    dense_cum = cum[seg_index] + seg[seg_index] * fraction
    return np.vstack((dense, coords[-1:])), np.concatenate((dense_cum, cum[-1:]))


def simplify(coords, tolerance_degrees=0.0008):
    """Ramer-Douglas-Peucker simplification (planar, in degrees) for display geometry.

    The default tolerance (~80 m) keeps the line visually identical on a map
    while typically dropping 80-90% of the vertices of a long route.
    """
    coords = np.asarray(coords, dtype=float)
    n = len(coords)
    if n < 3:
        return coords
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        a, b = coords[first], coords[last]
        points = coords[first + 1:last]
        ab = b - a
        norm = np.hypot(ab[0], ab[1])
        if norm == 0:
            distances = np.hypot(points[:, 0] - a[0], points[:, 1] - a[1])
        else:
            distances = np.abs(ab[0] * (points[:, 1] - a[1]) - ab[1] * (points[:, 0] - a[0])) / norm
        worst = int(np.argmax(distances))
        if distances[worst] > tolerance_degrees:
            split = first + 1 + worst
            keep[split] = True
            stack.append((first, split))
            stack.append((split, last))
    return coords[keep]

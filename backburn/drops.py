"""Shared drop geometry for previews, orders and payload accounting."""

import math


def length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def payload_per_cell(spec):
    # A consistent application density: larger tanks cover more ground, while
    # wider swaths use more payload per metre of the route.
    return float(spec["drop_width"]) * 3.0


def capacity_length(spec, payload):
    return max(0.0, payload) / payload_per_cell(spec)


def clip_path(points, maximum):
    if not points:
        return []
    result = [tuple(points[0])]
    remaining = max(0.0, maximum)
    for target in points[1:]:
        start = result[-1]
        distance = math.dist(start, target)
        if distance < 1e-8:
            continue
        if remaining <= 1e-8:
            break
        portion = min(1.0, remaining / distance)
        result.append(tuple(a + (b - a) * portion for a, b in zip(start, target)))
        remaining -= distance * portion
        if portion < 1:
            break
    return result


def smooth_path(points):
    """Round mouse-drawn corners while preserving start and end locations."""
    points = list(points)
    for _ in range(2):
        if len(points) < 3:
            break
        rounded = [points[0]]
        for a, b in zip(points, points[1:]):
            rounded.extend(
                [
                    tuple(0.75 * x + 0.25 * y for x, y in zip(a, b)),
                    tuple(0.25 * x + 0.75 * y for x, y in zip(a, b)),
                ]
            )
        points = rounded + [points[-1]]
    return points


def coverage_centers(points, spacing):
    """Evenly spaced circles along a polyline, including both endpoints."""
    if not points:
        return []
    output = [points[0]]
    remaining = spacing
    for start, end in zip(points, points[1:]):
        distance = math.dist(start, end)
        used = 0.0
        while remaining <= distance - used and distance > 0:
            used += remaining
            output.append(tuple(a + (b - a) * used / distance for a, b in zip(start, end)))
            remaining = spacing
        remaining -= distance - used
    if math.dist(output[-1], points[-1]) > 1e-6:
        output.append(points[-1])
    return output

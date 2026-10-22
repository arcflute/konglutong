"""Continuous geometry in local metres; space is closed, time is handled elsewhere.

AABB and capsule checks are exact for a spherical protection body. Cylinders use
a documented conservative cylinder (radius + protection, z +/- protection), which
may report false positives at cap rims but cannot omit the rounded swept body.
"""

from math import isfinite, sqrt

EPS = 1e-9


class UnsupportedGeometry(ValueError):
    """The geometry has no declared conservative checking implementation."""


def vector(value, size=3):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"expected {size} coordinates")
    result = tuple(float(x) for x in value)
    if not all(isfinite(x) for x in result):
        raise ValueError("nonfinite coordinate")
    return result


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def distance(a, b):
    return sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def interpolate(a, b, u):
    return [x + u * (y - x) for x, y in zip(a, b)]


def _merge(intervals):
    result = []
    for start, end in sorted(intervals):
        if start > end:
            continue
        if result and start <= result[-1][1] + EPS:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _quadratic_interval(a, b, c, lo=0.0, hi=1.0):
    """Closed portion of a*u*u+b*u+c <= 0, where a is nonnegative."""
    if a <= 1e-24:
        if abs(b) <= 1e-24:
            return [(lo, hi)] if c <= EPS else []
        root = -c / b
        left, right = (lo, min(hi, root)) if b > 0 else (max(lo, root), hi)
    else:
        discriminant = b * b - 4 * a * c
        tolerance = 1e-12 * max(1.0, b * b, abs(4 * a * c))
        if discriminant < -tolerance:
            return []
        root = sqrt(max(0.0, discriminant))
        left, right = max(lo, (-b - root) / (2 * a)), min(hi, (-b + root) / (2 * a))
    return [(left, right)] if left <= right else []


def _linear_interval(origin, delta, lower, upper, lo=0.0, hi=1.0):
    if abs(delta) <= 1e-15:
        return [(lo, hi)] if lower - EPS <= origin <= upper + EPS else []
    a, b = (lower - origin) / delta, (upper - origin) / delta
    left, right = max(lo, min(a, b)), min(hi, max(a, b))
    return [(left, right)] if left <= right else []


def _positive(value, name):
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def geometry_bounds(geometry, radius=0.0):
    """Conservative bounding box for broad phase, including protection once."""
    if not isinstance(geometry, dict):
        raise UnsupportedGeometry("geometry is unknown or is not an object")
    radius = _positive(radius, "radius")
    kind = geometry.get("type")
    if kind == "aabb":
        low, high = vector(geometry["min_m"]), vector(geometry["max_m"])
        if any(a > b for a, b in zip(low, high)):
            raise ValueError("reversed AABB bounds")
        return ([v - radius for v in low], [v + radius for v in high])
    if kind == "vertical_cylinder":
        x, y = vector(geometry["center_xy_m"], 2)
        r = _positive(geometry["radius_m"], "cylinder radius") + radius
        z0, z1 = float(geometry["z_min_m"]), float(geometry["z_max_m"])
        if not isfinite(z0) or not isfinite(z1) or z0 > z1:
            raise ValueError("invalid cylinder height bounds")
        return ([x-r, y-r, z0-radius], [x+r, y+r, z1+radius])
    if kind == "capsule":
        start, end = vector(geometry["start_m"]), vector(geometry["end_m"])
        r = _positive(geometry["radius_m"], "capsule radius") + radius
        return ([min(a,b)-r for a,b in zip(start,end)],
                [max(a,b)+r for a,b in zip(start,end)])
    raise UnsupportedGeometry(f"unsupported geometry: {kind!r}")


def segment_occupancy(p0, p1, geometry, radius=0.0):
    """Return all closed u intervals in [0,1] where a protected segment intersects.

    Positions follow p(u)=p0+u*(p1-p0). Unsupported or malformed geometry raises,
    allowing the caller to mark the affected check as incomplete.
    """
    p0, p1 = vector(p0), vector(p1)
    radius = _positive(radius, "protection radius")
    low, high = geometry_bounds(geometry, radius)
    if any(max(a,b) < c-EPS or min(a,b) > d+EPS
           for a,b,c,d in zip(p0,p1,low,high)):
        return []
    velocity = tuple(b-a for a,b in zip(p0,p1))
    kind = geometry["type"]
    if kind == "aabb":
        low, high = vector(geometry["min_m"]), vector(geometry["max_m"])
        cuts = {0.0, 1.0}
        for origin, delta, lower, upper in zip(p0,velocity,low,high):
            if abs(delta) > 1e-15:
                cuts.update(u for u in ((lower-origin)/delta, (upper-origin)/delta) if 0 < u < 1)
        cuts = sorted(cuts)
        intervals = []
        for left, right in zip(cuts, cuts[1:]):
            middle = (left+right)/2
            offsets, slopes = [], []
            for origin, delta, lower, upper in zip(p0,velocity,low,high):
                point = origin+middle*delta
                if point < lower:
                    offsets.append(origin-lower); slopes.append(delta)
                elif point > upper:
                    offsets.append(origin-upper); slopes.append(delta)
            intervals += _quadratic_interval(dot(slopes,slopes), 2*dot(offsets,slopes),
                                             dot(offsets,offsets)-radius*radius, left,right)
        return _merge(intervals)
    if kind == "vertical_cylinder":
        x, y = vector(geometry["center_xy_m"],2)
        radius += float(geometry["radius_m"])
        offset = (p0[0]-x,p0[1]-y)
        vxy = velocity[:2]
        horizontal = _quadratic_interval(dot(vxy,vxy),2*dot(offset,vxy),dot(offset,offset)-radius*radius)
        if not horizontal:
            return []
        return _linear_interval(p0[2],velocity[2],low[2],high[2],*horizontal[0])
    # Exact capsule = finite axial cylinder union the two spherical ends.
    start, end = vector(geometry["start_m"]), vector(geometry["end_m"])
    radius += float(geometry["radius_m"])
    axis = tuple(b-a for a,b in zip(start,end))
    offset = tuple(a-b for a,b in zip(p0,start))
    norm = dot(axis,axis)
    if norm <= 1e-24:
        return _quadratic_interval(dot(velocity,velocity),2*dot(offset,velocity),dot(offset,offset)-radius*radius)
    intervals = []
    for center in (start,end):
        off = tuple(a-b for a,b in zip(p0,center))
        intervals += _quadratic_interval(dot(velocity,velocity),2*dot(off,velocity),dot(off,off)-radius*radius)
    projection0, projection_v = dot(offset,axis)/norm,dot(velocity,axis)/norm
    axial_interval = _linear_interval(projection0,projection_v,0,1)
    if axial_interval:
        perpendicular0 = tuple(a-projection0*b for a,b in zip(offset,axis))
        perpendicular_v = tuple(a-projection_v*b for a,b in zip(velocity,axis))
        intervals += _quadratic_interval(dot(perpendicular_v,perpendicular_v),
                                        2*dot(perpendicular0,perpendicular_v),
                                        dot(perpendicular0,perpendicular0)-radius*radius,
                                        *axial_interval[0])
    return _merge(intervals)


def geometry_method(geometry):
    if geometry.get("type") == "vertical_cylinder":
        return "continuous_conservative_cylinder_radial_and_axial_expansion"
    return "continuous_exact_sphere_sweep"

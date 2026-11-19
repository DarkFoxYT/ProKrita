"""Fit a gesture in screen coordinates, with equal weight per unit of travel."""
from bisect import bisect_right
from math import atan2, cos, degrees, hypot, pi, sin, sqrt

from .geometry import recognize as axis_shape


def distance(a, b):
    return hypot(a[0] - b[0], a[1] - b[1])


def lengths(points):
    result = [0.]
    for a, b in zip(points, points[1:]):
        result.append(result[-1] + distance(a, b))
    return result


def sample(points, stops, at):
    i = min(len(points) - 2, max(0, bisect_right(stops, at) - 1))
    t = (at - stops[i]) / max(1e-9, stops[i+1] - stops[i])
    a, b = points[i:i+2]
    return (a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t)


def segment_error(p, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0., min(1., ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / max(1e-9, dx*dx+dy*dy)))
    return hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def simplify(points, tolerance):
    if len(points) < 3:
        return list(points)
    errors = [segment_error(p, points[0], points[-1]) for p in points]
    i = max(range(len(points)), key=errors.__getitem__)
    if errors[i] <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[:i+1], tolerance)[:-1] + simplify(points[i:], tolerance)


def circumcircle(a, b, c):
    x, y = b[0]-a[0], b[1]-a[1]
    u, v = c[0]-a[0], c[1]-a[1]
    det = 2*(x*v-y*u)
    if abs(det) < 1e-7:
        return None
    ab, ac = x*x+y*y, u*u+v*v
    return (a[0] + (v*ab-y*ac)/det, a[1] + (x*ac-u*ab)/det)


def arc_fit(path, scale, total):
    a, mid, end = path[0], path[len(path)//2], path[-1]
    center = circumcircle(a, mid, end)
    if center is None:
        return None
    radius = distance(a, center)
    err = [abs(distance(p, center)-radius) for p in path]
    if max(err) > max(2.8, .058*scale) or sum(err)/len(err) > max(1.2, .024*scale):
        return None
    angles = [atan2(p[1]-center[1], p[0]-center[0]) for p in path]
    steps = [(v-u+pi) % (2*pi) - pi for u, v in zip(angles, angles[1:])]
    sweep = sum(steps)
    if not .34 < abs(sweep) < 1.94*pi or abs(sweep)*radius < total*.86:
        return None
    # Near a half turn, make the two endpoints an exact diameter.
    if abs(abs(degrees(sweep))-180) <= 8:
        sign = 1 if sweep > 0 else -1
        mid = ((a[0]+end[0]+sign*(end[1]-a[1]))/2,
               (a[1]+end[1]-sign*(end[0]-a[0]))/2)
    return "Arc", (a, mid, end)


def oval_fit(path):
    pts = path[:-1]
    cx = sum(p[0] for p in pts)/len(pts)
    cy = sum(p[1] for p in pts)/len(pts)
    xx = sum((x-cx)**2 for x, y in pts)
    yy = sum((y-cy)**2 for x, y in pts)
    xy = sum((x-cx)*(y-cy) for x, y in pts)
    angle = atan2(2*xy, xx-yy)/2
    if abs(sin(2*angle)) < .06:
        angle = 0.
    c, s = cos(angle), sin(angle)
    uv = [(c*x+s*y, -s*x+c*y) for x, y in pts]
    us, vs = zip(*uv)
    u, v = (max(us)+min(us))/2, (max(vs)+min(vs))/2
    rx, ry = (max(us)-min(us))/2, (max(vs)-min(vs))/2
    if min(rx, ry) < 3:
        return None
    errors = [abs(hypot((x-u)/rx, (y-v)/ry)-1) for x, y in uv]
    if max(errors) > .32 or sum(errors)/len(errors) > .107:
        return None
    x, y = c*u-s*v, s*u+c*v
    # Prefer the simpler circular model for ambiguous hand-drawn loops. Work
    # in normalized distances so zoom and tablet resolution do not change intent.
    # Local residual changes estimate wobble without treating a smooth oval's
    # systematic stretch as noise.
    residuals = [hypot((a-u)/rx, (b-v)/ry)-1 for a, b in uv]
    wobble = sum(abs(a-b) for a, b in zip(residuals, residuals[1:]+residuals[:1]))/len(residuals)
    radius = sum(hypot(a-u, b-v) for a, b in uv)/len(uv)
    circle_error = sum(abs(hypot(a-u, b-v)-radius) for a, b in uv)/len(uv)/radius
    oval_error = sum(errors)/len(errors)
    ratio = min(rx, ry)/max(rx, ry)
    allowance = .065 + min(.025, wobble*.7)
    circle = ratio >= .76 and circle_error <= oval_error + allowance
    if circle:
        rx = ry = radius
        c, s = 1., 0.
    return ("Circle" if circle else "Oval"), ((x,y),(x+c*rx,y+s*rx),(x-s*ry,y+c*ry))


def sharp_corner(path, p):
    i = min(range(len(path)), key=lambda j: distance(path[j], p))
    if i < 2 or i > len(path)-3:
        return False
    def turn(span):
        a, b = path[i-span], path[i+span]
        ax, ay, bx, by = p[0]-a[0], p[1]-a[1], b[0]-p[0], b[1]-p[1]
        return abs(atan2(ax*by-ay*bx, ax*bx+ay*by))
    # A corner makes most of its turn right at the vertex. A wave distributes
    # the same turn over a longer stretch of ink.
    near, wide = turn(2), turn(min(7,i,len(path)-1-i))
    return near > .39 and near > .65*wide


def fit_shape(points):
    if len(points) < 2:
        return None
    xs, ys = zip(*points)
    scale = hypot(max(xs)-min(xs), max(ys)-min(ys))
    if scale < 12:
        return None
    stops = lengths(points)
    total = stops[-1]
    path = [sample(points, stops, total*i/96) for i in range(97)]
    a, end = points[0], points[-1]
    chord = distance(a, end)
    if chord >= .74*scale and total <= 1.35*chord:
        deviation = max(segment_error(p,a,end) for p in path)
        if deviation < max(2.1, .076*scale):
            if len(points) >= 8 and deviation > max(2.5,.032*scale):
                arc = arc_fit(path,scale,total)
                if arc:
                    return arc
            return "Line", (a, end)
    if chord <= .19*scale and total > 1.7*scale:
        far = max(range(len(path)), key=lambda i: distance(a, path[i]))
        loop = path[far:]+path[:far]+[path[far]]
        across = max(range(len(loop)), key=lambda i: distance(loop[0], loop[i]))
        tol = max(2., scale*.042)
        corners = simplify(loop[:across+1], tol)[:-1] + simplify(loop[across:], tol)[:-1]
        if 3 <= len(corners) <= 4 and min(distance(p, corners[i-1]) for i,p in enumerate(corners)) > .13*scale:
            if len(corners) == 3:
                return "Triangle", corners
            # Keep trapezoids and other four-sided strokes faithful to the artist's corners.
            right = True
            for i, (x,y) in enumerate(corners):
                l, r = corners[i-1], corners[(i+1)%4]
                cosine = ((l[0]-x)*(r[0]-x)+(l[1]-y)*(r[1]-y))/(distance(l,(x,y))*distance(r,(x,y)))
                right &= abs(cosine) < .18
            if right:
                i = max(range(4), key=lambda j: distance(corners[j-1],corners[j]))
                p, q = corners[i-1], corners[i]
                angle = atan2(q[1]-p[1], q[0]-p[0])
                c,s = cos(angle),sin(angle)
                uv = [(c*x+s*y,-s*x+c*y) for x,y in points]
                rect = axis_shape(uv)
                if rect is not uv and len(rect)==5:
                    return "Rectangle", [(c*x-s*y,s*x+c*y) for x,y in rect[:-1]]
            return "Quadrilateral", corners
        oval = oval_fit(path)
        if oval:
            return oval
        return None
    coarse = simplify(path, max(2.2, min(scale*.032, 8.)))
    if 2 < len(coarse) <= 12 and all(sharp_corner(path,p) for p in coarse[1:-1]):
        return "Polyline", coarse
    arc = arc_fit(path, scale, total)
    if arc:
        return arc
    if len(points) >= 4 and total > 24:
        # Keep the stroke's inflections and remove small positional jitter.
        knots = simplify(path, max(1.7, min(6., scale*.019)))
        if 3 <= len(knots) <= 28:
            return "Curve", knots
    return None

from math import hypot, sqrt, pi, cos, sin


def recognize(points):
    """Return a snapped polyline or the original stroke."""
    if len(points) < 2:
        return points
    xs, ys = zip(*points)
    l, r, t, b = min(xs), max(xs), min(ys), max(ys)
    w, h = r-l, b-t
    scale = hypot(w, h)
    if scale < 12:
        return points
    x, y = points[0]
    dx, dy = points[-1][0]-x, points[-1][1]-y
    chord = hypot(dx, dy)
    length = sum(hypot(i-k, j-m) for (i,j),(k,m) in zip(points, points[1:]))
    if chord > scale*.72 and length < chord*1.45:
        if max(abs((i-x)*dy-(j-y)*dx) for i,j in points) < chord*scale*.13:
            return [points[0], points[-1]]
    if chord > scale*.25 or min(w,h) < 8 or len(points)<5:
        return points
    uv = [((i-l)/w,(j-t)/h) for i,j in points]
    if all(min(hypot(u-a,v-bb) for u,v in uv)<.16 for a,bb in [(0,0),(1,0),(1,1),(0,1)]) and max(min(u,v,1-u,1-v) for u,v in uv)<.17:
        return [(l,t),(r,t),(r,b),(l,b),(l,t)]
    radii = [hypot(2*u-1,2*v-1) for u,v in uv]
    area = abs(sum(u*z-v*q for (u,v),(q,z) in zip(uv,uv[1:]+uv[:1])))/2
    if .53 < area < .94 and max(abs(z-1) for z in radii)<.34:
        if min(w,h)/max(w,h)>.9:
            w=h=(w+h)/2
        return [((l+r+w*cos(k*pi/64))/2,(t+b+h*sin(k*pi/64))/2) for k in range(129)]
    return points

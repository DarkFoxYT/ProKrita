"""Geometry used by both the held pen and the on-canvas edit handles."""
from dataclasses import dataclass
from math import atan2, degrees, hypot, sqrt

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QPainterPath, QPainterPathStroker, QTransform

from .shapes import circumcircle, fit_shape


def length(vector):
    return hypot(vector.x(), vector.y())


def dot(first, second):
    return first.x() * second.x() + first.y() * second.y()


def perpendicular(vector):
    return QPointF(-vector.y(), vector.x())


@dataclass(frozen=True)
class Shape:
    # Ellipses use three controls: center, end of horizontal axis, end of vertical axis.
    # Polygons store each vertex exactly once.
    kind: str
    controls: tuple

    @classmethod
    def fit(cls, samples):
        # Bound recognition time even after a very long, high-frequency tablet stroke.
        if len(samples) > 1024:
            end = len(samples) - 1
            samples = [samples[round(i * end / 1023)] for i in range(1024)]
        fit = fit_shape(samples)
        if fit:
            kind, points = fit
            return cls(kind, tuple(QPointF(x, y) for x, y in points))
        return None

    @property
    def elliptic(self):
        return self.kind in ("Circle", "Oval")

    @property
    def closed(self):
        return self.elliptic or self.kind in ("Triangle", "Equilateral triangle", "Rectangle", "Square", "Quadrilateral")

    def variants(self):
        if self.elliptic:
            return ("Circle","Oval")
        if self.kind in ("Triangle","Equilateral triangle"):
            return ("Equilateral triangle","Triangle")
        if self.kind in ("Quadrilateral","Rectangle","Square"):
            return ("Square","Rectangle","Quadrilateral")
        if self.kind == "Arc":
            return ("Semicircle",)
        return ()

    def refined(self, kind):
        if kind not in self.variants() or kind == self.kind:
            return self
        points = self.controls
        center = self.center()
        if kind in ("Oval","Triangle","Quadrilateral"):
            return Shape(kind,points)
        if kind == "Circle":
            center,a,b = points
            radius = (length(a-center)+length(b-center))/2
            return Shape(kind,(center, center+(a-center)*(radius/max(.01,length(a-center))),
                               center+(b-center)*(radius/max(.01,length(b-center)))))
        if kind == "Semicircle":
            a,mid,end = points
            center = (a+end)*.5
            normal = perpendicular(end-a)*.5
            return Shape("Arc",(a,center+normal*(1 if dot(mid-center,normal)>=0 else -1),end))
        if kind == "Equilateral triangle":
            first = points[0]-center
            radius = sum(length(p-center) for p in points)/3
            first = first*(radius/max(.01,length(first)))
            normal = perpendicular(first)*(sqrt(3)/2)
            if dot(normal, points[1]-center)<0:
                normal = -normal
            return Shape(kind,(center+first,center-first*.5+normal,center-first*.5-normal))
        axis = (points[1]-points[0])+(points[2]-points[3])
        axis = axis/max(.01,length(axis))
        other = perpendicular(axis)
        if dot(other,points[2]-points[1])<0:
            other = -other
        w = sum(abs(dot(p-center,axis)) for p in points)/4
        h = sum(abs(dot(p-center,other)) for p in points)/4
        if kind == "Square":
            w = h = (w+h)/2
        return Shape(kind,(center-axis*w-other*h,center+axis*w-other*h,
                           center+axis*w+other*h,center-axis*w+other*h))

    def center(self):
        if self.elliptic:
            return self.controls[0]
        return sum(self.controls, QPointF()) / len(self.controls)

    def mapped(self, transform):
        return Shape(self.kind, tuple(transform.map(p) for p in self.controls))

    def path(self):
        if self.elliptic:
            center, east, south = self.controls
            x_axis, y_axis = east - center, south - center
            ellipse = QPainterPath()
            ellipse.addEllipse(-1, -1, 2, 2)
            frame = QTransform(x_axis.x(), x_axis.y(), y_axis.x(), y_axis.y(),
                               center.x(), center.y())
            return frame.map(ellipse)
        outline = QPainterPath(self.controls[0])
        if self.kind == "Arc":
            a, mid, end = self.controls
            c = circumcircle(*[(p.x(), p.y()) for p in self.controls])
            if c:
                cx, cy = c
                radius = length(a-QPointF(cx,cy))
                def angle(p):
                    return degrees(atan2(cy-p.y(), p.x()-cx))
                start = angle(a)
                sweep = (angle(end)-start) % 360
                if (angle(mid)-start) % 360 > sweep:
                    sweep -= 360
                outline.arcTo(cx-radius, cy-radius, 2*radius, 2*radius, start, sweep)
                return outline
        if self.kind == "Curve":
            pts = self.controls
            for i, (a,b) in enumerate(zip(pts,pts[1:])):
                before, after = pts[max(0,i-1)], pts[min(i+2,len(pts)-1)]
                # Distance-weighted tangents avoid loops next to tightly spaced knots.
                chord = length(b-a)
                u = (b-before)*(chord/(3*max(.001, length(a-before)+chord)))
                v = (after-a)*(chord/(3*max(.001, length(after-b)+chord)))
                outline.cubicTo(a+u, b-v, b)
            return outline
        for point in self.controls[1:]:
            outline.lineTo(point)
        if self.closed:
            outline.closeSubpath()
        return outline

    def handles(self):
        if self.elliptic:
            center, east, south = self.controls
            return (east, south, center * 2 - east, center * 2 - south)
        return self.controls

    def hit(self, point):
        # Hit testing is in logical screen pixels, independent of the canvas zoom.
        handles = self.handles()
        nearest = min(range(len(handles)), key=lambda i: length(handles[i] - point))
        if length(handles[nearest] - point) <= 11:
            return nearest
        if length(point - self.center()) <= 12:
            return "move"
        outline = self.path()
        edge = QPainterPathStroker()
        edge.setWidth(16)
        edge.setCapStyle(Qt.RoundCap)
        if edge.createStroke(outline).contains(point) or (self.closed and outline.contains(point)):
            return "move"
        return None

    def held(self, anchor, point):
        if self.kind == "Line":
            return Shape("Line", (self.controls[0], point))
        # A similarity transform about the original center keeps circles round and
        # right angles square. Always start from the locked fit, never from the last frame.
        center = self.center() if self.closed else self.controls[0]
        original, current = anchor - center, point - center
        denominator = dot(original, original)
        if denominator < 1 or length(current) < 2:
            return self
        real = dot(current, original) / denominator
        imag = dot(current, perpendicular(original)) / denominator
        offset = center - (center * real + perpendicular(center) * imag)
        return self.mapped(QTransform(real, imag, -imag, real, offset.x(), offset.y()))

    def dragged(self, handle, delta):
        if handle == "move":
            return Shape(self.kind, tuple(point + delta for point in self.controls))
        point = self.handles()[handle] + delta
        if self.elliptic:
            center, east, south = self.controls
            axis = (point - center) * (1 if handle < 2 else -1)
            radius = length(axis)
            if radius < 2:
                return self
            # Retain handedness even when the canvas is mirrored.
            handedness = 1 if dot(perpendicular(east - center), south - center) >= 0 else -1
            if handle % 2 == 0:
                other = radius if self.kind == "Circle" else length(south - center)
                east, south = center + axis, center + perpendicular(axis) * (handedness * other / radius)
            else:
                other = radius if self.kind == "Circle" else length(east - center)
                east, south = center - perpendicular(axis) * (handedness * other / radius), center + axis
            return Shape(self.kind, (center, east, south))
        vertices = list(self.controls)
        if self.kind == "Equilateral triangle":
            return self.held(vertices[handle], point)
        if self.kind in ("Rectangle","Square"):
            # The opposite corner stays planted; project onto the original two edges.
            opposite = vertices[(handle + 2) % 4]
            diagonal = point - opposite
            for neighbor in ((handle + 1) % 4, (handle - 1) % 4):
                edge = self.controls[neighbor] - opposite
                size = length(edge)
                if size < 0.001:
                    return self
                unit = edge / size
                extent = dot(diagonal, unit)
                if self.kind == "Square":
                    extent = (1 if extent >= 0 else -1)*length(diagonal)/sqrt(2)
                if abs(extent) < 2:
                    return self
                vertices[neighbor] = opposite + unit * extent
            if self.kind == "Square":
                point = vertices[(handle+1)%4]+vertices[(handle-1)%4]-opposite
        vertices[handle] = point
        return Shape(self.kind, tuple(vertices))

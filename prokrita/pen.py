"""Reapply the artist's tablet input along a fitted path through the active brush."""
from bisect import bisect_right
from dataclasses import dataclass
from math import ceil
import sys
from time import monotonic

from PyQt5.QtCore import QEvent, QPointF, Qt
from PyQt5.QtGui import QMouseEvent, QTabletEvent
from PyQt5.QtWidgets import QApplication

from .shapes import lengths, sample


def axes(event):
    return (event.pressure(), event.xTilt(), event.yTilt(), event.tangentialPressure(),
            event.rotation(), event.z(), event.timestamp() or round(monotonic()*1000))


def on_path(path, size, fraction):
    return path.pointAtPercent(path.percentAtLength(size*max(0., min(1., fraction))))


@dataclass
class PenProfile:
    device: tuple
    stops: list
    axes: list
    phase: float
    direction: int

    @classmethod
    def capture(cls, stroke):
        if not stroke.tablet or not stroke.pressures:
            return None
        points = stroke.samples
        stops = lengths(points)
        total = stops[-1]
        if total < 1:
            return None
        phase, direction = 0., 1
        if stroke.locked.closed:
            path = stroke.locked.path()
            size = path.length()
            start = QPointF(*points[0])
            phase = min(range(180), key=lambda k: (on_path(path,size,k/180)-start).manhattanLength())/180
            def error(sign):
                result = 0.
                for t in (.06,.17,.32,.52,.73):
                    drawn = QPointF(*sample(points,stops,t*total))
                    result += (on_path(path,size,(phase+sign*t) % 1)-drawn).manhattanLength()
                return result
            direction = min((1,-1), key=error)
        return cls(stroke.device, [d/total for d in stops], stroke.pressures, phase, direction)

    def values(self, t):
        i = min(len(self.stops)-2, max(0,bisect_right(self.stops,t)-1))
        weight = (t-self.stops[i])/max(1e-9,self.stops[i+1]-self.stops[i])
        a, b = self.axes[i:i+2]
        v = [x+(y-x)*weight for x,y in zip(a,b)]
        v[4] = (a[4]+((b[4]-a[4]+180) % 360-180)*weight) % 360
        return v

    def replay(self, canvas, shape, return_to_mouse=False):
        path = shape.path()
        size = path.length()
        count = max(8, min(2400, ceil(size/2.5)))
        # Include the actual pressure extrema between regular geometric samples.
        times = {i/count for i in range(count+1)}
        stride = max(1, ceil(len(self.stops)/1900))
        times.update(self.stops[::stride])
        first_stamp = self.axes[0][-1]
        start_stamp = max(1, round(monotonic()*1000) - max(0, self.axes[-1][-1]-first_stamp))
        device, pointer, unique_id = self.device
        def send(kind, pos, v, button, buttons):
            origin = QPointF(canvas.mapToGlobal(canvas.rect().topLeft()))
            e = QTabletEvent(kind, pos, origin+pos, device, pointer, v[0], round(v[1]),
                             round(v[2]), v[3], v[4], round(v[5]), Qt.NoModifier,
                             unique_id, button, buttons)
            e.setTimestamp(max(1, round(start_stamp + v[-1] - first_stamp)))
            QApplication.sendEvent(canvas, e)
        for t in sorted(times):
            f = (self.phase+self.direction*t) % 1 if shape.closed else t
            pos, v = on_path(path,size,f), self.values(t)
            first = t == 0
            send(QEvent.TabletPress if first else QEvent.TabletMove, pos, v,
                 Qt.LeftButton if first else Qt.NoButton, Qt.LeftButton)
            if first and sys.platform == "win32":
                # WinTab normally echoes a mouse press. Krita reserves and consumes
                # one such press per pen stroke, including an in-process tablet stroke.
                g = QPointF(canvas.mapToGlobal(canvas.rect().topLeft())) + pos
                win = QPointF(canvas.mapTo(canvas.window(),canvas.rect().topLeft())) + pos
                for kind, buttons in ((QEvent.MouseButtonPress,Qt.LeftButton),(QEvent.MouseButtonRelease,Qt.NoButton)):
                    echo = QMouseEvent(kind,pos,win,g,Qt.LeftButton,buttons,Qt.NoModifier,
                                       Qt.MouseEventSynthesizedBySystem)
                    QApplication.sendEvent(canvas,echo)
        v[0] = 0.
        send(QEvent.TabletRelease, pos, v, Qt.LeftButton, Qt.NoButton)
        if return_to_mouse:
            event = QTabletEvent(QEvent.TabletLeaveProximity,pos,pos,device,pointer,0.,0,0,0.,0.,0,
                                 Qt.NoModifier,unique_id,Qt.NoButton,Qt.NoButton)
            QApplication.sendEvent(QApplication.instance(),event)

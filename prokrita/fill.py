"""ColorDrop options and a read-only, canvas-sized preview of its connected region."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from math import sqrt
import re

from krita import Krita, Selection
from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QImage, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from .canvas_ui import FILL

K = Krita.instance()
GROUP = "ProKrita"
RUN = re.compile(b"\xff+")
PREVIEW_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ProKrita color preview")


def number(group, key, default, low, high):
    try:
        return max(low, min(high, int(K.readSetting(group, key, str(default)))))
    except ValueError:
        return default


def enabled(key, default):
    return K.readSetting(GROUP, key, str(default)).lower() == "true"


def threshold():
    return number(GROUP, "threshold", number(FILL, "thresholdAmount", 8, 1, 100), 1, 100)


def reference():
    if enabled("visibleInk", False):
        return "allLayers"
    legacy = K.readSetting(FILL, "sampleMerged", "false").lower() == "true"
    return K.readSetting(FILL, "sampleLayersMode", "") or ("allLayers" if legacy else "currentLayer")


def transparent_at(node, x, y):
    if not node.bounds().contains(x,y):
        return True
    if node.colorModel() not in ("RGBA", "GRAYA", "CMYKA", "LABA", "XYZA", "YCbCrA"):
        return False
    size = {"U8":1, "U16":2, "F16":2, "F32":4}.get(node.colorDepth())
    return size and not any(bytes(node.pixelData(x,y,1,1))[-size:])


@contextmanager
def drop_settings(view, node, seed, value):
    # KisView copies these options into its fill job synchronously at the drop.
    options = {"thresholdAmount": (value, 8), "contiguousFillMode": ("floodFill", "")}
    if enabled("visibleInk", False):
        options["sampleLayersMode"] = ("allLayers", "")
    if enabled("sealEdges", True):
        grow = number(FILL, "growSelection", 0, -400, 400)
        gap = number(FILL, "closeGapAmount", 0, 0, 32)
        options.update(growSelection=(max(1,grow),0), closeGapAmount=(max(2,gap),0),
                       antiAlias=("true","true"), stopGrowingAtDarkestPixel=("true","false"))
        custom = K.readSetting(FILL,"useCustomBlendingOptions","false").lower() == "true"
        if not custom and view.currentBlendingMode() == "normal" and transparent_at(node, int(seed.x()),int(seed.y())):
            options.update(useCustomBlendingOptions=("true","false"), customCompositeOp=("behind","normal"),
                           customOpacity=(round(100*view.paintingOpacity()),100))
    previous = {}
    try:
        for key, (new, default) in options.items():
            old = K.readSetting(FILL, key, str(default))
            if old != str(new):
                previous[key] = old
                K.writeSetting(FILL, key, str(new))
        yield
    finally:
        for key, old in previous.items():
            K.writeSetting(FILL, key, old)


def reference_image(doc, node, w, h):
    if reference() != "currentLayer":
        img = doc.projection(0,0,w,h) if (w,h) == (doc.width(),doc.height()) else doc.thumbnail(w,h)
        return img.scaled(w,h).convertToFormat(QImage.Format_RGBA8888)
    # Node thumbnails are local to the tight bounds, which can extend off the page.
    image = QImage(w,h,QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    bounds = node.bounds()
    if bounds.isEmpty():
        return image
    if ((w,h) == (doc.width(),doc.height()) and node.colorModel()=="RGBA" and node.colorDepth()=="U8"
            and "srgbtrc" in node.colorProfile().lower()):
        raw = bytes(node.pixelData(0,0,w,h))
        return QImage(raw,w,h,4*w,QImage.Format_ARGB32).convertToFormat(QImage.Format_RGBA8888)
    sx, sy = w/doc.width(), h/doc.height()
    area = QRectF(bounds.x()*sx,bounds.y()*sy,bounds.width()*sx,bounds.height()*sy)
    thumb = node.thumbnail(max(1,min(1024,round(area.width()))), max(1,min(1024,round(area.height()))))
    painter = QPainter(image)
    painter.drawImage(area, thumb)
    painter.end()
    return image


def selection_mask(doc, w, h):
    sel = doc.selection()
    if not sel:
        return None
    dw, dh = doc.width(), doc.height()
    if (dw,dh) == (w,h):
        return bytes(sel.pixelData(0,0,w,h))
    # Only read the sampled scanlines. A poster-sized selection stays poster-sized in Krita.
    cols = [min(dw-1, int((x+.5)*dw/w)) for x in range(w)]
    result = bytearray()
    for y in range(h):
        row = bytes(sel.pixelData(0, min(dh-1,int((y+.5)*dh/h)), dw, 1))
        result.extend(row[x] for x in cols)
    return bytes(result)


LINEAR = tuple(((v/255+.055)/1.055)**2.4 if v > 10 else v/3294.6 for v in range(256))


def lab(rgb):
    r,g,b = (LINEAR[v] for v in rgb)
    # sRGB to D50 Lab, the same metric used by Krita's color-managed flood fill.
    xyz = ((.43607*r+.38506*g+.14308*b)/.96422, .22250*r+.71688*g+.06062*b,
           (.01393*r+.09710*g+.71417*b)/.82521)
    x,y,z = (v**(1/3) if v > .008856 else 7.787*v+16/116 for v in xyz)
    return 116*y-16,500*(x-y),200*(y-z)


def differences(raw, index):
    rgba = memoryview(raw).cast("I")
    seed = bytes(raw[4*index:4*index+4])
    alpha = seed[3]
    if not alpha:
        return raw[3::4].translate(bytes(max(2,round(a*100/255)) if a else 0 for a in range(256)))
    base = lab(seed[:3])
    cache = {}
    def difference(p):
        a = p >> 24
        if not a:
            return round(alpha*100/255)
        vals = lab((p & 255, p >> 8 & 255, p >> 16 & 255))
        val = sqrt(sum((x-y)**2 for x,y in zip(base,vals)) + ((a-alpha)*100/255)**2)
        # Krita uses exact color matching at the first threshold setting.
        return min(255, max(0 if p == rgba[index] else 2, int(val)))
    for p in set(rgba):
        cache[p] = difference(p)
    return bytes(map(cache.__getitem__, rgba))


def connected(allowed, w, h, seed):
    result = bytearray(w*h)
    if not allowed[seed]:
        return result
    queue = deque([seed])
    while queue:
        i = queue.pop()
        if not allowed[i]:
            continue
        row = (i//w)*w
        left = allowed.rfind(0, row,i)+1
        left = max(left,row)
        right = allowed.find(0, i,row+w)
        if right < 0:
            right = row+w
        allowed[left:right] = b"\x00"*(right-left)
        result[left:right] = b"\xff"*(right-left)
        for adjacent in (row-w,row+w):
            if 0 <= adjacent < w*h:
                shift = adjacent-row
                queue.extend(m.start() for m in RUN.finditer(allowed,left+shift,right+shift))
    return result


def resized_mask(raw, w, h, radius, grow):
    if radius <= 0:
        return raw
    mask = Selection()
    mask.setPixelData(bytes(raw),0,0,w,h)
    if grow:
        mask.grow(radius,radius)
    else:
        mask.shrink(radius,radius,True)
    return bytearray(mask.pixelData(0,0,w,h))


class FillPreview(QWidget):
    def __init__(self, canvas, target, seed, transform, color, opacity):
        super().__init__(canvas)
        self.setObjectName("prokrita_fill_preview")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(canvas.rect())
        self.seed = QPointF(seed)
        self.transform = transform
        doc = target.document
        self.page = QRectF(0,0,doc.width(),doc.height())
        scale = min(1., 512/max(doc.width(),doc.height()))
        self.w, self.h = max(1,round(doc.width()*scale)), max(1,round(doc.height()*scale))
        point = transform.map(self.seed)
        self.index = (min(self.h-1,int(point.y()*self.h/doc.height()))*self.w +
                      min(self.w-1,int(point.x()*self.w/doc.width())))
        image = reference_image(doc, target.active_layer(), self.w, self.h)
        raw = image.constBits().asstring(image.byteCount())
        self.diff = None
        self.future = None
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.take_differences)
        if raw[4*self.index+3]:
            # Only immutable display pixels go to the worker. Brush input and a quick
            # release remain responsive during color matching on a detailed painting.
            self.future = PREVIEW_WORKER.submit(differences,raw,self.index)
            self.poll.start(18)
        else:
            self.diff = differences(raw,self.index)
        self.selection = selection_mask(doc,self.w,self.h)
        self.selection_boundary = K.readSetting(FILL,"useSelectionAsBoundary","false").lower()=="true"
        gap = number(FILL,"closeGapAmount",0,0,32)
        grow = number(FILL,"growSelection",0,-400,400)
        seal = enabled("sealEdges",True)
        self.gap = round(max(gap,2 if seal else 0)*scale/2)
        self.grow = round(max(grow,1 if seal else grow)*scale)
        self.palette = [QColor(color.red(),color.green(),color.blue(),round(i*.78*opacity*color.alphaF())).rgba()
                        for i in range(256)]
        self.mask = QImage()
        self.value = None
        self.show()
        self.raise_()

    def take_differences(self):
        if self.future.done():
            self.diff = self.future.result()
            self.poll.stop()
            value, self.value = self.value, None
            if value is not None:
                self.set_threshold(value)

    def hideEvent(self,event):
        self.poll.stop()
        if self.future is not None:
            self.future.cancel()
        super().hideEvent(event)

    def set_threshold(self, value):
        if self.value == value:
            return
        self.value = value
        if self.diff is None:
            self.update()
            return
        w,h = self.w,self.h
        allowed = bytearray(self.diff.translate(bytes(255 if i <= value else 0 for i in range(256))))
        if self.selection_boundary and self.selection:
            allowed = bytearray(a if b else 0 for a,b in zip(allowed,self.selection))
        inner = resized_mask(allowed,w,h,self.gap,False)
        # If the seed lies directly against a boundary, keep its original component.
        use_gap = self.gap if inner[self.index] else 0
        region = connected(inner if use_gap else allowed,w,h,self.index)
        region = resized_mask(region,w,h,use_gap,True)
        region = resized_mask(region,w,h,abs(self.grow),self.grow>=0)
        if self.selection is not None:
            region = bytes(map(min, region, self.selection))
        self.mask = QImage(bytes(region), w,h,w,QImage.Format_Indexed8).copy()
        self.mask.setColorTable(self.palette)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setWorldTransform(self.transform.inverted()[0])
        painter.drawImage(self.page,self.mask)
        painter.resetTransform()
        painter.setRenderHint(QPainter.Antialiasing)
        x,y = self.seed.x(), self.seed.y()
        box = QRectF(max(3,min(x-56,self.width()-115)), max(3,min(y-52,self.height()-34)),112,30)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(26,26,30,235))
        painter.drawRoundedRect(box,12,12)
        painter.setPen(Qt.white)
        painter.drawText(box,Qt.AlignCenter,"‹   Fill  {}%   ›".format(self.value))
        for color,width in ((Qt.black,3),(Qt.white,1.4)):
            painter.setPen(QPen(color,width))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self.seed,5,5)

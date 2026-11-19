"""Make an antialiased, undoable Krita selection from fitted image geometry."""
from krita import Selection
from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QImage, QPainter


def select_shape(document, shape, mode):
    if not shape.closed:
        return
    path = shape.path()
    bounds = path.boundingRect().adjusted(-1,-1,1,1).toAlignedRect()
    bounds = bounds.intersected(QRect(0,0,document.width(),document.height()))
    if bounds.isEmpty():
        return
    mask = Selection()
    x,w = bounds.x(),bounds.width()
    strip = max(1,min(512,1048576//w))
    for y in range(bounds.top(), bounds.bottom()+1, strip):
        h = min(strip,bounds.bottom()-y+1)
        image = QImage(w,h,QImage.Format_Grayscale8)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(-x,-y)
        painter.fillPath(path,Qt.white)
        painter.end()
        data = image.constBits().asstring(image.byteCount())
        stride = image.bytesPerLine()
        if stride != w:
            data = b"".join(data[i:i+w] for i in range(0,len(data),stride))
        mask.setPixelData(data,x,y,w,h)
    prior = document.selection()
    if mode != "Replace" and prior:
        merged = prior.duplicate()
        if mode == "Add":
            merged.add(mask)
        else:
            merged.subtract(mask)
        mask = merged
    elif mode == "Subtract":
        return
    document.setSelection(mask)

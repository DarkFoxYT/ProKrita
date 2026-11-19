from time import monotonic
from xml.etree import ElementTree

from PyQt5 import sip
from PyQt5.QtCore import (QByteArray, QDataStream, QEvent, QIODevice, QMimeData,
                          QPoint, QPointF, QRectF, Qt, QTimer)
from PyQt5.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPen
from PyQt5.QtWidgets import QActionGroup, QApplication, QComboBox, QMenu, QMdiArea, QToolButton, QWidget
from krita import Krita

from .shape_edit import length
from .shading import ShadingPalette
from .color_picker import ColorPicker


K = Krita.instance()
CANVAS_CLASSES = ("KisOpenGLCanvas2", "KisQPainterCanvas")
FILL = "KritaFill/KisToolFill"
BRUSH = "KritaShape/KisToolBrush"
_last_canvas = (None, None)


def smoothing_combo():
    window = K.activeWindow()
    panel = window.qwindow().findChild(QWidget, BRUSH+"option widget") if window else None
    if panel:
        # Krita's brush-options combo lists None, Basic, Weighted, Stabilizer and Pixel Perfect.
        return next((combo for combo in panel.findChildren(QComboBox) if combo.count()==5),None)
    return None


def canvas_widget(window):
    global _last_canvas
    if not window:
        return None
    area = window.qwindow().findChild(QMdiArea)
    # activeSubWindow() can be null while a toolbar/preset popup has focus.
    # currentSubWindow() keeps the document that owns the visible canvas.
    sub = area.currentSubWindow() if area else None
    if sub:
        owner, canvas = _last_canvas
        if owner is sub and canvas is not None and not sip.isdeleted(canvas):
            if canvas.isVisible() and sub.isAncestorOf(canvas):
                return canvas
        for widget in sub.findChildren(QWidget):
            if widget.metaObject().className() in CANVAS_CLASSES and widget.isVisible():
                _last_canvas = (sub, widget)
                return widget
    _last_canvas = (None, None)
    return None


def canvas_to_image(view):
    inverse, valid = view.flakeToCanvasTransform().inverted()
    if valid:
        return inverse * view.flakeToImageTransform()
    return None


def swatch_mime(color):
    # Use the same stream as KisSwatch::writeToStream. The canvas displays a
    # monitor-converted QColor, but the drop must carry the document/profile color.
    xml = ElementTree.fromstring(color.toXML())
    xml[0].set("bitdepth", color.colorDepth())  # KisSwatch reads depth from the color element.
    payload = QByteArray()
    stream = QDataStream(payload, QIODevice.WriteOnly)
    stream.writeQString("Foreground")
    stream.writeQString("")
    stream.writeBool(False)
    stream.writeInt32(-1)
    stream.writeInt32(-1)
    stream.writeQString("")
    stream.writeQString(ElementTree.tostring(xml, encoding="unicode"))
    mime = QMimeData()
    mime.setData("krita/x-colorsetentry", payload)
    return mime


def drop_on_canvas(canvas, position, color):
    """Enter and drop through Krita's own color-drop handler. No tool change."""
    native_view = canvas
    while native_view and not native_view.inherits("KisView"):
        native_view = native_view.parentWidget()
    if not native_view:
        return False
    # The native handler interprets its seed in canvas-widget coordinates.
    # Alt requests contiguous fill if the bucket is set to selection or all similar.
    fill_mode = K.readSetting(FILL, "whatToFill", "")
    if not fill_mode:
        fill_mode = ("fillSelection" if K.readSetting(FILL, "fillSelection", "false").lower() == "true"
                     else "fillContiguousRegion")
    modifiers = Qt.NoModifier if fill_mode == "fillContiguousRegion" else Qt.AltModifier
    mime = swatch_mime(color)
    enter = QDragEnterEvent(position, Qt.CopyAction, mime, Qt.LeftButton, modifiers)
    QApplication.sendEvent(native_view, enter)
    if enter.isAccepted():
        event = QDropEvent(QPointF(position), Qt.CopyAction, mime, Qt.NoButton, modifiers)
        QApplication.sendEvent(native_view, event)
        # KisView schedules the color fill after accepting drag-enter. Its color
        # branch does not set the accepted flag on the final QDropEvent.
        return True
    return False


class DropMarker(QWidget):
    def __init__(self, canvas, color):
        super().__init__(canvas)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.color = color
        self.resize(32, 48)

    def track(self, point):
        self.move(point - QPoint(16, 39))
        if not self.isVisible():
            self.show()
            self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for width, color in ((3, Qt.black), (1, Qt.white)):
            painter.setPen(QPen(color, width))
            painter.drawLine(11, 39, 21, 39)
            painter.drawLine(16, 34, 16, 44)
        painter.setPen(QPen(QColor(0, 0, 0, 190), 3))
        painter.setBrush(self.color)
        painter.drawEllipse(QRectF(3, 3, 26, 26))
        painter.setPen(QPen(Qt.white, 1.3))
        painter.drawEllipse(QRectF(3.5, 3.5, 25, 25))


class SmudgeButton(QToolButton):
    def __init__(self, parent, toggle):
        super().__init__(parent)
        self.toggle_smudge = toggle
        self.active = False
        self.started = 0.
        self.setObjectName("prokrita_quick_smudge")
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_TabletTracking)
        self.setCheckable(True)
        self.setFixedSize(max(67, self.fontMetrics().horizontalAdvance("Keep on") + 18), 32)
        self.setStyleSheet(
            "QToolButton { background: palette(window); color: palette(window-text);"
            " border: 1px solid palette(mid); border-radius: 15px; }"
            "QToolButton:hover { border-color: palette(highlight); }"
            "QToolButton:checked { background: palette(highlight); color: palette(highlighted-text); }")
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.setTimerType(Qt.PreciseTimer)
        self.hold.timeout.connect(lambda: self.setText("Keep on") if self.isDown() else None)
        self.pressed.connect(self.start_hold)
        self.released.connect(self.stop_hold)
        self.clicked.connect(self.activate)
        self.set_active(False)
        self.show()

    def set_active(self, active):
        self.active = active
        self.setChecked(active)
        self.stop_hold()
        self.setAccessibleName("Return to drawing brush" if active else "Quick smudge")
        self.setToolTip("Tap to return to your drawing brush" if active else
                        "Tap for one smudge stroke. Hold until Keep on appears for several strokes.")

    def start_hold(self):
        self.started = monotonic()
        if not self.active:
            self.hold.start(500)

    def stop_hold(self):
        self.hold.stop()
        self.setText("Paint" if self.active else "Smudge")

    def activate(self):
        self.setChecked(self.active)
        self.toggle_smudge(monotonic() - self.started >= .5)

    def tabletEvent(self, event):
        if event.type() == QEvent.TabletPress and event.button() == Qt.LeftButton:
            self.setDown(True)
            self.start_hold()
        elif event.type() == QEvent.TabletRelease and self.isDown() and event.button() in (Qt.LeftButton, Qt.NoButton):
            self.setDown(False)
            self.stop_hold()
            if self.rect().contains(event.pos()):
                self.activate()
        elif not self.isDown():
            event.ignore()
            return
        event.accept()


class ColorSwatch(QWidget):
    def __init__(self, canvas, extension):
        super().__init__(canvas)
        self.setObjectName("prokrita_color_drop")
        self.setAccessibleName("Current color")
        self.setToolTip("Tap to pick a color. Drag to fill. Pause inside a region and slide to adjust.")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TabletTracking)
        self.setAttribute(Qt.WA_NoMousePropagation)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.resize(42, 42)
        self.color = QColor(Qt.black)
        self.picker = None
        self.brush_button = QToolButton(canvas)
        self.brush_button.setObjectName("prokrita_brush_presets")
        self.brush_button.setAccessibleName("Brush presets")
        self.brush_button.setToolTip("Choose a brush")
        self.brush_button.setIcon(K.icon("krita_tool_freehand"))
        self.brush_button.setFixedSize(32, 32)
        self.brush_button.setCursor(Qt.PointingHandCursor)
        self.brush_button.setFocusPolicy(Qt.NoFocus)
        self.brush_button.setStyleSheet(
            "QToolButton { background: palette(window); border: 1px solid palette(mid);"
            " border-radius: 16px; } QToolButton:hover { border-color: palette(highlight); }")
        self.brush_button.clicked.connect(self.choose_brush)
        self.brush_button.show()
        self.smudge_button = SmudgeButton(canvas, extension.toggle_smudge)
        self.options_button = QToolButton(canvas)
        self.options_button.setObjectName("prokrita_options")
        self.options_button.setAccessibleName("Drawing options")
        self.options_button.setText("⋯")
        self.options_button.setFixedSize(32,32)
        self.options_button.setCursor(Qt.PointingHandCursor)
        self.options_button.setFocusPolicy(Qt.NoFocus)
        self.options_button.setStyleSheet(self.brush_button.styleSheet() +
            " QToolButton::menu-indicator { image: none; }")
        self.options_button.setToolTip("Shading palette, ColorDrop, selection and pen smoothing")
        self.menu = QMenu(self.options_button)
        self.options_button.setMenu(self.menu)
        self.options_button.setPopupMode(QToolButton.InstantPopup)
        self.options_button.show()
        self.shading = ShadingPalette(canvas,extension.show_shading)
        self.shading_action = self.menu.addAction("Shading palette")
        self.shading_action.setCheckable(True)
        self.shading_action.triggered.connect(extension.show_shading)
        self.menu.addAction("Smudge visible layers on a new layer").triggered.connect(
            lambda: QTimer.singleShot(0, extension.smudge_visible))
        picker_menu = self.menu.addMenu("Color picker")
        modes = QActionGroup(picker_menu)
        modes.setExclusive(True)
        for key, label in (("native", "Krita default"), ("square", "Square + hue")):
            action = picker_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == K.readSetting("ProKrita", "colorPickerLayout", "native"))
            modes.addAction(action)
            action.triggered.connect(lambda checked, mode=key: QTimer.singleShot(
                0, lambda: self.open_color_picker(mode)))
        shade = K.action("show_mypaint_shade_selector")
        if shade:
            picker_menu.addSeparator()
            picker_menu.addAction("MyPaint Shade Selector").triggered.connect(
                lambda: QTimer.singleShot(0, lambda: self.open_color_picker("shade")))
        pen_snap = self.menu.addAction("Hold to snap with pen")
        pen_snap.setCheckable(True)
        pen_snap.setChecked(extension.pen_snap)

        def set_pen_snap(enabled):
            extension.pen_snap = enabled
            K.writeSetting("ProKrita", "penSnap", str(enabled).lower())
            extension.stop_stroke()

        pen_snap.toggled.connect(set_pen_snap)
        self.menu.addSection("ColorDrop")
        self.add_switch("Seal ink edges", "sealEdges", True)
        self.add_switch("Use visible layers as fill reference", "visibleInk", False)
        self.refine_menu = self.menu.addMenu("Refine last shape")
        self.shape_menu = self.menu.addMenu("Select inside last shape")
        for mode in ("Replace","Add","Subtract"):
            action = self.shape_menu.addAction({"Replace":"New selection","Add":"Add to selection",
                                                "Subtract":"Subtract from selection"}[mode])
            action.triggered.connect(lambda checked, mode=mode: extension.select_recent(mode))
        def refresh_shape():
            shape = extension.last_shape()
            self.shape_menu.setEnabled(bool(shape and shape.closed))
            variants = shape.variants() if shape else ()
            self.refine_menu.clear()
            self.refine_menu.setEnabled(bool(variants))
            for kind in variants:
                action = self.refine_menu.addAction(kind)
                action.setEnabled(shape.kind != kind)
                # Native brush replay and Undo need the canvas to own input again.
                action.triggered.connect(lambda checked, name=kind:
                                         QTimer.singleShot(0,lambda: extension.refine_recent(name)))
        self.menu.aboutToShow.connect(refresh_shape)
        selections = self.menu.addMenu("Selection tools")
        for key, label in (("KisToolSelectOutline","Freehand lasso"), ("KisToolSelectPolygonal","Polygon lasso"),
                           ("KisToolSelectContiguous","Contiguous color"), ("KisToolSelectSimilar","Similar color"),
                           ("invert_selection","Invert selection"), ("deselect","Deselect")):
            self.native_action(selections,key,label)
        smoothing = self.menu.addMenu("Pen smoothing")
        for index,(key,label) in enumerate((("no","None"),("simple","Basic"),("weighted","Weighted"),
                                             ("stabilizer","Stabilizer"),("pixel_perfect","Pixel perfect"))):
            action_id = "set_pixel_perfect_smoothing" if index==4 else "set_"+key+"_brush_smoothing"
            self.native_action(smoothing,action_id,label,index)
        self.edit_button = QToolButton(canvas)
        self.edit_button.setObjectName("prokrita_edit_shape")
        self.edit_button.setText("Edit Shape")
        self.edit_button.setToolTip("Move or reshape the last snapped stroke")
        self.edit_button.setFocusPolicy(Qt.NoFocus)
        self.edit_button.setCursor(Qt.PointingHandCursor)
        self.edit_button.setStyleSheet(
            "QToolButton { background: palette(window); color: palette(window-text);"
            " border: 1px solid palette(mid); border-radius: 13px; padding: 4px 12px; }"
            "QToolButton:hover { border-color: palette(highlight); }")
        self.edit_button.clicked.connect(extension.toggle_edit)
        self.edit_button.hide()
        self.repeat_button = QToolButton(canvas)
        self.repeat_button.setObjectName("prokrita_continue_fill")
        self.repeat_button.setFocusPolicy(Qt.NoFocus)
        self.repeat_button.setCursor(Qt.PointingHandCursor)
        self.repeat_button.setStyleSheet(self.edit_button.styleSheet())
        self.repeat_button.clicked.connect(extension.toggle_repeat)
        self.repeat_button.hide()
        self.layout_controls()
        self.show()
        self.raise_()

    def add_switch(self, label, key, default):
        action = self.menu.addAction(label)
        action.setCheckable(True)
        def refresh():
            action.setChecked(K.readSetting("ProKrita",key,str(default)).lower()=="true")
        self.menu.aboutToShow.connect(refresh)
        refresh()
        action.triggered.connect(lambda checked: K.writeSetting("ProKrita",key,str(checked).lower()))

    @staticmethod
    def native_action(menu, key, label, smoothing_index=None):
        native = K.action(key)
        if native:
            action = menu.addAction(label)
            action.triggered.connect(native.trigger)
            def refresh():
                action.setEnabled(native.isEnabled())
                if smoothing_index is None:
                    action.setCheckable(native.isCheckable())
                    action.setChecked(native.isChecked())
                else:
                    combo = smoothing_combo()
                    action.setCheckable(combo is not None)
                    action.setChecked(combo is not None and combo.currentIndex()==smoothing_index)
            menu.aboutToShow.connect(refresh)

    def buttons(self):
        return (self.brush_button, self.smudge_button, self.options_button, self.edit_button,
                self.repeat_button, self.shading)

    def show_shading(self, visible):
        self.shading_action.setChecked(visible)
        self.shading.setVisible(visible)
        if visible:
            self.layout_controls()
            self.shading.raise_()

    def show_repeat(self, active):
        self.repeat_button.setText("Done filling" if active else "Continue filling")
        self.repeat_button.setToolTip("Return to drawing" if active else "Tap additional regions to fill them")
        self.layout_controls()
        self.repeat_button.show()
        self.repeat_button.raise_()

    def owns_menu(self, widget):
        # Each submenu has its own popup window; QWidget.isAncestorOf stops at
        # that window even though the menu still belongs to this canvas.
        while isinstance(widget,QWidget):
            if widget is self.menu:
                return True
            widget = widget.parentWidget()
        return False

    def choose_brush(self):
        if self.smudge_button.active:
            self.smudge_button.toggle_smudge(False)
        brush, presets = K.action(BRUSH), K.action("show_brush_presets")
        if brush and presets:
            window = K.activeWindow()
            button = window.qwindow().findChild(QToolButton, BRUSH) if window else None
            if button is None or not button.isChecked():
                brush.trigger()
            presets.trigger()

    def open_color_picker(self, mode=None):
        if sip.isdeleted(self) or not self.isVisible():
            return
        window = K.activeWindow()
        view = window.activeView() if window else None
        if not view or canvas_widget(window) is not self.parentWidget():
            return
        if mode in ("native", "square"):
            K.writeSetting("ProKrita", "colorPickerLayout", mode)
        layout = mode or K.readSetting("ProKrita", "colorPickerLayout", "native")
        if layout == "square":
            if self.visible_picker():
                if mode is None:
                    self.close_color_picker()
                return
            self.close_color_picker()
            color = view.foregroundColor()
            if color:
                self.set_color(color.colorForCanvas(view.canvas()))
            self.picker = ColorPicker(self, view)
            self.place_picker()
            self.picker.show()
            self.picker.raise_()
            return
        self.close_color_picker()
        action = K.action("show_mypaint_shade_selector" if layout == "shade" else "show_color_selector")
        if action is not None and action.isEnabled():
            action.trigger()

    def visible_picker(self):
        picker = self.picker
        return picker if picker is not None and not sip.isdeleted(picker) and picker.isVisible() else None

    def close_color_picker(self):
        picker, self.picker = self.picker, None
        if picker is not None and not sip.isdeleted(picker):
            picker.close()

    def place_picker(self):
        picker = self.picker
        canvas = self.parentWidget()
        side = max(48, min(196, canvas.width() - 49, canvas.height() - 104))
        if picker.field.width() != side:
            picker.field.setFixedSize(side, side)
            picker.hue_field.setFixedHeight(side)
            picker.field.cache = picker.hue_field.cache = None
        picker.adjustSize()
        x = max(0, self.geometry().right() - picker.width() + 1)
        y = min(self.geometry().bottom() + 5, max(0, canvas.height() - picker.height()))
        picker.move(x, y)

    def layout_controls(self):
        canvas = self.parentWidget()
        self.move(max(0, canvas.width() - self.width() - 12), 10)
        self.brush_button.move(max(0, self.x() - self.brush_button.width() - 4), 15)
        self.smudge_button.move(max(0, self.brush_button.x()-self.smudge_button.width()-4), 15)
        self.options_button.move(max(0,self.smudge_button.x()-self.options_button.width()-4),15)
        self.shading.move(max(0,self.x()+self.width()-self.shading.width()), self.y()+self.height()+3)
        if self.visible_picker():
            self.place_picker()
        for pill in (self.edit_button,self.repeat_button):
            pill.adjustSize()
            x = max(0,min((canvas.width()-pill.width())//2,self.options_button.x()-pill.width()-4))
            pill.move(x, 15 if x+pill.width()<=self.options_button.x() else self.y()+self.height()+3)

    def show_edit(self, editing):
        self.edit_button.setText("Done" if editing else "Edit Shape")
        self.layout_controls()
        self.edit_button.show()
        self.edit_button.raise_()

    def set_color(self, color):
        if color != self.color:
            self.color = color
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(15, 15, 18, 200), 3))
        painter.setBrush(self.color)
        painter.drawEllipse(QRectF(4, 4, 34, 34))
        painter.setPen(QPen(Qt.white, 1.6))
        painter.drawEllipse(QRectF(4.7, 4.7, 32.6, 32.6))


class Guide(QWidget):
    def __init__(self, canvas, shape, view, transform, editing=False):
        super().__init__(canvas)
        self.setObjectName("prokrita_shape_guide")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(canvas.rect())
        self.editing = editing
        self.dragging = False
        self.shape = shape
        self.color = view.foregroundColor().colorForCanvas(view.canvas())
        self.color.setAlphaF(min(1.0, max(0.0, self.color.alphaF() * view.paintingOpacity())))
        to_canvas, _ = transform.inverted()
        self.width = max(0.8, length(to_canvas.map(QPointF(view.brushSize(), 0)) - to_canvas.map(QPointF())))
        self.show()
        self.raise_()

    def set_shape(self, shape):
        if self.shape == shape:
            return
        self.shape = shape
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        curve = self.shape.path()
        if not self.editing or self.dragging:
            painter.setPen(QPen(self.color, self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(curve)
        painter.setPen(QPen(QColor(30, 30, 34, 145), 1.6))
        painter.drawPath(curve)
        painter.setPen(QPen(QColor(255, 255, 255, 200), 0.9, Qt.DashLine))
        painter.drawPath(curve)
        if self.editing:
            painter.setPen(QPen(QColor(30, 30, 34), 1.5))
            painter.setBrush(Qt.white)
            for point in self.shape.handles():
                painter.drawEllipse(point, 4.5, 4.5)
        if self.editing or self.shape.kind != "Line":
            center = self.shape.center()
            painter.setPen(QPen(QColor(25, 25, 29), 1.5))
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(center, 5, 5)
            painter.drawLine(center - QPointF(2.5, 0), center + QPointF(2.5, 0))
            painter.drawLine(center - QPointF(0, 2.5), center + QPointF(0, 2.5))
        if not self.editing and self.shape.kind == "Line":
            painter.setPen(QPen(QColor(24, 24, 28), 1.5))
            painter.setBrush(Qt.white)
            painter.drawEllipse(self.shape.controls[1], 3.4, 3.4)

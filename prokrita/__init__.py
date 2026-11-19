from dataclasses import dataclass, field
import sys
from time import monotonic

from krita import Extension, Krita
from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, QPointF, QTimer, Qt
from PyQt5.QtGui import QIcon, QMouseEvent, QTabletEvent
from PyQt5.QtWidgets import QApplication, QListView, QToolButton, QWidget

from .canvas_ui import (BRUSH, CANVAS_CLASSES, FILL, ColorSwatch, DropMarker, Guide,
                        canvas_to_image, canvas_widget, drop_on_canvas, smoothing_combo)
from .shape_edit import Shape, length
from .pen import PenProfile, axes
from .fill import GROUP, FillPreview, drop_settings, threshold
from .selection import select_shape
from .smudge import BrushState, Smudge, blender_preset


K = Krita.instance()
PRESS = (QEvent.MouseButtonPress, QEvent.TabletPress)
MOVE = (QEvent.MouseMove, QEvent.TabletMove)
RELEASE = (QEvent.MouseButtonRelease, QEvent.TabletRelease)
POINTER = PRESS + MOVE + RELEASE
OBSERVED = POINTER + (QEvent.MouseButtonDblClick, QEvent.KeyPress, QEvent.KeyRelease, QEvent.Wheel,
                            QEvent.ApplicationDeactivate, QEvent.Resize, QEvent.Hide,
                            QEvent.Close, QEvent.Show, QEvent.Leave, QEvent.TabletLeaveProximity)


def live(widget):
    return widget is not None and not sip.isdeleted(widget)


def local_pos(event):
    return event.posF() if isinstance(event, QTabletEvent) else event.localPos()


def dispose(widget):
    if live(widget):
        widget.hide()
        widget.deleteLater()


def selected(window, name):
    button = window.qwindow().findChild(QToolButton, name) if window else None
    return button is not None and button.isChecked()


def history_text(source):
    # Undo labels can have two translations separated by a newline. The docker uses the first.
    return K.krita_i18nc("(qtundo-format)", source).split("\n")[0]


def settle(document):
    document.waitForDone()
    QCoreApplication.sendPostedEvents(None, QEvent.MetaCall)


@dataclass
class Target:
    canvas: QWidget
    document: object
    layer_id: object
    frame: int

    @classmethod
    def capture(cls, canvas, document):
        node = document.activeNode()
        if node and node.type() == "paintlayer" and node.visible() and not node.locked():
            return cls(canvas, document, node.uniqueId(), document.currentTime())
        return None

    def active_layer(self):
        if not live(self.canvas) or not self.canvas.isVisible():
            return None
        if K.activeDocument() != self.document or canvas_widget(K.activeWindow()) is not self.canvas:
            return None
        if self.document.currentTime() != self.frame:
            return None
        node = self.document.activeNode()
        if node and node.uniqueId() == self.layer_id and node.visible() and not node.locked():
            return node
        return None


@dataclass
class Stroke:
    target: Target
    history: QListView
    before: int
    revision: int
    transform: object
    tablet: bool
    anchor: QPointF
    samples: list
    pressures: list = field(default_factory=list)
    device: tuple = ()
    locked: Shape = None
    preview: Shape = None
    grab: QPointF = None
    retries: int = 0

@dataclass
class Recent:
    target: Target
    history: QListView
    row: int
    revision: int
    image_shape: Shape
    pressure: object
    label: str


@dataclass
class HandleDrag:
    original: Shape
    handle: object
    start: QPointF
    transform: object
    tablet: bool


@dataclass
class ColorDrag:
    swatch: ColorSwatch
    target: object
    color: object
    start: object
    tablet: bool
    marker: DropMarker = None
    hover: object = None
    point: object = None
    seed: object = None
    transform: object = None
    preview: object = None
    initial: int = 8
    value: int = 8
    tap: bool = False


class ProKrita(Extension):
    def setup(self):
        self.fill_target = None
        self.repeat_target = None
        self.repeat_tool = None
        self.repeating = False
        self.space_down = False
        self.smudge = None
        self.palette_visible = K.readSetting(GROUP,"shadingPalette","false").lower()=="true"
        self.swatch = None
        self.drop = None
        self.stroke = None
        self.pending = None
        self.recent = None
        self.editing = False
        self.handle_drag = None
        self.guide = None
        self.watched_model = None
        self.revision = 0
        self.replaying = False
        self.pen_quiet_at = 0.0
        self.overlay_echo = {}
        self.native_picker = None
        self.native_picker_drag = None
        self.pen_snap = K.readSetting(GROUP, "penSnap", "false").lower() == "true"
        self.rough_label = history_text("Freehand Brush Stroke")
        self.shape_label = history_text("Scripted Brush Stroke")
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.timeout.connect(self.snap)
        self.dwell = QTimer(self)
        self.dwell.setSingleShot(True)
        self.dwell.timeout.connect(self.anchor_drop)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.refresh_fill)
        # This also catches replaced OpenGL canvases, new windows and canvas-only mode.
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(250)
        self.ui_timer.timeout.connect(self.sync_ui)
        self.ui_timer.start()
        QApplication.instance().installEventFilter(self)

    def sync_ui(self):
        # Color conversion, layer queries and widget synchronization belong between
        # strokes, not on the same GUI thread as incoming tablet packets.
        if self.stroke is not None or self.pending is not None or monotonic() < self.pen_quiet_at:
            return
        window = K.activeWindow()
        view = window.activeView() if window else None
        canvas = canvas_widget(window) if view else None
        if canvas is None and live(self.swatch) and self.swatch.parentWidget().isVisible():
            return
        if self.smudge:
            self.check_smudge(window, view, canvas)
        if not live(self.swatch) or self.swatch.parentWidget() is not canvas:
            self.cancel_all()
            self.swatch = None
            if canvas:
                self.swatch = canvas.findChild(ColorSwatch)
                if self.swatch is None:
                    self.swatch = ColorSwatch(canvas,self)
                self.swatch.show_shading(self.palette_visible)
        if not self.swatch:
            return
        if self.repeat_target and not self.repeat_valid():
            self.stop_repeat()
        if self.drop and self.drop.seed is not None:
            if self.drop.target.active_layer() is None or canvas_to_image(view) != self.drop.transform:
                self.cancel_drop()
        color = view.foregroundColor()
        if color:
            self.swatch.set_color(color.colorForCanvas(view.canvas()))
            if self.swatch.shading.isVisible():
                self.swatch.shading.sync(view,color)
        if self.recent:
            if not self.recent_valid():
                self.forget_recent()
            elif self.editing:
                transform = canvas_to_image(view)
                if transform != self.edit_transform:
                    self.forget_recent()

    def clear_guide(self):
        dispose(self.guide)
        self.guide = None

    def stop_stroke(self):
        self.hold.stop()
        self.stroke = self.pending = None
        if not self.editing:
            self.clear_guide()

    def forget_recent(self):
        self.recent = None
        self.handle_drag = None
        if self.editing:
            self.clear_guide()
        self.editing = False
        if live(self.swatch):
            self.swatch.edit_button.hide()

    def cancel_drop(self):
        gesture = self.drop
        self.drop = None
        self.dwell.stop()
        self.preview_timer.stop()
        if gesture:
            dispose(gesture.marker)
            dispose(gesture.preview)

    def cancel_all(self):
        self.cancel_drop()
        self.stop_repeat()
        self.forget_recent()
        self.stop_stroke()
        self.fill_target = None

    def show_shading(self, visible):
        self.palette_visible = visible
        K.writeSetting(GROUP,"shadingPalette",str(visible).lower())
        if live(self.swatch):
            self.swatch.show_shading(visible)
            if visible:
                self.sync_ui()

    def smudge_visible(self):
        if self.smudge:
            self.finish_smudge(self.smudge)
        doc = K.activeDocument()
        if not doc or blender_preset() is None:
            return
        layer = doc.createNode("Smudge blend", "paintlayer")
        doc.rootNode().addChildNode(layer, None)
        doc.setActiveNode(layer)
        doc.waitForDone()
        self.toggle_smudge(True, layer.uniqueId())

    def toggle_smudge(self, continuous=False, blend_layer=None):
        if self.smudge:
            self.finish_smudge(self.smudge)
            return
        window = K.activeWindow()
        view = window.activeView() if window else None
        canvas = canvas_widget(window) if view else None
        if not canvas or not live(self.swatch) or self.swatch.parentWidget() is not canvas:
            return
        if Target.capture(canvas, view.document()) is None:
            view.showFloatingMessage("Choose a visible, unlocked paint layer to smudge.", QIcon(), 1600, 1)
            return
        blender = blender_preset()
        if blender is None:
            view.showFloatingMessage("Enable Krita 4 Default Resources for its blender brushes.", QIcon(), 2200, 1)
            return
        brush = BrushState.capture(view)
        if brush.preset is None:
            return
        self.cancel_all()
        if not selected(window, BRUSH):
            K.action(BRUSH).trigger()
        session = Smudge(window, view, canvas, brush, blender, continuous, blend_layer)
        if session.start():
            self.smudge = session
            self.swatch.smudge_button.set_active(True)
            message = "Smudge on. Tap Paint to return." if continuous else "Smudge once. Lift to draw again."
            view.showFloatingMessage(message, QIcon(), 1000, 1)

    def finish_smudge(self, session):
        if self.smudge is not session:
            return
        self.smudge = None
        session.restore_sampling()
        if live(self.swatch) and self.swatch.parentWidget() is session.canvas:
            self.swatch.smudge_button.set_active(False)
        if live(session.window.qwindow()):
            view = session.window.activeView()
            if view and view.document():
                session.finish(view)

    def check_smudge(self, window, view, canvas):
        session = self.smudge
        if (canvas is not session.canvas or view != session.view or not selected(window, BRUSH)
                or view.currentBrushPreset() != session.blender
                or (session.blend_layer and view.document().activeNode().uniqueId() != session.blend_layer)):
            self.finish_smudge(session)

    def observe_smudge(self, obj, event):
        session = self.smudge
        typ = event.type()
        if typ in PRESS and obj is session.canvas and event.button() == Qt.LeftButton:
            if session.released:
                # Rapid consecutive strokes may arrive before the posted return to paint.
                self.finish_smudge(session)
                return
            window = K.activeWindow()
            view = window.activeView() if window else None
            self.check_smudge(window, view, canvas_widget(window))
            pen = isinstance(event, QTabletEvent)
            if (self.smudge and not session.continuous and session.tablet is None
                    and not session.panning and not event.modifiers()
                    and not (pen and event.pointerType() == QTabletEvent.Eraser)
                    and Target.capture(session.canvas, view.document())):
                session.tablet = pen
        elif (typ in RELEASE and session.tablet is not None and not session.released
              and session.tablet == isinstance(event, QTabletEvent) and event.button() in (Qt.LeftButton, Qt.NoButton)):
            session.released = True
            # Krita must receive the tip-up with the blender still active.
            QTimer.singleShot(0, lambda: self.finish_smudge(session))

    def repeat_valid(self):
        return (self.repeat_target is not None and self.repeat_target.active_layer() is not None
                and (self.repeat_tool is None or (live(self.repeat_tool) and self.repeat_tool.isChecked())))

    def stop_repeat(self):
        if self.drop and self.drop.tap:
            self.cancel_drop()
        self.repeat_target = self.repeat_tool = None
        self.repeating = False
        self.space_down = False
        if live(self.swatch):
            self.swatch.repeat_button.hide()

    def offer_repeat(self, target):
        if self.repeat_target is None:
            buttons = K.activeWindow().qwindow().findChildren(QToolButton)
            self.repeat_tool = next((b for b in buttons if b.inherits("KoToolBoxButton") and b.isChecked()),None)
        self.repeat_target = target
        self.swatch.show_repeat(self.repeating)

    def toggle_repeat(self):
        if self.repeating or not self.repeat_valid():
            self.stop_repeat()
            return
        self.repeating = True
        self.swatch.show_repeat(True)
        K.activeWindow().activeView().showFloatingMessage("Tap to fill. Hold and slide to adjust.",QIcon(),1500,1)

    def suspend_gestures(self):
        # A Windows popup can briefly deactivate Krita. Cancel live input, but
        # keep the committed shape, whose document and history are checked on use.
        self.cancel_drop()
        self.space_down = False
        self.handle_drag = None
        self.editing = False
        self.stop_stroke()
        self.fill_target = None
        if self.recent and live(self.swatch):
            self.swatch.show_edit(False)

    def watch(self, model):
        if model is not self.watched_model:
            if live(self.watched_model):
                self.watched_model.modelReset.disconnect(self.history_changed)
            self.watched_model = model
            self.revision += 1
            model.modelReset.connect(self.history_changed)

    def history_changed(self):
        self.revision += 1
        if self.recent:
            self.forget_recent()

    def begin(self, canvas, event):
        if isinstance(event, QTabletEvent) and (
                not self.pen_snap or event.pointerType() == QTabletEvent.Eraser):
            return
        if self.smudge or canvas.metaObject().className() not in CANVAS_CLASSES:
            return
        window = K.activeWindow()
        if not selected(window, BRUSH) or canvas is not canvas_widget(window):
            return
        view = window.activeView()
        if view.eraserMode():
            return
        target = Target.capture(canvas, view.document())
        if target is None:
            return
        history = next((item for item in window.qwindow().findChildren(QListView)
                        if item.inherits("KisUndoView")), None)
        transform = canvas_to_image(view)
        if history is None or transform is None:
            return
        self.watch(history.model())
        pos = local_pos(event)
        pen = isinstance(event, QTabletEvent)
        self.stroke = Stroke(target, history, history.currentIndex().row(), self.revision,
                             transform, pen, pos, [(pos.x(), pos.y())])
        if pen and sys.platform != "win32":
            self.stroke.device = (event.device(), event.pointerType(), event.uniqueId())
            self.stroke.pressures.append(axes(event))

    def snap(self):
        stroke = self.stroke
        if not stroke or stroke.locked or stroke.target.active_layer() is None:
            return
        fit = Shape.fit(stroke.samples)
        if fit is not None:
            stroke.grab = QPointF(*stroke.samples[-1])
            stroke.locked = stroke.preview = fit
            view = K.activeWindow().activeView()
            self.guide = Guide(stroke.target.canvas, fit, view, stroke.transform)

    def adjust_held(self, stroke, point):
        # Pen-up can jitter by a pixel even when the artist has held perfectly still.
        edited = stroke.locked if length(point - stroke.grab) <= 2 else stroke.locked.held(stroke.grab, point)
        stroke.preview = edited
        if live(self.guide):
            self.guide.set_shape(edited)

    def track(self, event):
        stroke = self.stroke
        pos = local_pos(event)
        if event.type() in MOVE:
            if not stroke.tablet and not (event.buttons() & Qt.LeftButton):
                self.stop_stroke()
                return False
            if stroke.locked:
                self.adjust_held(stroke, pos)
                # The real brush already painted the sketch. Do not feed handle motion
                # into its dab queue. The real release still ends exactly that stroke.
                return True
            sample = (pos.x(), pos.y())
            if stroke.samples[-1] != sample:
                # Keep geometry and pressure aligned without growing indefinitely
                # during long high-frequency pen strokes. Always retain endpoints.
                if len(stroke.samples) >= 4096:
                    stroke.samples[:] = stroke.samples[::2] + [stroke.samples[-1]]
                    if stroke.pressures:
                        stroke.pressures[:] = stroke.pressures[::2] + [stroke.pressures[-1]]
                stroke.samples.append(sample)
                if stroke.pressures:
                    stroke.pressures.append(axes(event))
            elif stroke.tablet and len(stroke.pressures) > 1 and event.pressure() > 0:
                self.stroke.pressures[-1] = axes(event)
            if length(pos - stroke.anchor) > 4:
                stroke.anchor = pos
                self.hold.start(450)
        elif event.type() in RELEASE and event.button() in (Qt.LeftButton, Qt.NoButton):
            self.hold.stop()
            self.stroke = None
            if stroke.locked:
                self.adjust_held(stroke, pos)
                self.pending = stroke
                QTimer.singleShot(0, lambda: self.finish_native(stroke))
        return False

    def finish_native(self, stroke):
        if self.pending is not stroke:
            return
        if stroke.target.active_layer() is None or not live(stroke.history):
            self.stop_stroke()
            return
        # Wait for Krita's undo model asynchronously. Flushing the document
        # here stalls input while a large brush stroke is still rendering.
        index = stroke.history.currentIndex()
        after = index.row()
        at_tip = after > 0 and after == stroke.history.model().rowCount() - 1
        advanced = after == stroke.before + 1
        # Krita 5.3 issues two model resets when a new command evicts the oldest
        # at the history limit. In both cases also require the exact native brush command.
        rolled = after == stroke.before and self.revision == stroke.revision + 2
        if (self.revision > stroke.revision and at_tip and (advanced or rolled)
                and index.data() == self.rough_label):
            self.pending = None
            self.clear_guide()
            shape = stroke.preview.mapped(stroke.transform)
            pressure = PenProfile.capture(stroke) if sys.platform != "win32" else None
            self.replace_top(stroke.target, stroke.history, self.rough_label, shape, pressure)
        elif stroke.retries < 24 and self.revision == stroke.revision:
            stroke.retries += 1
            QTimer.singleShot(20, lambda: self.finish_native(stroke))
        else:
            self.stop_stroke()

    def recent_valid(self):
        item = self.recent
        return (item is not None and self.revision == item.revision and live(item.history)
                and item.history.currentIndex().row() == item.row
                and item.history.currentIndex().data() == item.label
                and item.target.active_layer() is not None)

    def replace_top(self, target, history, expected, shape, pressure, reopen=False, return_to_mouse=False):
        # Re-entering Windows tablet input with fabricated proximity/button
        # events can corrupt the real driver's stroke state. Use Krita's native
        # path API for snapped shapes; physical freehand pressure stays native.
        if sys.platform == "win32":
            pressure = None
        node = target.active_layer()
        index = history.currentIndex()
        row = index.row()
        if (node is None or row <= 0 or index.data() != expected
                or row != history.model().rowCount() - 1):
            return
        undo = K.action("edit_undo")
        if not undo or not undo.isEnabled():
            return
        self.forget_recent()
        document = target.document
        undo.trigger()
        settle(document)
        if history.currentIndex().row() != row - 1:
            return
        result_label = self.rough_label if pressure else self.shape_label
        revision = self.revision
        try:
            if pressure:
                transform = canvas_to_image(K.activeWindow().activeView())
                if transform:
                    self.replaying = True
                    smoothing = smoothing_combo()
                    mode = smoothing.currentIndex() if smoothing is not None else -1
                    try:
                        # The fitted geometry is already smooth. A second time-based
                        # stabilizer pass would round corners or delay the replay.
                        if mode in (2,3):
                            smoothing.setCurrentIndex(0)
                        pressure.replay(target.canvas, shape.mapped(transform.inverted()[0]),return_to_mouse)
                    finally:
                        if mode in (2,3) and live(smoothing):
                            smoothing.setCurrentIndex(mode)
            else:
                node.paintPath(shape.path(), "ForegroundColor", "None")
            settle(document)
        finally:
            self.replaying = False
            painted = self.revision != revision and history.currentIndex().row() == row
            # A locked channel, empty selection or failed paint engine can leave the
            # original as a redo entry. Restore that specific entry if painting did nothing.
            if (history.currentIndex().row() == row - 1
                    and history.model().index(row, 0).data() == expected):
                redo = K.action("edit_redo")
                if redo and redo.isEnabled():
                    redo.trigger()
                    settle(document)
        document.refreshProjection()
        if (painted and history.currentIndex().data() == result_label
                and live(self.swatch) and self.swatch.parentWidget() is target.canvas):
            self.recent = Recent(target, history, row, self.revision, shape, pressure, result_label)
            self.swatch.show_edit(False)
            if reopen:
                self.toggle_edit()

    def toggle_edit(self):
        if self.editing:
            self.clear_guide()
            self.editing = False
            self.handle_drag = None
            if self.recent_valid():
                self.swatch.show_edit(False)
            return
        if not self.recent_valid():
            self.forget_recent()
            return
        view = K.activeWindow().activeView()
        transform = canvas_to_image(view)
        if transform is None:
            return
        self.edit_transform = transform
        displayed = self.recent.image_shape.mapped(transform.inverted()[0])
        self.editing = True
        self.guide = Guide(self.recent.target.canvas, displayed, view, transform, editing=True)
        self.swatch.raise_()
        self.swatch.brush_button.raise_()
        self.swatch.options_button.raise_()
        self.swatch.show_edit(True)

    def last_shape(self):
        return self.recent.image_shape if self.recent_valid() else None

    def refine_recent(self, kind):
        if self.recent_valid():
            item = self.recent
            shape = item.image_shape.refined(kind)
            if shape is not item.image_shape:
                self.replace_top(item.target,item.history,item.label,shape,item.pressure,reopen=True,return_to_mouse=True)

    def select_recent(self, mode):
        if self.recent_valid() and self.recent.image_shape.closed:
            item = self.recent
            self.forget_recent()
            select_shape(item.target.document,item.image_shape,mode)

    def edit_press(self, event):
        if not self.editing or not self.recent_valid() or not live(self.guide):
            return False
        pos = local_pos(event)
        handle = self.guide.shape.hit(pos)
        if handle is None:
            return False
        self.handle_drag = HandleDrag(self.guide.shape, handle, pos, self.edit_transform,
                                     isinstance(event, QTabletEvent))
        self.guide.dragging = True
        return True

    def edit_motion(self, event, obj):
        drag = self.handle_drag
        if not self.recent or not live(self.guide):
            return False
        if drag.tablet != isinstance(event, QTabletEvent):
            return True
        canvas = self.recent.target.canvas
        pos = local_pos(event) if obj is canvas else QPointF(canvas.mapFromGlobal(event.globalPos()))
        delta = pos - drag.start
        shape = drag.original.dragged(drag.handle, delta)
        self.guide.set_shape(shape)
        if event.type() in RELEASE:
            self.handle_drag = None
            self.guide.dragging = False
            item = self.recent
            if length(delta) >= 1 and self.recent_valid():
                self.replace_top(item.target, item.history, item.label,
                                 shape.mapped(drag.transform), item.pressure, reopen=True,return_to_mouse=not drag.tablet)
        return True

    def start_drop(self, event, tap=False):
        self.forget_recent()
        self.stop_stroke()
        window = K.activeWindow()
        view = window.activeView() if window else None
        if not view or self.swatch.parentWidget() is not canvas_widget(window):
            return
        color = view.foregroundColor() if tap else None
        canvas = self.swatch.parentWidget()
        self.drop = ColorDrag(self.swatch, Target.capture(canvas, view.document()) if tap else None, color,
                              event.globalPos() if tap else self.swatch.mapToGlobal(local_pos(event).toPoint()),
                              isinstance(event, QTabletEvent), tap=tap)
        if tap:
            self.drop.initial = self.drop.value = threshold()
            self.drop.point = canvas.mapFromGlobal(event.globalPos())
            self.drop.transform = canvas_to_image(view)
            self.dwell.start(350)

    def anchor_drop(self):
        drag = self.drop
        if not drag or drag.point is None or not drag.target or drag.target.active_layer() is None:
            return
        view = K.activeWindow().activeView()
        transform = canvas_to_image(view)
        if transform is None or (drag.tap and transform!=drag.transform):
            return
        seed = transform.map(QPointF(drag.point))
        doc = drag.target.document
        if not (0 <= seed.x() < doc.width() and 0 <= seed.y() < doc.height()):
            return
        drag.seed, drag.transform = drag.point, transform
        if drag.marker:
            drag.marker.hide()
        drag.preview = FillPreview(drag.target.canvas, drag.target, drag.seed, transform,
                                   drag.swatch.color, view.paintingOpacity())
        self.refresh_fill()

    def refresh_fill(self):
        drag = self.drop
        if drag and live(drag.preview):
            drag.preview.set_threshold(drag.value)

    def drop_motion(self, event, receiver):
        drag = self.drop
        if drag.tablet != isinstance(event, QTabletEvent):
            return event.type() in RELEASE or event.buttons() != Qt.NoButton
        if not live(drag.swatch):
            self.cancel_drop()
            return True
        if event.type() in RELEASE and event.button() not in (Qt.LeftButton,Qt.NoButton):
            return True
        if (drag.marker and event.type() in MOVE and event.buttons() == Qt.NoButton
                and (not drag.tablet or event.pressure() <= 0)):
            self.cancel_drop()
            return False
        # Overlay-local coordinates stay reliable on mixed-DPI tablet displays.
        global_pos = (drag.swatch.mapToGlobal(local_pos(event).toPoint())
                      if receiver is drag.swatch else event.globalPos())
        canvas = drag.swatch.parentWidget()
        point = canvas.mapFromGlobal(global_pos)
        swatch_point = drag.swatch.mapFromGlobal(global_pos)
        on_swatch = drag.swatch.rect().contains(swatch_point)
        if not drag.tap and not drag.marker:
            tap_bounds = drag.swatch.rect().adjusted(-4, -4, 4, 4)
            if event.type() in RELEASE:
                self.cancel_drop()
                if tap_bounds.contains(swatch_point):
                    QTimer.singleShot(0, drag.swatch.open_color_picker)
                return True
            if event.buttons() == Qt.NoButton and (not drag.tablet or event.pressure() <= 0):
                return False
            if (tap_bounds.contains(swatch_point)
                    or (global_pos - drag.start).manhattanLength() < QApplication.startDragDistance()):
                return True
            window = K.activeWindow()
            view = window.activeView() if window else None
            if not view or canvas_widget(window) is not canvas:
                self.cancel_drop()
                return True
            drag.target = Target.capture(canvas, view.document())
            drag.color = view.foregroundColor()
            drag.initial = drag.value = threshold()
            drag.swatch.set_color(drag.color.colorForCanvas(view.canvas()))
            drag.marker = DropMarker(canvas, drag.swatch.color)
        over_brush = any(w.isVisible() and w.geometry().contains(point) for w in drag.swatch.buttons())
        window = canvas.window()
        underlying = window.childAt(window.mapFromGlobal(global_pos))
        over_canvas = (canvas.rect().contains(point) and not (on_swatch or over_brush)
                       and (underlying is canvas or (underlying is not None and canvas.isAncestorOf(underlying))))
        if (drag.tap and drag.seed is None and event.type() in MOVE and over_canvas
                and abs(point.x()-drag.point.x())>=QApplication.startDragDistance()):
            self.dwell.stop()
            self.anchor_drop()
        if drag.marker and drag.seed is None:
            if over_canvas:
                drag.marker.track(point)
                drag.point = point
                if drag.hover is None or (point-drag.hover).manhattanLength() > 5:
                    drag.hover = point
                    self.dwell.start(350)
            else:
                drag.marker.hide()
                drag.hover = drag.point = None
                self.dwell.stop()
        elif drag.seed is not None:
            span = max(110, min(320,canvas.width()*.42))
            drag.value = max(1, min(100, drag.initial + round(100*(point.x()-drag.seed.x())/span)))
            if not self.preview_timer.isActive():
                self.preview_timer.start(20)
        if event.type() not in RELEASE:
            return True
        self.cancel_drop()
        if over_canvas:
            window = K.activeWindow()
            view = window.activeView() if window else None
            if not view:
                return True
            if not drag.target or drag.target.active_layer() is None:
                view.showFloatingMessage("Choose a visible, unlocked paint layer to fill.", QIcon(), 1800, 1)
                return True
            transform = canvas_to_image(view)
            if transform is None:
                return True
            if (drag.tap or drag.seed is not None) and transform != drag.transform:
                return True
            destination = drag.seed if drag.seed is not None else (drag.point if drag.tap else point)
            seed = transform.map(QPointF(destination))
            doc = drag.target.document
            if 0 <= seed.x() < doc.width() and 0 <= seed.y() < doc.height():
                erasing = view.eraserMode()
                try:
                    if erasing:
                        view.setEraserMode(False)
                    with drop_settings(view, drag.target.active_layer(), seed, drag.value):
                        accepted = drop_on_canvas(canvas, destination, drag.color)
                    if accepted:
                        if drag.seed is not None:
                            K.writeSetting(GROUP,"threshold",str(drag.value))
                        self.offer_repeat(drag.target)
                finally:
                    if erasing:
                        view.setEraserMode(True)
        return True

    def accept_overlay_pointer(self, event):
        # Windows Ink emits a mouse button event for each accepted pen button
        # event. It may reach the widget underneath after the picker changes.
        kind = event.type()
        if kind == QEvent.TabletPress:
            self.overlay_echo[QEvent.MouseButtonPress] = monotonic() + .25
        elif kind == QEvent.TabletRelease:
            self.overlay_echo[QEvent.MouseButtonRelease] = monotonic() + .25
        event.accept()
        return True

    def route_native_picker(self, obj, event):
        kind = event.type()
        if kind == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() and obj.inherits("KisColorSelector"):
            self.native_picker = obj
        picker = self.native_picker
        if not live(picker) or not picker.isVisible():
            self.native_picker_drag = None
            return False
        if kind == QEvent.Leave and obj is picker and self.native_picker_drag is not None:
            return True
        if kind not in POINTER + (QEvent.MouseButtonDblClick, QEvent.TabletLeaveProximity):
            return False
        pressing = kind in PRESS + (QEvent.MouseButtonDblClick,)
        if not pressing and self.native_picker_drag is None:
            return False
        proximity_exit = kind == QEvent.TabletLeaveProximity
        if proximity_exit and self.native_picker_drag is not True:
            return False
        tablet = isinstance(event, QTabletEvent)
        point = picker.mapFromGlobal(event.globalPos()) if not proximity_exit else None
        if pressing:
            if self.native_picker_drag is True and not tablet and event.source() != Qt.MouseEventNotSynthesized:
                return True
            self.native_picker_drag = None
            if event.button() != Qt.LeftButton or (obj is not picker and not picker.rect().contains(point)):
                return False
            self.native_picker_drag = tablet
        elif self.native_picker_drag is None:
            return False
        elif self.native_picker_drag != tablet and not proximity_exit:
            return True
        released = kind in RELEASE or proximity_exit
        if released:
            self.native_picker_drag = None
        if tablet or obj is not picker:
            # Send only widget-local mouse input to the selector. No synthetic
            # tablet event re-enters Krita's painting input manager.
            mouse_kind = (QEvent.MouseButtonRelease if released else QEvent.MouseButtonPress
                          if pressing else QEvent.MouseMove)
            if proximity_exit:
                point = picker.rect().center()
            elif obj is picker:
                point = event.pos()
            button = Qt.NoButton if mouse_kind == QEvent.MouseMove else Qt.LeftButton
            buttons = Qt.NoButton if released else Qt.LeftButton
            local = QPointF(point)
            screen = QPointF(picker.mapToGlobal(point))
            mouse = QMouseEvent(mouse_kind, local, local, screen, button, buttons, Qt.NoModifier)
            self.replaying = True
            try:
                QApplication.sendEvent(picker, mouse)
            finally:
                self.replaying = False
            if released and live(picker) and picker.isVisible() and not picker.rect().contains(point):
                QCoreApplication.postEvent(picker, QEvent(QEvent.Leave))
            return not proximity_exit
        return False

    def eventFilter(self, obj, event):
        if self.replaying:
            return False
        typ = event.type()
        if typ not in OBSERVED:
            return False
        if typ == QEvent.MouseButtonDblClick:
            echo_kind = QEvent.MouseButtonPress
        else:
            echo_kind = typ
        if echo_kind in self.overlay_echo:
            expires = self.overlay_echo.pop(echo_kind)
            if monotonic() <= expires and event.source() != Qt.MouseEventNotSynthesized:
                event.accept()
                return True
        if typ in (QEvent.TabletPress, QEvent.TabletRelease):
            self.pen_quiet_at = monotonic() + .18
            if typ == QEvent.TabletPress:
                self.overlay_echo.clear()
        elif typ == QEvent.TabletMove and event.buttons() & Qt.LeftButton:
            self.pen_quiet_at = monotonic() + .18
        if (self.native_picker is not None or typ == QEvent.Show) and self.route_native_picker(obj, event):
            return self.accept_overlay_pointer(event)
        picker = self.swatch.visible_picker() if live(self.swatch) and self.swatch.picker is not None else None
        if picker is not None:
            if typ in POINTER or typ == QEvent.MouseButtonDblClick:
                point = picker.pointer_position(obj, event)
                inside = picker.owns(obj) or picker.rect().contains(point)
                if (inside or picker.drag_tablet is not None) and picker.route_pointer(obj, event, point):
                    return self.accept_overlay_pointer(event)
                if inside:
                    return False
                if typ in PRESS and obj is not self.swatch:
                    self.swatch.close_color_picker()
                    if obj is self.swatch.parentWidget():
                        return self.accept_overlay_pointer(event)
            elif typ == QEvent.TabletLeaveProximity:
                picker.drag_tablet = None
            elif typ == QEvent.ApplicationDeactivate or (typ == QEvent.Hide and obj is picker.parentWidget()):
                self.swatch.close_color_picker()
            elif typ == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self.swatch.close_color_picker()
                event.accept()
                return True
        if typ in MOVE and self.stroke is None and self.drop is None and self.handle_drag is None:
            return False
        if typ == QEvent.TabletLeaveProximity:
            if self.stroke and self.stroke.tablet:
                self.stop_stroke()
            if self.drop and self.drop.tablet:
                self.cancel_drop()
            return False
        if (typ == QEvent.TabletPress and event.pointerType() == QTabletEvent.Eraser
                and live(self.swatch) and obj is self.swatch.parentWidget()):
            # The eraser tip belongs to Krita, even with a shape or fill armed.
            self.cancel_all()
            return False
        if self.smudge:
            session = self.smudge
            if typ in PRESS + RELEASE:
                self.observe_smudge(obj, event)
            elif typ in (QEvent.KeyPress, QEvent.KeyRelease):
                if event.key() == Qt.Key_Space:
                    session.panning = typ == QEvent.KeyPress
                elif typ == QEvent.KeyPress and event.key() == Qt.Key_Escape and not QApplication.activePopupWidget():
                    self.finish_smudge(session)
                    event.accept()
                    return True
            elif typ == QEvent.ApplicationDeactivate:
                session.panning = False
                if session.tablet is not None:
                    QTimer.singleShot(0, lambda: self.finish_smudge(session))
            elif (typ == QEvent.Close and isinstance(obj, QWidget) and live(session.canvas)
                  and obj.isAncestorOf(session.canvas)):
                self.finish_smudge(session)
            elif typ == QEvent.Hide and obj is session.canvas:
                QTimer.singleShot(0, lambda: self.finish_smudge(session))
        if typ == QEvent.ApplicationDeactivate:
            self.suspend_gestures()
            return False
        if typ == QEvent.Resize and live(self.swatch) and obj is self.swatch.parentWidget():
            self.swatch.layout_controls()
        if typ == QEvent.Hide and live(self.swatch) and obj is self.swatch.parentWidget():
            self.cancel_all()
        if self.repeating and typ==QEvent.KeyRelease and event.key()==Qt.Key_Space:
            self.space_down = False
        if live(self.swatch) and (typ in PRESS or typ in (QEvent.KeyPress,QEvent.Wheel)):
            if obj is self.swatch.options_button or self.swatch.owns_menu(obj):
                return False
        if typ in (QEvent.KeyPress, QEvent.Wheel):
            if self.repeating and (typ==QEvent.Wheel or event.key() in (Qt.Key_Space,Qt.Key_Control,Qt.Key_Shift,Qt.Key_Alt)):
                if typ==QEvent.KeyPress and event.key()==Qt.Key_Space:
                    self.space_down = True
                self.cancel_drop()
                return False
            was_editing = self.repeating or self.editing or self.drop is not None or (self.stroke and self.stroke.locked)
            self.cancel_all()
            if typ == QEvent.KeyPress and event.key() == Qt.Key_Escape and was_editing:
                event.accept()
                return True
        is_double_tap = (typ==QEvent.MouseButtonDblClick and self.repeating and live(self.swatch)
                         and obj is self.swatch.parentWidget())
        if (typ not in POINTER and not is_double_tap) or not isinstance(obj, QWidget):
            if typ != QEvent.MouseButtonDblClick or not isinstance(obj, ColorSwatch):
                return False
        if (isinstance(obj, ColorSwatch) and typ in PRESS + (QEvent.MouseButtonDblClick,)
                and event.button() == Qt.LeftButton):
            # A new overlay press must work even after Krita lost a canvas tip-up.
            if (self.drop and self.drop.tablet and typ != QEvent.TabletPress
                    and event.source() != Qt.MouseEventNotSynthesized):
                return True  # Ignore the mouse echo of the active pen gesture.
            self.cancel_all()
            if obj is not self.swatch:
                self.sync_ui()
            if obj is self.swatch:
                self.start_drop(event)
            return self.accept_overlay_pointer(event)
        if self.repeating and self.space_down and obj is self.repeat_target.canvas:
            return False
        if self.drop and typ in MOVE + RELEASE:
            if self.drop_motion(event, obj):
                return self.accept_overlay_pointer(event)
            return False
        if self.handle_drag and typ in MOVE + RELEASE:
            event.accept()
            return self.edit_motion(event, obj)
        if typ in PRESS or is_double_tap:
            if self.drop and (typ == QEvent.TabletPress
                              or event.source() == Qt.MouseEventNotSynthesized):
                self.cancel_drop()
            if self.stroke:
                # A second real press on this canvas recovers from a lost release;
                # propagated events and the tablet's synthesized mouse press are duplicates.
                if (obj is not self.stroke.target.canvas or event.button() != Qt.LeftButton
                        or self.stroke.tablet != isinstance(event, QTabletEvent)):
                    return False
                self.stop_stroke()
            if self.drop or self.handle_drag:
                return True
            if self.pending:
                # Never make the next brush press wait for a previous snap.
                self.stop_stroke()
            if live(self.swatch):
                if obj is self.swatch.edit_button or obj is self.swatch.repeat_button:
                    return False
                if self.repeat_target and (obj is self.swatch.brush_button or obj.inherits("KoToolBoxButton")):
                    self.stop_repeat()
                if obj is self.swatch.parentWidget() and event.button() == Qt.LeftButton and not event.modifiers():
                    if self.repeat_target:
                        eraser_tip = isinstance(event,QTabletEvent) and event.pointerType()==QTabletEvent.Eraser
                        if self.repeating and self.repeat_valid() and not eraser_tip:
                            self.start_drop(event,tap=True)
                            event.accept()
                            return True
                        self.stop_repeat()
                    if self.edit_press(event):
                        event.accept()
                        return True
            self.forget_recent()
            if self.fill_target and not selected(K.activeWindow(), FILL):
                self.fill_target = None
            if not self.fill_target and event.button() == Qt.LeftButton and not event.modifiers():
                self.begin(obj, event)
        elif self.fill_target is obj and typ in RELEASE and event.button() == Qt.LeftButton:
            self.fill_target = None
            QTimer.singleShot(0, lambda: K.action(BRUSH).trigger() if selected(K.activeWindow(), FILL) else None)
        elif self.stroke and obj is self.stroke.target.canvas:
            if self.stroke.tablet == isinstance(event, QTabletEvent) and self.track(event):
                event.accept()
                return True
        return False

    def createActions(self, window):
        action = window.createAction("prokrita_fill", "Fill Once", "tools/scripts/ProKrita")
        action.setShortcut("Ctrl+Alt+F")

        def fill():
            active = K.activeWindow()
            canvas = canvas_widget(active)
            if canvas:
                self.cancel_all()
                K.action(FILL).trigger()
                self.fill_target = canvas

        action.triggered.connect(fill)


K.addExtension(ProKrita(K))

"""A small, color-managed light and shadow palette for the current brush."""
from math import log

from krita import Krita, ManagedColor
from PyQt5.QtCore import QEvent, QSignalBlocker, Qt
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSlider, QStyle, QStyleOptionSlider,
                             QToolButton, QVBoxLayout)


K = Krita.instance()
TONES = ("Deep shadow", "Shadow", "Base color", "Light", "Highlight")


def shade_colors(base):
    """Adjust perceptual lightness in Lab, then return to the artist's source profile."""
    space = (base.colorModel(), base.colorDepth(), base.colorProfile())
    values = base.components()
    lab = ManagedColor(*space)
    lab.setComponents(values)
    if not lab.setColorSpace("LABA", "U16", ""):
        return [base] * len(TONES)
    lab_space = (lab.colorModel(), lab.colorDepth(), lab.colorProfile())
    lightness, a, b, alpha = lab.components()
    result = []
    for step in (-.48, -.23, 0., .24, .50):
        if not step:
            result.append(base)
            continue
        room = lightness if step < 0 else 1-lightness
        chroma = 1 - abs(step)*.38
        neutral = 128/255
        tone = ManagedColor(*lab_space)
        tone.setComponents([lightness+room*step, neutral+(a-neutral)*chroma,
                            neutral+(b-neutral)*chroma, alpha])
        if tone.setColorSpace(*space):
            # Keep the original alpha even when the source has more precision than Lab/U16.
            components = tone.components()
            components[-1] = values[-1]
            tone.setComponents(components)
            result.append(tone)
        else:
            result.append(base)
    return result


class BrushSlider(QSlider):
    """Tap anywhere on the track or scrub with a pen or mouse."""
    def __init__(self, name, maximum, parent):
        super().__init__(Qt.Horizontal,parent)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setFocusPolicy(Qt.NoFocus)
        self.setRange(0,maximum)
        self.setMinimumHeight(24)
        self.setAttribute(Qt.WA_TabletTracking)

    def set_from_point(self, point):
        options = QStyleOptionSlider()
        self.initStyleOption(options)
        grip = self.style().pixelMetric(QStyle.PM_SliderLength,options,self)
        self.setValue(QStyle.sliderValueFromPosition(self.minimum(),self.maximum(),
                      round(point.x()-grip/2),max(1,self.width()-grip),options.upsideDown))

    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setSliderDown(True)
        self.set_from_point(event.pos())
        event.accept()

    def mouseMoveEvent(self,event):
        if self.isSliderDown():
            self.set_from_point(event.pos())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self.isSliderDown() and event.button()==Qt.LeftButton:
            self.set_from_point(event.pos())
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def tabletEvent(self,event):
        if event.type()==QEvent.TabletPress and event.button()==Qt.LeftButton:
            self.setSliderDown(True)
        if self.isSliderDown():
            self.set_from_point(event.posF())
            if event.type()==QEvent.TabletRelease:
                self.setSliderDown(False)
            event.accept()
        else:
            event.ignore()


class ShadingPalette(QFrame):
    def __init__(self, canvas, close):
        super().__init__(canvas)
        self.setObjectName("prokrita_shading_palette")
        self.setAccessibleName("Shading palette")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_NoMousePropagation)
        self.setStyleSheet("QFrame#prokrita_shading_palette { background: palette(window);"
                           " border: 1px solid palette(mid); border-radius: 7px; }")
        self.document = self.expected_color = None
        self.colors = []
        self.chips = []
        try:
            self.configured_max = max(1,int(K.readSetting("","maximumBrushSize","1000")))
        except ValueError:
            self.configured_max = 1000
        self.size_limit = self.configured_max
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7,4,7,6)
        layout.setSpacing(4)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Shading", self))
        heading.addStretch()
        dismiss = QToolButton(self)
        dismiss.setText("×")
        dismiss.setToolTip("Close shading palette")
        dismiss.setAccessibleName("Close shading palette")
        dismiss.setFocusPolicy(Qt.NoFocus)
        dismiss.setAutoRaise(True)
        dismiss.clicked.connect(lambda: close(False))
        heading.addWidget(dismiss)
        layout.addLayout(heading)
        row = QHBoxLayout()
        row.setSpacing(3)
        for i, name in enumerate(TONES):
            chip = QToolButton(self)
            chip.setFixedSize(40 if i==2 else 32,30)
            if i==2:
                chip.setText("Base")
            chip.setCheckable(True)
            chip.setAccessibleName(name)
            chip.setToolTip(name)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda checked, index=i: self.choose(index))
            row.addWidget(chip)
            self.chips.append(chip)
        layout.addLayout(row)
        self.size_slider, self.size_text = self.brush_row(layout,"Size","Brush diameter in pixels",1000)
        self.opacity_slider, self.opacity_text = self.brush_row(layout,"Opacity","Brush opacity",100)
        self.size_slider.valueChanged.connect(self.change_size)
        self.opacity_slider.valueChanged.connect(self.change_opacity)
        actions = QHBoxLayout()
        self.lock = QToolButton(self)
        self.lock.setText("Lock alpha")
        self.lock.setToolTip("Keep the active paint layer's existing transparency as you shade")
        self.lock.setCheckable(True)
        self.lock.clicked.connect(lambda: self.layer_action("toggle_layer_alpha_lock"))
        self.clip = QToolButton(self)
        self.clip.setText("Clip layer")
        self.clip.setToolTip("Group the selected layers and add a new paint layer clipped to their silhouette")
        self.clip.clicked.connect(lambda: self.layer_action("create_quick_clipping_group"))
        for button in (self.lock,self.clip):
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.adjustSize()
        self.hide()

    def brush_row(self,layout,label,name,maximum):
        row = QHBoxLayout()
        caption = QLabel(label,self)
        caption.setMinimumWidth(caption.fontMetrics().horizontalAdvance("Opacity"))
        row.addWidget(caption)
        slider = BrushSlider(name,maximum,self)
        caption.setBuddy(slider)
        row.addWidget(slider,1)
        readout = QLabel(self)
        readout.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
        readout.setMinimumWidth(readout.fontMetrics().horizontalAdvance("1000 px"))
        row.addWidget(readout)
        layout.addLayout(row)
        return slider, readout

    def sync_brush(self,view):
        size = view.brushSize()
        self.size_limit = max(self.configured_max,size)
        size_tick = round(1000*log(max(.1,size)/.1)/log(self.size_limit/.1))
        opacity = round(view.paintingOpacity()*100)
        for slider,tick in ((self.size_slider,size_tick),(self.opacity_slider,opacity)):
            if not slider.isSliderDown():
                with QSignalBlocker(slider):
                    slider.setValue(tick)
        self.size_text.setText(f"{size:.1f} px" if size<10 else f"{size:.0f} px")
        self.opacity_text.setText(f"{opacity}%")

    def change_size(self,tick):
        view = self.active_view()
        if view:
            view.setBrushSize(round(.1*(self.size_limit/.1)**(tick/1000),2))
            self.sync_brush(view)

    def change_opacity(self,tick):
        view = self.active_view()
        if view:
            view.setPaintingOpacity(tick/100)
            self.sync_brush(view)

    def sync(self, view, foreground):
        if self.document != view.document() or self.expected_color is None or foreground != self.expected_color:
            self.document = view.document()
            self.expected_color = foreground
            self.colors = shade_colors(foreground)
            for index, (chip, tone) in enumerate(zip(self.chips,self.colors)):
                color = tone.colorForCanvas(view.canvas())
                # The contrasting inner border remains visible on black and white tones.
                edge = "#ffffff" if color.lightness()<125 else "#222222"
                chip.setStyleSheet("QToolButton { background: %s; color: %s; border: 1px solid palette(mid);"
                                   " border-radius: 4px; } QToolButton:checked { border: 3px solid %s; }"
                                   % (color.name(),edge,edge))
                chip.setChecked(index==2)
        self.sync_layer(view)
        self.sync_brush(view)

    def sync_layer(self, view):
        node = view.document().activeNode()
        paint = bool(node and node.type()=="paintlayer" and node.visible() and not node.locked())
        lock, clip = K.action("toggle_layer_alpha_lock"), K.action("create_quick_clipping_group")
        self.lock.setEnabled(bool(paint and lock and lock.isEnabled()))
        self.lock.setChecked(bool(node and node.type()=="paintlayer" and node.alphaLocked()))
        self.clip.setEnabled(bool(clip and clip.isEnabled() and node and node.visible() and not node.locked()))

    def active_view(self):
        window = K.activeWindow()
        view = window.activeView() if window else None
        return view if view and view.document()==self.document else None

    def choose(self, index):
        view = self.active_view()
        if view and self.colors:
            view.setForeGroundColor(self.colors[index])
            self.expected_color = view.foregroundColor()
            for i,chip in enumerate(self.chips):
                chip.setChecked(i==index)

    def layer_action(self, name):
        view = self.active_view()
        action = K.action(name)
        if view and action and action.isEnabled():
            action.trigger()
            self.sync_layer(view)

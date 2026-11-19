"""Canvas color picker. Cache the gradient and commit only on release."""
from PyQt5.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt5.QtGui import (QColor, QLinearGradient, QPainter, QPalette, QTabletEvent, QPixmap)
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget
from krita import Krita, ManagedColor


class ColorField(QWidget):
    def __init__(self, picker, hue_strip=False):
        super().__init__(picker)
        self.picker = picker
        self.hue_strip = hue_strip
        self.setFixedSize(20 if hue_strip else 196, 196)
        self.setAttribute(Qt.WA_TabletTracking)
        self.cache = None

    def paintEvent(self, event):
        p = self.picker
        key = None if self.hue_strip else p.hue
        if self.cache is None or self.cache[0] != key:
            pixmap = QPixmap(self.size())
            painter = QPainter(pixmap)
            rect = QRectF(self.rect())
            if self.hue_strip:
                gradient = QLinearGradient(0, 0, 0, self.height())
                for stop in range(7):
                    gradient.setColorAt(stop / 6, QColor.fromHsvF((stop % 6) / 6, 1, 1))
                painter.fillRect(rect, gradient)
            else:
                gradient = QLinearGradient(0, 0, self.width(), 0)
                gradient.setColorAt(0, Qt.white)
                gradient.setColorAt(1, QColor.fromHsvF(p.hue, 1, 1))
                painter.fillRect(rect, gradient)
                shade = QLinearGradient(0, 0, 0, self.height())
                shade.setColorAt(0, QColor(0, 0, 0, 0))
                shade.setColorAt(1, Qt.black)
                painter.fillRect(rect, shade)
            painter.end()
            self.cache = (key, pixmap)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.cache[1])
        if self.hue_strip:
            y = min(self.height() - 2, max(2, round(p.hue * self.height())))
            painter.setPen(Qt.black)
            painter.drawRect(0, y - 2, self.width() - 1, 4)
            painter.setPen(Qt.white)
            painter.drawLine(1, y, self.width() - 2, y)
        else:
            painter.setRenderHint(QPainter.Antialiasing)
            point = QPointF(p.saturation * self.width(), (1 - p.value) * self.height())
            painter.setPen(Qt.black)
            painter.drawEllipse(point, 6, 6)
            painter.setPen(Qt.white)
            painter.drawEllipse(point, 5, 5)

    def pick(self, pos):
        p = self.picker
        y = min(1, max(0, pos.y() / self.height()))
        if self.hue_strip:
            p.hue = min(.9999, y)
        else:
            p.saturation = min(1, max(0, pos.x() / self.width()))
            p.value = 1 - y
        p.refresh()


class ColorPicker(QWidget):
    def __init__(self, swatch, view):
        # Keep the palette in the canvas window. A separate Qt popup can steal
        # tablet focus during tip-up and leave the native cursor behind.
        super().__init__(swatch.parentWidget())
        self.setObjectName("prokrita_color_picker")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_NoMousePropagation)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAutoFillBackground(True)
        self.setCursor(Qt.ArrowCursor)
        self.setStyleSheet(
            "#prokrita_color_picker { background: palette(window); "
            "border: 1px solid palette(mid); border-radius: 8px; }"
            "#prokrita_color_title { font-weight: bold; }"
            "#prokrita_color_value { padding: 3px 6px; font-weight: bold; }"
        )
        self.swatch, self.view = swatch, view
        self.drag_tablet = None
        self.drag_field = None
        hue, self.saturation, self.value, self.alpha = swatch.color.getHsvF()
        self.hue = max(0, hue)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 10)
        layout.setSpacing(7)
        header = QHBoxLayout()
        title = QLabel("Color", self)
        title.setObjectName("prokrita_color_title")
        header.addWidget(title)
        header.addStretch()
        close = QToolButton(self)
        close.setText("×")
        close.setAccessibleName("Close color picker")
        close.setToolTip("Close color picker")
        close.clicked.connect(self.close)
        header.addWidget(close)
        layout.addLayout(header)
        fields = QHBoxLayout()
        fields.setSpacing(7)
        self.field = ColorField(self)
        self.hue_field = ColorField(self, hue_strip=True)
        self.field.setAccessibleName("Saturation and value")
        self.hue_field.setAccessibleName("Hue")
        fields.addWidget(self.field)
        fields.addWidget(self.hue_field)
        layout.addLayout(fields)
        self.label = QLabel(self)
        self.value_label = QLabel(self)
        self.value_label.setObjectName("prokrita_color_value")
        self.value_label.setAutoFillBackground(True)
        details = QHBoxLayout()
        details.addWidget(self.label)
        details.addStretch()
        details.addWidget(self.value_label)
        layout.addLayout(details)
        self.refresh()

    def owns(self, receiver):
        while isinstance(receiver, QWidget):
            if receiver is self:
                return True
            receiver = receiver.parentWidget()
        return False

    def pointer_position(self, receiver, event):
        if self.owns(receiver) or receiver is self.parentWidget():
            return self.mapFromGlobal(receiver.mapToGlobal(event.pos()))
        return self.mapFromGlobal(event.globalPos())

    def route_pointer(self, receiver, event, point):
        kind = event.type()
        tablet = isinstance(event, QTabletEvent)
        press = kind in (QEvent.TabletPress, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick)
        release = kind in (QEvent.TabletRelease, QEvent.MouseButtonRelease)
        field = next((item for item in (self.field, self.hue_field)
                      if item is receiver or item.geometry().contains(point)), None)
        on_field = field is not None
        on_background = self.rect().contains(point) and not self.owns(receiver)
        on_background = on_background or receiver in (self, self.label, self.value_label)
        if press:
            if self.drag_tablet is True and not tablet and event.source() != Qt.MouseEventNotSynthesized:
                return True
            self.drag_tablet = None
            if not on_field or event.button() != Qt.LeftButton:
                return on_field or on_background
            self.drag_tablet = tablet
            self.drag_field = field
        elif self.drag_tablet is None:
            return on_field or on_background
        elif self.drag_tablet != tablet:
            return True
        elif not release and event.buttons() == Qt.NoButton and (not tablet or event.pressure() <= 0):
            # Hover after a lost tip-up ends the interaction without applying it.
            self.drag_tablet = None
            return on_field

        # Resolve moves and tip-up against the field even after the pointer
        # crosses onto the canvas. No input from this drag reaches the brush.
        field = self.drag_field
        if receiver is field:
            point = event.posF() if tablet else event.localPos()
        else:
            point = QPointF(point - field.pos())
        field.pick(point)
        if release:
            self.drag_tablet = None
            self.commit()
        return True

    def refresh(self):
        color = self.current_color()
        self.label.setText("HSV")
        self.value_label.setText(color.name().upper())
        palette = self.value_label.palette()
        palette.setColor(QPalette.Window, color)
        palette.setColor(QPalette.WindowText, Qt.black if color.lightness() > 132 else Qt.white)
        self.value_label.setPalette(palette)
        self.field.update()
        self.hue_field.update()

    def current_color(self):
        return QColor.fromHsvF(self.hue, self.saturation, self.value, self.alpha)

    def commit(self):
        window = Krita.instance().activeWindow()
        if window and window.activeView() == self.view:
            color = self.current_color()
            self.view.setForeGroundColor(ManagedColor.fromQColor(color, self.view.canvas()))
            self.swatch.set_color(color)

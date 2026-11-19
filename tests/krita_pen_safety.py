"""In-process interaction and pixel checks. Run via run-krita.ps1 in a scratch profile."""
import math
import os
import sys
import traceback
from pathlib import Path
from xml.etree import ElementTree
from PyQt5.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer
from PyQt5.QtGui import QColor, QKeyEvent, QMouseEvent, QTabletEvent
from PyQt5.QtWidgets import QApplication, QListView, QToolButton, QWidget
from krita import Krita, ManagedColor, Preset, Selection
from prokrita.canvas_ui import BRUSH, ColorSwatch, Guide, canvas_widget, smoothing_combo
from prokrita.fill import FillPreview

app=Krita.instance()
base=Path(os.environ["PROKRITA_QA"])
def log(*msg):
    with (base/"gestures.log").open("a", encoding="utf-8") as f: print(*msg,file=f)
def wait(ms):
    loop=QEventLoop()
    QTimer.singleShot(ms,loop.quit)
    loop.exec_()
def until(fn, timeout=1600):
    for i in range(timeout//20):
        value=fn()
        if value: return value
        wait(20)
    raise AssertionError("timed out waiting for "+str(fn))
sys.excepthook=lambda *exc: log("UNHANDLED", "".join(traceback.format_exception(*exc)))

def run():
    w=app.activeWindow()
    if not w:
        QTimer.singleShot(300,run)
        return
    doc=None
    try:
        from prokrita.shapes import fit_shape
        # Intent stays stable across size, rotation, sampling speed and wobble.
        for radius in (16, 60, 180):
            for angle in (0, .47, 1.2):
                for ratio, expected in ((1., "Circle"), (.84, "Circle"), (.55, "Oval")):
                    for jitter in (0., .018):
                        points=[]
                        for i in range(241):
                            t=2*math.pi*(i/240)**1.4
                            noise=1+jitter*math.sin(13*t)
                            x,y=radius*math.cos(t)*noise, radius*ratio*math.sin(t)*noise
                            points.append((200+x*math.cos(angle)-y*math.sin(angle),
                                           200+x*math.sin(angle)+y*math.cos(angle)))
                        result=fit_shape(points)
                        assert result and result[0]==expected, (radius,angle,ratio,jitter,result)
                        if expected=="Circle":
                            center,a,b=result[1]
                            assert abs(math.dist(center,a)-math.dist(center,b))<1e-8
        log("adaptive circle/oval intent: 54 scale, rotation, sampling and wobble cases")
        doc=app.createDocument(600,420,"ProKrita gesture test","RGBA","U8","",120)
        view=w.addView(doc)
        wait(500)
        canvas=canvas_widget(w)
        swatch=until(lambda: canvas.findChild(ColorSwatch))
        history=next(q for q in w.qwindow().findChildren(QListView) if q.inherits("KisUndoView"))
        edit=swatch.edit_button
        def hinfo(): return (history.currentIndex().row(),history.currentIndex().data(),history.model().rowCount(),edit.isVisible())
        log("Krita", app.version())
        name="b) Basic-1"
        presets=app.resources("preset")
        if name in presets: view.setCurrentBrushPreset(presets[name])
        app.action(BRUSH).trigger()
        view.setBrushSize(5)
        view.setPaintingOpacity(1)
        view.setPaintingFlow(1)
        def color(hexcode):
            view.setForeGroundColor(ManagedColor.fromQColor(QColor(hexcode),view.canvas()))
        color("#20205c")
        view.canvas().setZoomLevel(1.5)
        wait(220)
        def screen(x,y):
            transform=view.flakeToImageTransform().inverted()[0]*view.flakeToCanvasTransform()
            return transform.map(QPointF(x,y)).toPoint()
        def pixel(x,y):
            b,g,r,a=bytes(doc.activeNode().pixelData(x,y,1,1))
            return (r,g,b,a)
        def near(x,y,expected,tolerance=12):
            pix=pixel(x,y)
            assert max(abs(a-b) for a,b in zip(pix,expected))<=tolerance,(x,y,pix,expected)
        white=(255,255,255,255)
        ink=(32,32,92,255)
        def event(receiver,kind,local,pen=False,routed=False,pressure=.8):
            global_pos=receiver.mapToGlobal(local)
            if routed:
                receiver=w.qwindow().windowHandle()
                local=receiver.mapFromGlobal(global_pos)
            pt=QPointF(local)
            button=Qt.NoButton if kind=="move" else Qt.LeftButton
            buttons=Qt.NoButton if kind=="up" else Qt.LeftButton
            if pen:
                typ={"down":QEvent.TabletPress,"move":QEvent.TabletMove,"up":QEvent.TabletRelease}[kind]
                e=QTabletEvent(typ,pt,QPointF(global_pos),QTabletEvent.Stylus,QTabletEvent.Pen,
                    pressure if kind!="up" else 0.,0,0,0.,0.,0,Qt.NoModifier,101,button,buttons)
            else:
                typ={"down":QEvent.MouseButtonPress,"move":QEvent.MouseMove,"up":QEvent.MouseButtonRelease}[kind]
                window_pt=local if routed else receiver.window().mapFromGlobal(global_pos)
                e=QMouseEvent(typ,pt,QPointF(window_pt),QPointF(global_pos),button,buttons,Qt.NoModifier)
            QApplication.sendEvent(receiver,e)
        def pen_leaves():
            # WinTab suppresses mouse painting while the stylus is in range.
            e=QTabletEvent(QEvent.TabletLeaveProximity,QPointF(),QPointF(),QTabletEvent.Stylus,
                           QTabletEvent.Pen,0.,0,0,0.,0.,0,Qt.NoModifier,101,Qt.NoButton,Qt.NoButton)
            QApplication.sendEvent(QApplication.instance(),e)
            wait(140)
        def draw(pts,adjust=None,lock=True,pen=False,pressures=None):
            QApplication.sendEvent(canvas,QEvent(QEvent.Enter))
            canvas.setFocus()
            if pen:
                hover=screen(*pts[0])
                for receiver,typ in ((QApplication.instance(),QEvent.TabletEnterProximity),(canvas,QEvent.TabletMove)):
                    proximity=QTabletEvent(typ,QPointF(hover),QPointF(canvas.mapToGlobal(hover)),
                        QTabletEvent.Stylus,QTabletEvent.Pen,0.,0,0,0.,0.,0,Qt.NoModifier,101,Qt.NoButton,Qt.NoButton)
                    QApplication.sendEvent(receiver,proximity)
                wait(40)
            event(canvas,"down",screen(*pts[0]),pen,pressure=pressures[0] if pressures else .8)
            for i,p in enumerate(pts[1:],1):
                event(canvas,"move",screen(*p),pen,pressure=pressures[i] if pressures else .8)
                if pressures and i%2==0:
                    wait(9)
            if lock:
                wait(550)
                guide=next((g for g in canvas.findChildren(Guide) if g.isVisible()),None)
                assert guide and guide.isVisible(),("no hold preview",hinfo())
                kind=guide.shape.kind
                if adjust:
                    event(canvas,"move",screen(*adjust),pen)
            event(canvas,"up",screen(*(adjust or pts[-1])),pen)
            wait(220)
            doc.waitForDone()
            if lock:
                if not edit.isVisible(): wait(650)
                assert edit.isVisible(),("no committed shape",kind,hinfo(),doc.activeNode().name(),
                                         doc.activeNode().bounds(),view.eraserMode(),screen(*pts[0]))
                return kind
        def click(button):
            pos=button.rect().center()
            event(button,"down",pos)
            event(button,"up",pos)
            wait(70)
        def choose(submenu,label):
            # Enter through the on-canvas options button, including Qt's popup grab.
            selected=[]
            def pick():
                if not swatch.menu.isVisible() or not submenu.isEnabled():
                    swatch.menu.close()
                    return
                action=next((a for a in submenu.actions() if a.text()==label),None)
                if action and action.isEnabled():
                    if submenu is not swatch.menu:
                        submenu.popup(swatch.menu.mapToGlobal(swatch.menu.rect().bottomLeft()))
                    pos=submenu.actionGeometry(action).center()
                    def fired():
                        selected.append(True)
                    action.triggered.connect(fired)
                    submenu.setActiveAction(action)
                    event(submenu,"down",pos)
                    event(submenu,"up",pos)
                    action.triggered.disconnect(fired)
                submenu.close()
                swatch.menu.close()
            QTimer.singleShot(90,pick)
            click(swatch.options_button)
            wait(150)
            assert selected,("menu action unavailable",label)
        def move_handle(first,last):
            event(canvas,"down",screen(*first))
            event(canvas,"move",screen(*last))
            event(canvas,"up",screen(*last))
            wait(160)
            doc.waitForDone()
        def drop(pos,pen=False,routed=True):
            point=swatch.rect().center()
            target=swatch.mapFromGlobal(canvas.mapToGlobal(pos))
            event(swatch,"down",point,pen,routed)
            event(swatch,"move",QPoint((point.x()+target.x())//2,(point.y()+target.y())//2),pen,routed)
            event(swatch,"move",target,pen,routed)
            event(swatch,"up",target,pen,routed)
            wait(220)
            doc.waitForDone()
        import prokrita.pen
        def forbidden_replay(*args, **kwargs):
            raise AssertionError("Windows shape replacement re-entered tablet input")
        prokrita.pen.PenProfile.replay = forbidden_replay
        assert draw([(70+i*3,115) for i in range(81)],pen=True)=="Line"
        assert history.currentIndex().data()==app.krita_i18nc("(qtundo-format)","Scripted Brush Stroke").split("\n")[0]
        assert all(pixel(x,115)[3]>100 for x in range(80,300)), "gaps in snapped pen line"
        pen_leaves()
        draw([(90,210),(130,235),(170,210)],lock=False)
        assert pixel(130,235)[3]>100, "mouse input broken after pen snap"
        selector = app.action("show_color_selector")
        assert selector is not None and selector.isEnabled()
        original = bytes(doc.activeNode().pixelData(0, 0, doc.width(), doc.height()))
        for pen in (False, True):
            visible = {widget for widget in QApplication.allWidgets() if widget.isVisible()}
            foreground = view.foregroundColor().colorForCanvas(view.canvas())
            center = swatch.rect().center()
            event(swatch, "down", center, pen=pen)
            event(swatch, "up", center + QPoint(0, 11), pen=pen)
            def native_selector():
                for widget in QApplication.allWidgets():
                    if widget.isVisible() and widget not in visible and widget.inherits("KisColorSelector"):
                        return widget
                return None
            picker = until(native_selector)
            log("Native selector opened with", "pen" if pen else "mouse", picker.metaObject().className())
            changed = False
            for x, y in ((.65, .55), (.4, .4), (.5, .16), (.8, .5)):
                point = QPoint(round(x*picker.width()), round(y*picker.height()))
                event(picker, "down", point, pen=pen)
                QApplication.sendEvent(picker, QEvent(QEvent.Leave))
                end = QPoint(point.x()+9, point.y()+5)
                routed_end = canvas.mapFromGlobal(picker.mapToGlobal(end))
                event(canvas, "move", routed_end, pen=pen)
                event(canvas, "up", routed_end, pen=pen)
                wait(40)
                if view.foregroundColor().colorForCanvas(view.canvas()) != foreground:
                    changed = True
                    break
                if not picker.isVisible():
                    break
            assert changed, "native selector did not update foreground"
            if picker.isVisible():
                picker.hide()
            wait(40)
        swatch.open_color_picker("square")
        square = until(swatch.visible_picker)
        event(square.field, "down", QPoint(170, 15), pen=True)
        event(square.field, "up", QPoint(170, 15), pen=True)
        before_hue = view.foregroundColor().colorForCanvas(view.canvas())
        event(square.hue_field, "down", QPoint(9, 75), pen=True)
        event(square.hue_field, "up", QPoint(9, 95), pen=True)
        assert view.foregroundColor().colorForCanvas(view.canvas()) != before_hue
        assert swatch.visible_picker() is square
        assert app.readSetting("ProKrita", "colorPickerLayout", "") == "square"
        swatch.close_color_picker()
        doc.waitForDone()
        assert bytes(doc.activeNode().pixelData(0, 0, doc.width(), doc.height())) == original
        assert QApplication.overrideCursor() is None
        pen_leaves()
        draw([(260,325),(280,330),(300,325)],lock=False)
        assert pixel(280,330)[3]>100, "brush did not resume after native picker"
        log("PASS")
    except BaseException:
        log("FAILED",traceback.format_exc())
    finally:
        if doc: doc.setModified(False)
        QTimer.singleShot(20,w.qwindow().close)
QTimer.singleShot(1000,run)

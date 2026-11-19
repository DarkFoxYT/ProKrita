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
        draw([(30,35),(45,56),(60,35)],lock=False)
        near(45,56,ink)
        log("freehand preserved",hinfo())
        draw([(70,70),(95,73),(130,70)],(150,110))
        near(110,90,ink)
        near(95,73,white)
        near(130,70,white)
        log("held endpoint, original and handle-travel pixels cleared",hinfo())
        click(edit)
        assert edit.text()=="Done" and edit.isVisible(),("edit activation",hinfo())
        move_handle((70,70),(50,130))
        near(100,120,ink)
        near(70,70,white)
        assert edit.text()=="Done" and edit.isVisible(),("edit lost",hinfo())
        click(edit)
        log("opposite endpoint edit",hinfo())
        circ=lambda cx,cy,rx,ry: [(cx+rx*math.cos(i*math.pi/40),cy+ry*math.sin(i*math.pi/40)) for i in range(81)]
        draw(circ(260,105,27,27),(302,105))
        near(260,63,ink)
        near(260,78,white)
        click(edit)
        move_handle((260,105),(295,135))
        near(295,93,ink)
        near(260,63,white)
        click(edit)
        log("held circle radius and center move",hinfo())
        draw([(40,220),(140,220),(140,290),(40,290),(40,221)])
        click(edit)
        move_handle((140,290),(170,320))
        near(170,267,ink)
        near(140,267,white)
        near(95,220,ink)
        click(edit)
        log("rectangle fixed opposite corner",hinfo())
        draw([(240,250),(280,330),(200,330),(239,251),(240,250)])
        click(edit)
        move_handle((240,250),(260,235))
        near(260,235,ink)
        near(240,250,white)
        click(edit)
        log("triangle vertex edit",hinfo())
        draw(circ(433,150,48,22),(433,210))
        near(433,90,ink,35)
        near(433,128,white)
        log("rotated and resized oval",hinfo())
        # The two controls are real canvas children and the popup is the native chooser.
        click(swatch.brush_button)
        popup=QApplication.activePopupWidget()
        log("native brush popup", popup.metaObject().className() if popup else None)
        assert popup is not None,"brush preset popup did not open"
        popup.hide()
        canvas.setFocus()
        wait(80)
        preset=view.currentBrushPreset().name()
        color("#12b6a0")
        drop(screen(110,275))

        near(110,275,(18,182,160,255),2)
        near(180,275,white)
        near(170,267,ink)
        brush=w.qwindow().findChild(QToolButton,BRUSH)
        assert brush.isChecked() and view.currentBrushPreset().name()==preset, "drop changed the brush"
        near(50,230,(18,182,160,255),2)
        assert history.currentIndex().data()==app.krita_i18nc("(qtundo-format)","Flood Fill Layer").split("\n")[0],("not a fill",hinfo())
        log("window-routed color drop, distant interior, boundary, current brush",hinfo())
        app.action("edit_undo").trigger()
        wait(180)
        near(110,275,white)
        near(170,267,ink)
        app.action("edit_redo").trigger()
        wait(180)

        near(110,275,(18,182,160,255),2)
        log("one-step fill undo and redo")
        color("#e46939")
        outside=canvas.mapFromGlobal(swatch.mapToGlobal(QPoint(swatch.width()+5000,swatch.height()+5000)))
        before=hinfo()
        drop(outside)

        assert hinfo()==before,("outside drop edited document",hinfo(),before)
        log("out-of-canvas cancellation")
        # Zoom/rotate/mirror changes the screen target but not the document-space seed.
        view.canvas().setRotation(24)
        view.canvas().setMirror(True)
        wait(250)
        drop(screen(295,135),pen=True)
        near(295,135,(228,105,57,255),2)
        near(242,135,white)
        log("tablet event color drop on rotated mirrored canvas",hinfo())
        view.canvas().setMirror(False)
        view.canvas().resetRotation()
        wait(200)
        # Exercise a pen stroke distinct from the tablet event used for color dragging.
        color("#20205c")
        draw([(355,300),(385,303),(420,300)],(440,360),pen=True)
        near(397,330,ink,22)
        near(385,303,white)
        pen_leaves()
        log("tablet brush hold and moved endpoint",hinfo())
        # Filling on a transparent color layer uses the visible ink as its boundary.
        fill_group="KritaFill/KisToolFill"
        app.writeSetting(fill_group,"sampleLayersMode","allLayers")
        app.writeSetting(fill_group,"whatToFill","fillSelection")
        base_ink=doc.activeNode()
        colors=doc.createNode("Color","paintlayer")
        doc.rootNode().addChildNode(colors,base_ink)
        doc.setActiveNode(colors)
        wait(180)
        color("#2473db")
        view.setEraserMode(True)
        assert view.eraserMode()
        drop(screen(295,135))
        near(295,135,(36,115,219,255),2)
        assert pixel(245,135)[3]==0
        assert view.eraserMode() and app.readSetting(fill_group,"whatToFill","")=="fillSelection"
        log("separate ink/color layers and eraser restoration")
        view.setEraserMode(False)
        app.writeSetting(fill_group,"sampleLayersMode","currentLayer")
        app.writeSetting(fill_group,"whatToFill","fillContiguousRegion")
        def blank(title):
            n=doc.createNode(title,"paintlayer")
            doc.rootNode().addChildNode(n,doc.activeNode())
            doc.setActiveNode(n)
            doc.refreshProjection()
            wait(150)
            return n
        def active_preview():
            return next((p for p in canvas.findChildren(FillPreview) if p.isVisible()),None)
        def gesture_at(kind,pos):
            event(swatch,kind,swatch.mapFromGlobal(canvas.mapToGlobal(pos)),routed=True)
        def hold_color(x,y):
            event(swatch,"down",swatch.rect().center(),routed=True)
            pos=screen(x,y)
            gesture_at("move",pos)
            preview=until(lambda: active_preview())
            until(lambda: not preview.mask.isNull())
            return pos,preview
        def mask_at(preview,x,y):
            return preview.mask.pixelIndex(int(x*preview.w/doc.width()),int(y*preview.h/doc.height()))
        bands=blank("Threshold bands")
        bands.setPixelData((bytes([90,90,90,255])*60+bytes([147,147,147,255])*75)*85,90,150,135,85)
        doc.refreshProjection()
        wait(160)
        color("#e84668")
        seed,preview=hold_color(115,190)
        initial=preview.value
        assert mask_at(preview,115,190)==255 and mask_at(preview,180,190)==0,("initial preview",initial)
        high=seed+QPoint(120,0)
        gesture_at("move",high)
        until(lambda: preview.value>initial+20)
        assert mask_at(preview,180,190)==255,("expanded preview",preview.value)
        near(115,190,(90,90,90,255),0)
        near(180,190,(147,147,147,255),0)
        gesture_at("move",seed)
        until(lambda: preview.value==initial)
        assert mask_at(preview,180,190)==0
        QApplication.sendEvent(canvas,QKeyEvent(QEvent.KeyPress,Qt.Key_Escape,Qt.NoModifier))
        QApplication.sendEvent(canvas,QKeyEvent(QEvent.KeyRelease,Qt.Key_Escape,Qt.NoModifier))
        gesture_at("up",seed)
        wait(100)
        assert not active_preview()
        near(115,190,(90,90,90,255),0)
        seed,preview=hold_color(115,190)
        gesture_at("move",high)
        until(lambda: preview.value>initial+20)
        chosen=preview.value
        gesture_at("up",high)
        wait(220)
        near(115,190,(232,70,104,255),2)
        near(180,190,(232,70,104,255),2)
        assert int(app.readSetting("ProKrita","threshold","0"))==chosen
        assert app.readSetting(fill_group,"thresholdAmount","8")=="8"
        app.action("edit_undo").trigger()
        wait(180)
        near(115,190,(90,90,90,255),0)
        near(180,190,(147,147,147,255),0)
        seed,preview=hold_color(180,190)
        assert preview.value==chosen
        low=seed-QPoint(155,0)
        gesture_at("move",low)
        until(lambda: preview.value<initial)
        gesture_at("up",low)
        wait(200)
        near(115,190,(90,90,90,255),0)
        near(180,190,(232,70,104,255),2)
        log("anchored threshold preview grew and shrank, Escape, remembered value, one Undo; seed held across boundary",initial,chosen)
        app.writeSetting("ProKrita","threshold","8")
        lineart=blank("Transparent ink")
        # Opaque ink with a one-pixel break and a half-transparent inner fringe.
        size=82
        buf=bytearray(size*size*4)
        for y in range(8,74):
            for x in range(8,74):
                edge=min(x-8,73-x,y-8,73-y)
                a=255 if edge<3 else (115 if edge==3 else 0)
                if x==40 and y<13: a=0
                buf[(y*size+x)*4+3]=a
        lineart.setPixelData(bytes(buf),80,140,size,size)
        doc.refreshProjection()
        wait(140)
        color("#ed902a")
        drop(screen(120,180))
        near(120,180,(237,144,42,255),2)
        assert pixel(95,146)[3]==0 and pixel(140,144)[3]==0,("fill escaped a pinhole",pixel(95,146))
        assert pixel(89,180)==(0,0,0,255)
        assert pixel(91,180)[3]>=248,("translucent seam",pixel(91,180))
        log("transparent antialiased ink sealed, opaque ink retained, 1px break contained",pixel(91,180))
        artwork=blank("Shape families")
        color("#20205c")
        def sine(i): return (55+3*i,125+18*math.sin(i*math.pi/15))
        semicircle=[(110+42*math.cos(i*math.pi/60),78-42*math.sin(i*math.pi/60)) for i in range(61)]
        assert draw(semicircle)=="Arc"
        near(110,36,ink,30)
        assert pixel(110,78)[3]==0
        assert draw([sine(i) for i in range(61)])=="Curve"
        near(78,143,ink,45)
        assert draw([(55,245),(79,211),(108,247),(142,210),(172,240)])=="Polyline"
        near(108,247,ink,40)
        trapezoid=[(225,200),(300,200),(325,268),(207,268),(225,200)]
        assert draw(trapezoid)=="Quadrilateral"
        near(313,235,ink,45)
        log("semicircle, smooth S curves, cornered polyline and trapezoid classification and pixels")
        # Select the inside with the actual menu action; painting and a color drop obey the mask.
        choose(swatch.shape_menu,"New selection")
        wait(120)
        sel=doc.selection()
        assert sel and bytes(sel.pixelData(245,230,1,1))==b"\xff"
        assert bytes(sel.pixelData(190,230,1,1))==b"\x00"
        app.action("edit_undo").trigger()
        wait(120)
        assert doc.selection() is None or not any(bytes(doc.selection().pixelData(245,230,1,1)))
        app.action("edit_redo").trigger()
        wait(130)
        color("#4393ce")
        drop(screen(252,232))
        near(252,232,(67,147,206,255),3)
        assert pixel(190,230)[3]==0
        app.action("deselect").trigger()
        wait(140)
        sm=next(a.menu() for a in swatch.menu.actions() if a.text()=="Pen smoothing")
        choose(sm,"Weighted")
        combo=smoothing_combo()
        assert combo and combo.currentIndex()==2
        sm.actions()[1].trigger()
        assert combo.currentIndex()==1
        log("shape selection, selection Undo/Redo, clipped fill, native weighted and basic smoothing")
        rotated=[(395+48*math.cos(t)*.8-17*math.sin(t)*.6,110+48*math.cos(t)*.6+17*math.sin(t)*.8)
                 for t in (i*math.pi/40 for i in range(81))]
        assert draw(rotated)=="Oval"
        near(433,139,(67,147,206,255),40)
        choose(swatch.refine_menu,"Circle")
        guide=next((g for g in canvas.findChildren(Guide) if g.isVisible()),None)
        assert guide and guide.shape.kind=="Circle",(hinfo(),edit.text(),[(g.shape.kind,g.isVisible()) for g in canvas.findChildren(Guide)])
        click(edit)
        rect=[(335,200),(435,200),(435,261),(335,261),(335,200)]
        assert draw(rect)=="Rectangle"
        choose(swatch.refine_menu,"Square")
        guide=next((g for g in canvas.findChildren(Guide) if g.isVisible()),None)
        bounds=guide.shape.path().boundingRect()
        assert guide.shape.kind=="Square" and abs(bounds.width()-bounds.height())<1
        click(edit)
        mask=Selection()
        mask.select(310,170,60,110,255)
        doc.setSelection(mask)
        wait(120)
        assert draw(rect)=="Rectangle"
        choose(swatch.shape_menu,"Add to selection")
        sel=doc.selection()
        assert bytes(sel.pixelData(410,230,1,1))==b"\xff" and bytes(sel.pixelData(320,185,1,1))==b"\xff"
        assert draw(rect)=="Rectangle"
        choose(swatch.shape_menu,"Subtract from selection")
        sel=doc.selection()
        assert bytes(sel.pixelData(410,230,1,1))==b"\x00" and bytes(sel.pixelData(320,185,1,1))==b"\xff"
        app.action("deselect").trigger()
        wait(150)
        log("rotated ellipse, exact circle and square refinements, real menu selection add and subtract")
        pen_layer=blank("Pen pressure")
        pen_name="b) Basic-5 Size"
        assert pen_name in presets, ("size-sensitive presets",[n for n in presets if "Basic" in n])
        view.setCurrentBrushPreset(presets[pen_name])
        view.setBrushSize(30)
        view.setPaintingOpacity(1)
        color("#20205c")
        pts=[(70+i*3,115) for i in range(81)]
        pressure=[.38+.62*math.sin(math.pi*i/80)**4 for i in range(81)]
        draw([(x,64) for x,y in pts],pen=True,pressures=pressure,lock=False)
        assert draw(pts,pen=True,pressures=pressure)=="Line"
        def thickness(x,y,r=23):
            return sum(pixel(x,j)[3]>80 for j in range(y-r,y+r+1))
        widths=[thickness(x,115) for x in (91,190,289)]
        native_widths=[thickness(x,64) for x in (91,190,289)]
        if sys.platform != "win32":
            assert max(abs(a-b) for a,b in zip(widths,native_widths))<=4,(widths,native_widths,pen_layer.bounds(),screen(70,64))
            assert widths[1]>=max(widths[0],widths[2])+7,("interior pressure profile lost",widths)
        else:
            assert min(widths)>0 and max(widths)-min(widths)<=3, widths
        click(edit)
        move_handle((190,115),(190,148))
        widths2=[thickness(x,148) for x in (91,190,289)]
        assert pixel(190,115)[3]==0,(widths,widths2)
        assert (widths2[1]>=max(widths2[0],widths2[2])+7 if sys.platform!="win32" else min(widths2)>0),widths2
        click(edit)
        draw([(425,310),(450,326),(478,310)],lock=False)
        assert pixel(450,326)[3]>140,"mouse brush after editing a tablet stroke"
        loop=circ(330,225,47,47)
        # Reverse the drawing direction and start at the top, away from the canonical east handle.
        loop=loop[60:80]+loop[:61]
        loop=list(reversed(loop))
        pressure=[.2+.8*math.sin(math.pi*i/80)**6 for i in range(81)]
        assert draw(loop,pen=True,pressures=pressure)=="Circle"
        top,bottom=thickness(330,178,16),thickness(330,272,16)
        assert (bottom>=top+7 if sys.platform!="win32" else min(top,bottom)>0),("closed pressure registration",top,bottom)
        app.action("edit_undo").trigger()
        wait(180)
        assert pixel(330,272)[3]==0 and pixel(190,148)[3]>150
        log("native, snapped and edited widths; reversed circle with pressure opposite its seam; one Undo",native_widths,widths,widths2,top,bottom)
        choose(sm,"Stabilizer")
        assert combo.currentIndex()==3
        assert draw([(x,205) for x,y in pts],pen=True,pressures=pressure)=="Line"
        assert combo.currentIndex()==3
        assert (thickness(190,205)>thickness(91,205)+7 if sys.platform!="win32" else thickness(190,205)>0)
        choose(sm,"Basic")
        selections=next(a.menu() for a in swatch.menu.actions() if a.text()=="Selection tools")
        choose(selections,"Freehand lasso")
        lasso=w.qwindow().findChild(QToolButton,"KisToolSelectOutline")
        assert lasso and lasso.isChecked()
        log("stabilized pen snap with pressure, smoother restored, native freehand lasso switched on")
        # Color flats, an anchored tap gesture and separate shading on real paint layers.
        from prokrita.shading import shade_colors
        app.action(BRUSH).trigger()
        view.setCurrentBrushPreset(presets["b) Basic-1"])
        view.setEraserMode(False)
        view.setPaintingOpacity(1)
        view.setBrushSize(12)
        app.writeSetting("ProKrita","threshold","8")
        app.writeSetting("ProKrita","visibleInk","false")
        app.writeSetting("KritaFill/KisToolFill","sampleLayersMode","currentLayer")
        flats=blank("Color flats")
        gray=bytes([90,90,90,255])
        pale=bytes([146,146,146,255])
        for x in (65,145):
            flats.setPixelData(gray*55*65,x,147,55,65)
        flats.setPixelData((gray*65+pale*65)*65,236,147,130,65)
        doc.refreshProjection()
        wait(160)
        color("#eb7053")
        drop(screen(90,177))
        repeat=swatch.repeat_button
        assert repeat.isVisible() and repeat.text()=="Continue filling"
        click(repeat)
        assert repeat.isVisible() and repeat.text()=="Done filling"
        def tap_region(x,y,pen=False):
            point=screen(x,y)
            event(canvas,"down",point,pen,routed=True)
            event(canvas,"up",point,pen,routed=True)
            doc.waitForDone()
            wait(120)
        color("#587aca")
        tap_region(165,177,pen=True)
        pen_leaves()
        near(90,177,(235,112,83,255),2)
        near(165,177,(88,122,202,255),2)
        assert pixel(132,177)[3]==0
        app.action("edit_undo").trigger()
        wait(160)
        near(165,177,(90,90,90,255),0)
        near(90,177,(235,112,83,255),2)
        # Read the actual overlay before releasing the tap on a different band.
        pos=screen(255,177)
        event(canvas,"down",pos,routed=True)
        preview=until(lambda: next((p for p in canvas.findChildren(FillPreview) if p.isVisible()),None))
        until(lambda: not preview.mask.isNull())
        event(canvas,"move",pos+QPoint(118,0),routed=True)
        until(lambda: preview.value>30)
        near(320,177,(146,146,146,255),0)
        event(canvas,"up",pos+QPoint(118,0),routed=True)
        wait(160)
        near(255,177,(88,122,202,255),2)
        near(320,177,(88,122,202,255),2)
        click(repeat)
        assert not repeat.isVisible()
        draw([(90,266),(115,276),(138,264)],lock=False)
        near(115,276,(88,122,202,255),10)
        drop(screen(165,177))
        click(repeat)
        base_flat=blank("Base flats")
        until(lambda: not repeat.isVisible())
        assert base_flat.bounds().isEmpty()
        log("Continue Filling via mouse and tablet, current color, isolated Undo, hold threshold, Done, brush and layer switch")
        panel=swatch.shading
        if panel.isVisible():
            choose(swatch.menu,"Shading palette")
        button_center=swatch.rect().center()
        event(swatch,"down",button_center,routed=True)
        wait(620)
        event(swatch,"up",button_center,routed=True)
        until(panel.isVisible)
        assert swatch.shading_action.isChecked()
        source=ManagedColor("RGBA","U16","")
        source.setComponents([.16,.39,.75,.643])
        def lightness(c):
            sample=ManagedColor(c.colorModel(),c.colorDepth(),c.colorProfile())
            sample.setComponents(c.components())
            assert sample.setColorSpace("LABA","U16","")
            return sample.components()[0]
        tones=shade_colors(source)
        values=[lightness(t) for t in tones]
        assert all(b-a>.045 for a,b in zip(values,values[1:])),values
        assert all(t.colorDepth()=="U16" and t.colorProfile()==source.colorProfile()
                   and abs(t.components()[-1]-source.components()[-1])<.00002 for t in tones)
        view.setForeGroundColor(source)
        wait(360)
        original=view.foregroundColor()
        displayed=list(panel.colors)
        for i in (0,4,1,2):
            click(panel.chips[i])
            wait(280)
            assert panel.chips[i].isChecked() and panel.colors[2]==original,(i,original.toXML())
            assert abs(lightness(view.foregroundColor())-lightness(displayed[i]))<.012
        assert view.foregroundColor()==original
        color("#cd7757")
        until(lambda: panel.colors[2]==view.foregroundColor())
        assert panel.chips[2].isChecked()
        log("five ordered profile-aware U16 shading tones, preserved alpha and exact base, external color rebase",values)
        def scrub(slider,fraction,pen=False):
            pt=QPoint(round((slider.width()-1)*fraction),slider.height()//2)
            event(slider,"down",pt,pen,routed=True)
            event(slider,"up",pt,pen,routed=True)
            wait(100)
        scrub(panel.size_slider,.62)
        assert 13<view.brushSize()<65,(view.brushSize(),panel.size_slider.width())
        scrub(panel.opacity_slider,.50,pen=True)
        pen_leaves()
        assert .46<view.paintingOpacity()<.54,view.paintingOpacity()
        assert base_flat.bounds().isEmpty(),"a pen scrub left ink on the layer"
        app.action(BRUSH).trigger()
        draw([(83,257),(103,257),(120,257)],lock=False)
        assert 95<pixel(102,257)[3]<160,pixel(102,257)
        view.setPaintingOpacity(1)
        view.setBrushSize(12)
        until(lambda: panel.opacity_slider.value()==100 and panel.size_text.text()=="12 px")
        log("on-canvas brush size, pen opacity and native brush alpha, synchronized external controls")
        base_flat.setPixelData((bytes([87,119,205,96])+bytes([87,119,205,255])*99)*68,188,145,100,68)
        doc.refreshProjection()
        wait(240)
        click(panel.lock)
        assert base_flat.alphaLocked() and panel.lock.isChecked()
        click(panel.chips[0])
        draw([(162,172),(200,172),(232,172)],lock=False)
        assert pixel(174,172)[3]==0 and pixel(188,172)[3]==96
        assert pixel(217,172)!=(205,119,87,255) and pixel(217,172)[3]==255
        click(panel.lock)
        assert not base_flat.alphaLocked()
        base_data=bytes(base_flat.pixelData(175,140,125,84))
        def projected(x,y): return doc.projection(x,y,1,1).pixelColor(0,0).getRgb()
        original_parent=base_flat.parentNode().uniqueId()
        before_out=projected(172,191)
        before_in=projected(216,191)
        click(panel.clip)
        shade=until(lambda: doc.activeNode() if doc.activeNode().inheritAlpha() and doc.activeNode().type()=="paintlayer" else None)
        assert shade.parentNode().uniqueId()==base_flat.parentNode().uniqueId()
        assert shade.parentNode().uniqueId()!=original_parent
        click(panel.chips[4])
        draw([(162,191),(198,191),(250,191)],lock=False)
        assert pixel(172,191)[3]>200 and projected(172,191)==before_out
        assert projected(216,191)!=before_in and base_data==bytes(base_flat.pixelData(175,140,125,84))
        app.action("edit_undo").trigger()
        wait(170)
        assert projected(216,191)==before_in
        app.action("edit_undo").trigger()
        wait(180)
        assert base_flat.parentNode().uniqueId()==original_parent
        choose(swatch.menu,"Shading palette")
        assert not panel.isVisible() and not swatch.shading_action.isChecked()
        log("alpha lock kept transparent and partial-alpha pixels, clipped paint respected silhouette, original flat bytes and native Undo")
        # Quick smudge, through the on-canvas button and the native brush input path.
        mixed = blank("Smudge shadows and highlights")
        pen_leaves()
        app.action(BRUSH).trigger()
        drawing = presets["b) Basic-5 Size"]
        view.setCurrentBrushPreset(drawing)
        view.setBrushSize(13.75)
        view.setPaintingOpacity(.62)
        view.setPaintingFlow(.46)
        view.setBrushRotation(31.)
        view.setCurrentBlendingMode("multiply")
        color("#00ff00")
        def brush_settings():
            return (view.brushSize(), view.paintingOpacity(), view.paintingFlow(), view.brushRotation(),
                    view.patternSize(), view.currentBlendingMode(), view.eraserMode())
        original_brush = brush_settings()
        fg = view.foregroundColor()
        row = bytes([24,24,24,255])*75 + bytes([222,222,222,255])*85
        mixed.setPixelData(row*84, 180, 155, 160, 84)
        doc.refreshProjection()
        wait(140)
        def painted():
            return bytes(mixed.pixelData(160, 140, 200, 110))
        before_mix = painted()
        smudge = swatch.smudge_button
        click(smudge)
        assert smudge.isChecked() and smudge.text()=="Paint",view.currentBrushPreset().name()
        blender = view.currentBrushPreset()
        assert blender != drawing and view.currentBlendingMode()=="normal" and not view.eraserMode()
        view.setBrushSize(48)
        view.setPaintingOpacity(.9)
        blender_size, blender_opacity = view.brushSize(), view.paintingOpacity()
        QApplication.sendEvent(canvas, QEvent(QEvent.Enter))
        canvas.setFocus()
        event(canvas,"down",screen(225,185))
        for x in range(227,289,2):
            event(canvas,"move",screen(x,185))
            if x%8==3: wait(8)
        wait(550)
        assert not any(g.isVisible() for g in canvas.findChildren(Guide))
        event(canvas,"up",screen(289,185))
        until(lambda: smudge.text()=="Smudge")
        doc.waitForDone()
        samples = [pixel(x,185) for x in (248,254,256,260,266,272,280,286)]
        assert any(26<p[0]<218 for p in samples[2:]),samples
        assert all(max(p[:3])-min(p[:3])<=2 and p[3]==255 for p in samples),samples
        assert view.currentBrushPreset()==drawing and brush_settings()==original_brush,(brush_settings(),original_brush)
        assert view.foregroundColor()==fg
        app.action("edit_undo").trigger()
        wait(180)
        doc.waitForDone()
        assert painted()==before_mix
        draw([(208,250),(228,250),(250,250)],lock=False)
        p=pixel(228,250)
        assert p[1]>200 and p[0]<10 and p[2]<10 and 0<p[3]<230,p
        log("one native smudge blended grayscale values, no foreground pigment, one Undo, immediate original drawing brush", samples)
        app.action("edit_undo").trigger()
        wait(120)
        # Hold with the pen to blend across several strokes; selection and alpha lock remain native.
        mixed.setAlphaLocked(True)
        mask = Selection()
        mask.select(245,188,28,52,255)
        doc.setSelection(mask)
        wait(100)
        center = smudge.rect().center()
        event(smudge,"down",center,pen=True,routed=True)
        until(lambda: smudge.text()=="Keep on")
        event(smudge,"up",center,pen=True,routed=True)
        until(smudge.isChecked)
        assert view.currentBrushPreset()==blender and brush_settings()[:2]==(blender_size,blender_opacity)
        for y in (202,224):
            draw([(x,y) for x in range(226,292,2)],lock=False,pen=True)
            assert smudge.isChecked() and view.currentBrushPreset()==blender
            assert 24<pixel(260,y)[0]<222,(y,pixel(260,y))
            near(276,y,(222,222,222,255),0)
        near(260,240,(0,0,0,0),0)
        event(smudge,"down",center,pen=True,routed=True)
        event(smudge,"up",center,pen=True,routed=True)
        until(lambda: not smudge.isChecked())
        pen_leaves()
        assert view.currentBrushPreset()==drawing and brush_settings()==original_brush
        app.action("deselect").trigger()
        mixed.setAlphaLocked(False)
        view.setEraserMode(True)
        eraser_settings=brush_settings()
        click(smudge)
        assert not view.eraserMode() and smudge.isChecked()
        QApplication.sendEvent(canvas,QKeyEvent(QEvent.KeyPress,Qt.Key_Escape,Qt.NoModifier))
        assert brush_settings()==eraser_settings and not smudge.isChecked(),(eraser_settings,brush_settings())
        view.setEraserMode(False)
        click(smudge)
        chosen = presets["b) Basic-1"]
        view.setCurrentBrushPreset(chosen)
        until(lambda: not smudge.isChecked())
        assert view.currentBrushPreset()==chosen
        click(smudge)
        app.action("KisToolSelectOutline").trigger()
        until(lambda: not smudge.isChecked())
        assert view.currentBrushPreset()==chosen and w.qwindow().findChild(QToolButton,"KisToolSelectOutline").isChecked()
        log("pen hold and tap, repeated pressure strokes, selection, transparent pixels, independent smudge settings, eraser/Escape and manual tool/preset changes")
        app.action(BRUSH).trigger()
        view.setCurrentBrushPreset(drawing)
        shadow = blank("Shadow original")
        shadow.setPixelData(bytes([24,24,24,255])*80*60,180,150,80,60)
        light = blank("Highlight original")
        light.setPixelData(bytes([222,222,222,255])*80*60,260,150,80,60)
        doc.refreshProjection()
        doc.waitForDone()
        sources = [bytes(n.pixelData(180,150,160,60)) for n in (shadow,light)]
        choose(swatch.menu,"Smudge visible layers on a new layer")
        assert doc.activeNode().name()=="Smudge blend" and smudge.isChecked(),doc.activeNode().name()
        assert doc.activeNode().bounds().isEmpty()
        pen_leaves()
        view.setBrushSize(48)
        view.setPaintingOpacity(.9)
        draw([(x,180) for x in range(227,294,2)],lock=False,pen=True)
        assert pixel(315,161)[3]==0 and pixel(265,180)[3]>0
        light.setPixelData(bytes([32,98,166,255]),315,161,1,1)
        doc.refreshProjection()
        doc.waitForDone()
        assert doc.projection(315,161,1,1).pixelColor(0,0).red()==166
        light.setPixelData(bytes([222,222,222,255]),315,161,1,1)
        samples=[pixel(x,180)[0] for x in range(260,290,2)]
        assert any(24<v<222 for v in samples),samples
        assert sources==[bytes(n.pixelData(180,150,160,60)) for n in (shadow,light)]
        click(smudge)
        assert view.currentBrushPreset()==drawing
        log("two source layers preserved byte-for-byte; transparent layer sampled visible colors and exposed live underlying edits and drawing brush restored")
        pen_leaves()
        # An unsaved brush editor setting lives in the preset, outside the toolbar values.
        engine = Preset(view.currentBrushPreset())
        xml = ElementTree.fromstring(engine.toXML())
        pressure = "param[@name='PressureSize']"
        xml.find(pressure).text = "false"
        engine.fromXML(ElementTree.tostring(xml,encoding="unicode"))
        assert ElementTree.fromstring(engine.toXML()).findtext(pressure)=="false"
        event(smudge,"down",center,pen=True,routed=True)
        event(smudge,"up",center,pen=True,routed=True)
        until(smudge.isChecked)
        draw([(x,176) for x in range(226,292,2)],lock=False,pen=True)
        until(lambda: not smudge.isChecked())
        assert 24<pixel(260,176)[0]<222 and view.currentBrushPreset()==drawing
        assert ElementTree.fromstring(Preset(view.currentBrushPreset()).toXML()).findtext(pressure)=="false"
        pen_leaves()
        click(smudge)
        other = app.createDocument(64,64,"Smudge tab switch","RGBA","U8","",120)
        second = w.addView(other)
        until(lambda: not smudge.isChecked())
        assert second.currentBrushPreset()==drawing
        assert ElementTree.fromstring(Preset(second.currentBrushPreset()).toXML()).findtext(pressure)=="false"
        second_canvas = canvas_widget(w)
        second_ui = until(lambda: second_canvas.findChild(ColorSwatch))
        click(second_ui.smudge_button)
        assert second_ui.smudge_button.isChecked()
        other.setModified(False)
        app.action("file_close").trigger()
        until(lambda: w.activeView()==view and swatch.isVisible())
        assert view.currentBrushPreset()==drawing
        log("single pen stroke returned to unsaved pressure settings; switching and closing documents restored the original brush")
        log("PASS")
    except BaseException:
        log("FAILED",traceback.format_exc())
    finally:
        if doc: doc.setModified(False)
        # Close the window normally so Krita tears down its documents before Qt exits.
        QTimer.singleShot(20,w.qwindow().close)
QTimer.singleShot(1000,run)

// SPDX-License-Identifier: GPL-3.0-or-later
#include "prokrita_canvas_widgets.h"
#include <QTreeWidget>
#include <QTest>
#include <iostream>

int main(int argc,char **argv) {
    QApplication app(argc,argv);
    auto require=[](bool value,const char *message){if(!value)qFatal("%s",message);};
    QMainWindow window;window.resize(1200,900);
    auto *canvas=new QWidget;canvas->setStyleSheet("background:white;");window.setCentralWidget(canvas);
    auto *dock=new QDockWidget("Layers",&window);dock->setObjectName("KisLayerBox");
    QPointer<QDockWidget> nativeGuard(dock);
    auto *originalTitle=new QLabel("Original native title",dock);dock->setTitleBarWidget(originalTitle);
    auto *tree=new QTreeWidget(dock);tree->setHeaderHidden(true);
    new QTreeWidgetItem(tree,QStringList{"Ink"});new QTreeWidgetItem(tree,QStringList{"Colors"});new QTreeWidgetItem(tree,QStringList{"Background"});
    dock->setWidget(tree);window.addDockWidget(Qt::RightDockWidgetArea,dock);window.show();QTest::qWait(30);dock->hide();QTest::qWait(10);
    const auto originalFeatures=dock->features();
    auto *panel=new ProKrita::AnchoredLayersPanel(canvas,&window,dock,64,0.);
    QPointer<ProKrita::AnchoredLayersPanel> guarded(panel);panel->reveal(true);QTest::qWait(190);
    require(panel->x()+panel->width()==canvas->width()-12,"Layers lost right anchor");
    require(dock->isVisible() && dock->widget()==tree,"Native layer controls lost");
    // LayerGrip has no Q_OBJECT: find it through the known header geometry.
    auto *grip=static_cast<ProKrita::LayerGrip*>(panel->childAt(30,25));
    require(grip,"Missing drag grip");
    qreal dragged=-1.;panel->positionChanged=[&](qreal ratio){dragged=ratio;};
    QTest::mousePress(grip,Qt::LeftButton,Qt::NoModifier,QPoint(45,26));
    QMouseEvent move(QEvent::MouseMove,QPointF(45,100),Qt::NoButton,Qt::LeftButton,Qt::NoModifier);QApplication::sendEvent(grip,&move);
    QTest::mouseRelease(grip,Qt::LeftButton,Qt::NoModifier,QPoint(45,26));
    require(dragged>0. && panel->x()+panel->width()==canvas->width()-12,"Drag detached the Layers panel");
    panel->relayout(80);require(panel->y()>=80 && panel->geometry().bottom()<canvas->height(),"Layer panel escaped canvas");
    panel->dismiss(true);QTest::qWait(160);QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);
    require(!guarded,"Closing animation retained panel");
    require(nativeGuard,"Closing deleted the borrowed Layers docker");
    require(dock->parentWidget()==&window && dock->widget()==tree && !dock->isVisible(),"Closing lost native Layers docker");
    require(dock->features()==originalFeatures,"Native Layers features not restored");
    require(dock->titleBarWidget()==originalTitle,"Original dock title was lost");
    // Resizing during a close must still restore ownership exactly once.
    panel=new ProKrita::AnchoredLayersPanel(canvas,&window,dock,64,.5);panel->reveal(false);panel->dismiss(true);panel->relayout(80);QTest::qWait(20);QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);
    require(dock->parentWidget()==&window && dock->widget()==tree,"Resize during dismissal lost Layers");

    ProKrita::QuickRadialMenu radial(canvas);int selected=-1;
    const QStringList labels{"Undo","Redo","Eraser","Brushes","Flip","Layers"};
    for(int i=0;i<6;++i)radial.items[i]={labels[i],QIcon(),true};
    radial.chosen=[&](int index){selected=index;};
    for(int i=0;i<6;++i){radial.show();QTest::mouseClick(&radial,Qt::LeftButton,Qt::NoModifier,radial.itemCenter(i).toPoint());require(selected==i,"Wrong radial action");require(!radial.isVisible(),"Radial did not close");}
    radial.items[2].enabled=false;selected=-1;radial.show();QTest::mouseClick(&radial,Qt::LeftButton,Qt::NoModifier,radial.itemCenter(2).toPoint());require(selected==-1,"Disabled radial action fired");
    auto *touchDevice=QTest::createTouchDevice(QTouchDevice::TouchScreen);
    radial.show();selected=-1;
    QTouchEvent::TouchPoint finger(0);finger.setPos(radial.itemCenter(0));finger.setState(Qt::TouchPointPressed);
    QTouchEvent touchBegin(QEvent::TouchBegin,touchDevice,Qt::NoModifier,Qt::TouchPointPressed,{finger});QApplication::sendEvent(&radial,&touchBegin);
    finger.setState(Qt::TouchPointReleased);
    QTouchEvent touchEnd(QEvent::TouchEnd,touchDevice,Qt::NoModifier,Qt::TouchPointReleased,{finger});QApplication::sendEvent(&radial,&touchEnd);
    require(selected==0 && !radial.isVisible(),"Finger tap failed in radial menu");
    radial.show();selected=-1;
    const auto penPoint=radial.itemCenter(1);
    QTabletEvent penDown(QEvent::TabletPress,penPoint,penPoint,QTabletEvent::Stylus,QTabletEvent::Pen,.7,0,0,0,0,0,Qt::NoModifier,4,Qt::LeftButton,Qt::LeftButton);
    QTabletEvent penUp(QEvent::TabletRelease,penPoint,penPoint,QTabletEvent::Stylus,QTabletEvent::Pen,0,0,0,0,0,0,Qt::NoModifier,4,Qt::LeftButton,Qt::NoButton);
    QApplication::sendEvent(&radial,&penDown);QApplication::sendEvent(&radial,&penUp);
    require(selected==1 && !radial.isVisible(),"Pen tap failed in radial menu");selected=-1;
    radial.show();QTest::mouseClick(&radial,Qt::LeftButton,Qt::NoModifier,radial.rect().center());require(!radial.isVisible() && selected==-1,"Center should only close radial");

    QImage mask(100,100,QImage::Format_ARGB32_Premultiplied);mask.fill(Qt::transparent);
    {QPainter painter(&mask);painter.setPen(Qt::NoPen);painter.setBrush(Qt::white);painter.drawEllipse(QRect(5,5,90,90));}
    auto *wave=new ProKrita::FillWave(canvas,mask,QRect(40,40,100,100),QPoint(90,90),QTransform(),QColor("#dc3840"));
    QPointer<QWidget> guardedWave(wave);QTest::qWait(80);
    require(wave->testAttribute(Qt::WA_TransparentForMouseEvents),"Fill wave blocks drawing");
    const auto frame=wave->grab().toImage();require(frame.pixelColor(10,10).alpha()==0,"Wave escaped fill mask");
    require(frame.pixelColor(90,90).alpha()>0,"Fill wave did not render");
    QTest::qWait(200);QCoreApplication::sendPostedEvents(nullptr,QEvent::DeferredDelete);require(!guardedWave,"Fill wave failed to expire");
    auto *owner=new QMainWindow;auto *smallCanvas=new QWidget;owner->setCentralWidget(smallCanvas);owner->resize(420,240);
    auto *smallDock=new QDockWidget("Layers",owner);smallDock->setWidget(new QTreeWidget);owner->addDockWidget(Qt::RightDockWidgetArea,smallDock);owner->show();smallDock->hide();QTest::qWait(10);
    auto *smallPanel=new ProKrita::AnchoredLayersPanel(smallCanvas,owner,smallDock,52,0.);smallPanel->reveal(false);QTest::qWait(10);
    require(smallPanel->geometry().bottom()<smallCanvas->height(),"Layers overflow a short canvas");
    delete owner; // teardown with a borrowed native docker must also be safe
    std::cout<<"Canvas widget interaction checks passed\n";
}

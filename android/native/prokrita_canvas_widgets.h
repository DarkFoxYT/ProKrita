// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <QApplication>
#include <QDockWidget>
#include <QElapsedTimer>
#include <QHBoxLayout>
#include <QImage>
#include <QIcon>
#include <QLineF>
#include <QLabel>
#include <QMainWindow>
#include <QMouseEvent>
#include <QPainter>
#include <QPointer>
#include <QPushButton>
#include <QScrollArea>
#include <QTabletEvent>
#include <QTimer>
#include <QTouchEvent>
#include <QVariantAnimation>
#include <QVBoxLayout>
#include <array>
#include <cmath>
#include <functional>

namespace ProKrita {
// One pointer path for mouse, pen and touch. Gaps in panels consume input too.
class PointerSurface : public QWidget {
public:
    explicit PointerSurface(QWidget *parent=nullptr):QWidget(parent) {
        setAttribute(Qt::WA_AcceptTouchEvents);setAttribute(Qt::WA_NoMousePropagation);
    }
protected:
    virtual void pointer(QPointF position,bool begin,bool end,bool cancelled) = 0;
    bool event(QEvent *e) override {
        const auto type=e->type();
        if(type==QEvent::TouchBegin || type==QEvent::TouchUpdate || type==QEvent::TouchEnd || type==QEvent::TouchCancel) {
            auto *touch=static_cast<QTouchEvent*>(e);
            if(!touch->touchPoints().isEmpty())pointer(touch->touchPoints().first().pos(),type==QEvent::TouchBegin,type==QEvent::TouchEnd,
                type==QEvent::TouchCancel || touch->touchPoints().size()!=1);
            m_synthetic.start();e->accept();return true;
        }
        if(type==QEvent::TabletPress || type==QEvent::TabletMove || type==QEvent::TabletRelease) {
            pointer(static_cast<QTabletEvent*>(e)->posF(),type==QEvent::TabletPress,type==QEvent::TabletRelease,false);
            m_synthetic.start();e->accept();return true;
        }
        if(type==QEvent::MouseButtonPress || type==QEvent::MouseMove || type==QEvent::MouseButtonRelease || type==QEvent::MouseButtonDblClick) {
            auto *mouse=static_cast<QMouseEvent*>(e);
            if(mouse->source()!=Qt::MouseEventNotSynthesized && m_synthetic.isValid() && m_synthetic.elapsed()<300){e->accept();return true;}
            if(type!=QEvent::MouseButtonDblClick)pointer(mouse->localPos(),type==QEvent::MouseButtonPress && mouse->button()==Qt::LeftButton,type==QEvent::MouseButtonRelease,false);
            e->accept();return true;
        }
        return QWidget::event(e);
    }
private:
    QElapsedTimer m_synthetic;
};

class LayerGrip : public PointerSurface {
public:
    explicit LayerGrip(QWidget *parent):PointerSurface(parent) {setFixedHeight(46);setCursor(Qt::OpenHandCursor);setStyleSheet("background:#30343c;border:0;");}
    std::function<void(qreal,bool)> dragged;
    std::function<void()> closed;
protected:
    void pointer(QPointF position,bool begin,bool end,bool cancelled) override {
        const QPointF global=mapToGlobal(position.toPoint());
        const bool overClose=QRectF(width()-52,0,52,height()).contains(position);
        if(begin){m_down=true;m_closePress=overClose;m_start=global;setCursor(Qt::ClosedHandCursor);}
        if(m_down && !m_closePress && !cancelled && dragged)dragged(global.y()-m_start.y(),begin);
        if(end && m_down && m_closePress && overClose && !cancelled && closed)closed();
        if(end || cancelled){m_down=false;setCursor(Qt::OpenHandCursor);}
    }
    void paintEvent(QPaintEvent *) override {
        QPainter p(this);p.setRenderHint(QPainter::Antialiasing);
        p.setPen(QPen(QColor("#818b9c"),3,Qt::SolidLine,Qt::RoundCap));
        p.drawLine(width()/2-15,7,width()/2+15,7);
        p.setPen(QColor("#f2f4f8"));p.drawText(QRect(14,12,width()-70,30),Qt::AlignVCenter,tr("Layers"));
        p.setPen(Qt::NoPen);p.setBrush(QColor("#444c59"));p.drawRoundedRect(QRectF(width()-44,10,36,32),16,16);
        p.setPen(QColor("#f2f4f8"));p.drawText(QRectF(width()-44,10,36,32),Qt::AlignCenter,QString::fromUtf8("\xc3\x97"));
    }
private:
    bool m_down=false,m_closePress=false;QPointF m_start;
};

// Temporarily hosts Krita's real Layers docker: its model and every action stay
// native. Closing restores the docker to its main window, hidden and intact.
class AnchoredLayersPanel : public PointerSurface {
public:
    AnchoredLayersPanel(QWidget *canvas,QMainWindow *window,QDockWidget *dock,int top,qreal ratio)
        :PointerSurface(canvas),m_window(window),m_dock(dock),m_top(top),m_ratio(qBound(0.,ratio,1.)) {
        setObjectName("prokrita_layers_popup");setAutoFillBackground(true);
        setStyleSheet("#prokrita_layers_popup {background:#30343c;border:1px solid #626b7b;border-radius:14px;} QToolButton {min-height:28px;min-width:28px;}");
        m_area=window->dockWidgetArea(dock);if(m_area==Qt::NoDockWidgetArea)m_area=Qt::RightDockWidgetArea;
        m_features=dock->features();m_oldTitle=dock->titleBarWidget();
        auto *layout=new QVBoxLayout(this);layout->setContentsMargins(8,3,8,8);layout->setSpacing(2);
        auto *grip=new LayerGrip(this);layout->addWidget(grip);
        grip->closed=[this]{QTimer::singleShot(0,this,[this]{dismiss();});};
        grip->dragged=[this](qreal delta,bool begin) {
            if(m_closing)return;
            if(begin){m_slide.stop();m_dragY=y();}
            const int limit=qMax(m_top,parentWidget()->height()-height()-12);
            move(anchorX(),qBound(m_top,m_dragY+qRound(delta),limit));
            m_ratio=limit==m_top?0.:qreal(y()-m_top)/(limit-m_top);
            if(positionChanged)positionChanged(m_ratio);
        };
        window->removeDockWidget(dock);dock->setParent(this,Qt::Widget);
        m_blankTitle=new QWidget(dock);dock->setTitleBarWidget(m_blankTitle);
        dock->setFeatures(QDockWidget::NoDockWidgetFeatures);
        auto *body=new QScrollArea(this);m_body=body;body->setFrameShape(QFrame::NoFrame);body->setWidgetResizable(true);
        body->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);body->setWidget(dock);layout->addWidget(body);dock->show();
        relayout(top);hide();
        connect(&m_slide,&QVariantAnimation::valueChanged,this,[this](QVariant value){move(value.toPoint());});
        connect(&m_slide,&QVariantAnimation::finished,this,[this]{if(m_closing){hide();restore();deleteLater();}});
    }
    ~AnchoredLayersPanel() override {restore();}
    std::function<void(qreal)> positionChanged;
    void relayout(int top) {
        if(m_closing){dismiss(false);return;}
        m_top=qMax(4,top);m_slide.stop();
        resize(qMax(120,qMin(360,parentWidget()->width()-24)),qMax(100,qMin(440,parentWidget()->height()-m_top-12)));
        move(anchorX(),m_top+qRound(m_ratio*qMax(0,parentWidget()->height()-height()-12-m_top)));
    }
    void reveal(bool animated=true) {
        m_animated=animated;m_closing=false;const QPoint end=pos();show();raise();
        if(animated){move(end+QPoint(36,0));m_slide.setStartValue(pos());m_slide.setEndValue(end);m_slide.setDuration(150);m_slide.setEasingCurve(QEasingCurve::OutCubic);m_slide.start();}
    }
    void suspend() {m_slide.stop();hide();}
    void dismiss(bool animated=true) {
        if(m_closing && animated)return;
        m_closing=true;m_slide.stop();
        if(animated && m_animated && isVisible()) {
            m_slide.setStartValue(pos());m_slide.setEndValue(pos()+QPoint(32,0));m_slide.setDuration(110);m_slide.setEasingCurve(QEasingCurve::InCubic);m_slide.start();
        } else {hide();restore();deleteLater();}
    }
protected:
    void pointer(QPointF,bool,bool,bool) override {} // panel gaps never paint
private:
    int anchorX() const {return qMax(4,parentWidget()->width()-width()-12);}
    void restore() {
        if(!m_dock)return;
        // QPointer clears after QWidget starts destroying its children. A main
        // window with no layout is already tearing down; let Qt own that case.
        if(!m_window || !m_window->layout())return;
        auto *dock=m_dock.data();m_dock.clear();dock->hide();
        if(m_body && m_body->widget()==dock)m_body->takeWidget();
        dock->setTitleBarWidget(m_oldTitle);if(m_blankTitle)delete m_blankTitle.data();
        dock->setFeatures(m_features);
        if(m_window){dock->setParent(m_window);if(!QApplication::closingDown())m_window->addDockWidget(m_area,dock);dock->hide();}
    }
    QPointer<QMainWindow> m_window;QPointer<QDockWidget> m_dock;
    QPointer<QWidget> m_oldTitle,m_blankTitle;
    QPointer<QScrollArea> m_body;
    QDockWidget::DockWidgetFeatures m_features;
    Qt::DockWidgetArea m_area=Qt::RightDockWidgetArea;
    QVariantAnimation m_slide;int m_top=60,m_dragY=0;qreal m_ratio=0.;bool m_closing=false,m_animated=true;
};

struct RadialItem {QString label;QIcon icon;bool enabled=true;};
class QuickRadialMenu : public PointerSurface {
public:
    explicit QuickRadialMenu(QWidget *parent):PointerSurface(parent) {resize(260,260);hide();}
    std::array<RadialItem,6> items;
    std::function<void(int)> chosen;
    QPointF itemCenter(int index) const {const qreal a=(-90.+index*60.)*3.14159265358979323846/180.;return QPointF(width()/2.+84.*std::cos(a),height()/2.+84.*std::sin(a));}
    int itemAt(QPointF point) const {for(int i=0;i<6;++i)if(QLineF(point,itemCenter(i)).length()<=34.)return i;return -1;}
protected:
    void pointer(QPointF point,bool begin,bool end,bool cancelled) override {
        if(begin)m_down=true;
        if(m_down){m_hover=itemAt(point);update();}
        if(cancelled){m_down=false;m_hover=-1;hide();return;}
        if(end && m_down){const int index=itemAt(point);m_down=false;m_hover=-1;hide();if(index>=0 && items[index].enabled && chosen)chosen(index);}
    }
    void paintEvent(QPaintEvent *) override {
        QPainter p(this);p.setRenderHint(QPainter::Antialiasing);
        p.setPen(QPen(QColor("#667084"),1));p.setBrush(QColor(35,39,47,245));p.drawEllipse(rect().adjusted(6,6,-6,-6));
        for(int i=0;i<6;++i) {
            const QPointF center=itemCenter(i);const auto &item=items[i];
            p.setBrush(i==m_hover?QColor("#4d8199"):QColor("#3c4350"));p.setPen(QColor("#697489"));p.drawEllipse(center,34,34);
            const QRect iconRect(qRound(center.x()-12),qRound(center.y()-26),24,24);
            item.icon.paint(&p,iconRect,Qt::AlignCenter,item.enabled?QIcon::Normal:QIcon::Disabled);
            p.setPen(item.enabled?QColor("#f2f4f8"):QColor("#8c939f"));QFont font=p.font();font.setPointSizeF(8.5);p.setFont(font);
            p.drawText(QRectF(center.x()-32,center.y()+1,64,29),Qt::AlignHCenter|Qt::AlignTop|Qt::TextWordWrap,item.label);
        }
        p.setBrush(QColor("#303742"));p.setPen(QColor("#9ca8bb"));p.drawEllipse(QPointF(width()/2.,height()/2.),27,27);
        p.setPen(Qt::white);p.drawText(QRectF(width()/2.-25,height()/2.-20,50,40),Qt::AlignCenter,QString::fromUtf8("\xc3\x97"));
    }
private:
    bool m_down=false;int m_hover=-1;
};

// Cosmetic wave only: the real fill is already submitted to Krita's undo stack.
// Its mask is capped at 384x384; new pen/touch input cancels it immediately.
class FillWave : public QWidget {
public:
    FillWave(QWidget *canvas,QImage mask,QRect imageRect,QPoint seed,QTransform imageToWidget,QColor color)
        :QWidget(canvas),m_mask(std::move(mask)),m_rect(imageRect),m_seed(seed),m_transform(imageToWidget),m_color(color) {
        setAttribute(Qt::WA_TransparentForMouseEvents);setAttribute(Qt::WA_TranslucentBackground);setAttribute(Qt::WA_NoSystemBackground);
        setGeometry(canvas->rect());m_clock.start();m_timer.setInterval(16);
        connect(&m_timer,&QTimer::timeout,this,[this]{if(m_clock.elapsed()>=220){hide();deleteLater();}else update(m_transform.mapRect(m_rect).adjusted(-2,-2,2,2));});
        m_timer.start();show();raise();
    }
protected:
    void paintEvent(QPaintEvent *) override {
        if(m_mask.isNull())return;
        const qreal progress=qMin(1.,m_clock.elapsed()/220.);
        const qreal t=1.-std::pow(1.-progress,2.);
        QImage frame(m_mask.size(),QImage::Format_ARGB32_Premultiplied);frame.fill(Qt::transparent);
        QPainter wave(&frame);wave.setRenderHint(QPainter::Antialiasing);
        const qreal sx=qreal(m_mask.width())/qMax(1,m_rect.width()),sy=qreal(m_mask.height())/qMax(1,m_rect.height());
        const QPointF center((m_seed.x()-m_rect.x())*sx,(m_seed.y()-m_rect.y())*sy);
        qreal radius=0.;for(const auto &corner:{QPointF(0,0),QPointF(frame.width(),0),QPointF(0,frame.height()),QPointF(frame.width(),frame.height())})radius=qMax(radius,QLineF(center,corner).length());
        QColor tint=m_color.lighter(145);tint.setAlpha(qRound(52*(1.-progress)));
        wave.setBrush(tint);wave.setPen(QPen(QColor(255,255,255,qRound(155*(1.-progress))),3.));wave.drawEllipse(center,qMax(1.,radius*t),qMax(1.,radius*t));
        wave.setCompositionMode(QPainter::CompositionMode_DestinationIn);wave.drawImage(0,0,m_mask);wave.end();
        QPainter painter(this);painter.setTransform(m_transform);painter.drawImage(QRectF(m_rect),frame);
    }
private:
    QImage m_mask;QRect m_rect;QPoint m_seed;QTransform m_transform;QColor m_color;QElapsedTimer m_clock;QTimer m_timer;
};
}

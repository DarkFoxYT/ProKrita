// SPDX-License-Identifier: GPL-3.0-or-later
#include "kis_tool_prokrita.h"
#include <QCheckBox>
#include <QLabel>
#include <QPainter>
#include <QPushButton>
#include <QSpinBox>
#include <QDoubleSpinBox>
#include <QDockWidget>
#include <QSlider>
#include <QTouchEvent>
#include <QTabletEvent>
#include <QMouseEvent>
#include <QHBoxLayout>
#include <QVBoxLayout>
#include <QSignalBlocker>
#include <QAction>
#include <QApplication>
#include <QMenu>
#include <QMimeData>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QDataStream>
#include <QScrollArea>
#include <QScroller>
#include <QThread>
#include <kis_fill_painter.h>
#include <kis_pixel_selection.h>
#include "prokrita_gap_fill.h"
#include "prokrita_canvas_widgets.h"
#include <kis_selection.h>
#include <kis_selection_filters.h>
#include <kis_resources_snapshot.h>
#include <kis_stroke_strategy_undo_command_based.h>
#include <commands_new/kis_processing_command.h>
#include <processing/fill_processing_visitor.h>
#include <functional>
#include <KisResourceModel.h>
#include <resources/KisSwatch.h>
#include <KisView.h>
#include <input/kis_input_profile_manager.h>
#include <ksharedconfig.h>
#include <kactioncollection.h>
#include <KoCanvasBase.h>
#include <KoPointerEvent.h>
#include <KoToolManager.h>
#include <kis_canvas2.h>
#include <kis_coordinates_converter.h>
#include <kis_image.h>
#include <kis_image_animation_interface.h>
#include <kis_node.h>
#include <kis_paint_device.h>
#include <kis_painting_information_builder.h>
#include <kis_tool_freehand_helper.h>
#include <kis_smoothing_options.h>
#include <kis_figure_painting_tool_helper.h>
#include <KisViewManager.h>
#include <KisPart.h>
#include <KisMainWindow.h>
#include <kis_canvas_resource_provider.h>
#include <kis_config.h>
#include <kis_icon_utils.h>
#include <brushengine/kis_paintop_preset.h>
#include <brushengine/kis_paintop_settings.h>

namespace {
ProKrita::Point point(QPointF p) { return {p.x(),p.y()}; }
QPointF point(ProKrita::Point p) { return {p.x,p.y}; }

// Handle finger and pen input directly: no delayed synthesized click and no
// propagation to the canvas. Mouse/keyboard accessibility remains native Qt.
class TabletButton : public QPushButton {
public:
    TabletButton(const QString &text,QWidget *parent):QPushButton(text,parent) {
        setAttribute(Qt::WA_AcceptTouchEvents);setMinimumHeight(44);
    }
protected:
    bool event(QEvent *event) override {
        const auto type=event->type();
        if(type==QEvent::TouchBegin || type==QEvent::TouchUpdate || type==QEvent::TouchEnd || type==QEvent::TouchCancel) {
            auto *touch=static_cast<QTouchEvent*>(event);
            if(type==QEvent::TouchBegin) {m_valid=touch->touchPoints().size()==1;m_input=true;}
            if(touch->touchPoints().size()>1 || type==QEvent::TouchCancel)m_valid=false;
            if(!touch->touchPoints().isEmpty())m_position=touch->touchPoints().first().pos();
            setDown(m_valid && rect().contains(m_position.toPoint()));
            if(type==QEvent::TouchEnd || type==QEvent::TouchCancel) {
                const bool activate=m_input && isDown() && type==QEvent::TouchEnd;
                m_input=false;setDown(false);if(activate)click();
            }
            m_syntheticClock.start();event->accept();return true;
        }
        if(type==QEvent::TabletPress || type==QEvent::TabletMove || type==QEvent::TabletRelease) {
            auto *pen=static_cast<QTabletEvent*>(event);
            if(type==QEvent::TabletPress) {m_input=true;m_valid=pen->button()==Qt::LeftButton || pen->button()==Qt::NoButton;}
            if(m_input) {
                setDown(m_valid && rect().contains(pen->pos()));
                if(type==QEvent::TabletRelease) {const bool activate=isDown();m_input=false;setDown(false);if(activate)click();}
                m_syntheticClock.start();event->accept();return true;
            }
        }
        if(type==QEvent::MouseButtonPress || type==QEvent::MouseButtonRelease || type==QEvent::MouseMove) {
            auto *mouse=static_cast<QMouseEvent*>(event);
            if(mouse->source()!=Qt::MouseEventNotSynthesized && m_syntheticClock.isValid() && m_syntheticClock.elapsed()<250) {
                event->accept();return true;
            }
        }
        return QPushButton::event(event);
    }
private:
    bool m_input=false,m_valid=false;
    QPointF m_position;
    QElapsedTimer m_syntheticClock;
};

class TabletSlider : public QSlider {
public:
    explicit TabletSlider(QWidget *parent):QSlider(Qt::Horizontal,parent) {
        setAttribute(Qt::WA_AcceptTouchEvents);setMinimumHeight(44);
    }
protected:
    void at(const QPointF &position) {
        const double t=qBound(0.,(position.x()-12)/qMax(1,width()-24),1.);
        setValue(qRound(minimum()+t*(maximum()-minimum())));
    }
    void mousePressEvent(QMouseEvent *event) override {
        if(event->button()==Qt::LeftButton){setSliderDown(true);at(event->localPos());event->accept();}
        else QSlider::mousePressEvent(event);
    }
    void mouseMoveEvent(QMouseEvent *event) override {
        if(isSliderDown()){at(event->localPos());event->accept();}else QSlider::mouseMoveEvent(event);
    }
    void mouseReleaseEvent(QMouseEvent *event) override {
        if(event->button()==Qt::LeftButton){at(event->localPos());setSliderDown(false);event->accept();}
        else QSlider::mouseReleaseEvent(event);
    }
    bool event(QEvent *event) override {
        const auto type=event->type();
        if(type==QEvent::TouchBegin || type==QEvent::TouchUpdate || type==QEvent::TouchEnd || type==QEvent::TouchCancel) {
            auto *touch=static_cast<QTouchEvent*>(event);
            if(type==QEvent::TouchBegin)setSliderDown(true);
            if(touch->touchPoints().size()==1 && type!=QEvent::TouchCancel)at(touch->touchPoints().first().pos());
            if(type==QEvent::TouchEnd || type==QEvent::TouchCancel)setSliderDown(false);
            event->accept();return true;
        }
        if(type==QEvent::TabletPress || type==QEvent::TabletMove || type==QEvent::TabletRelease) {
            auto *pen=static_cast<QTabletEvent*>(event);
            if(type==QEvent::TabletPress)setSliderDown(true);
            if(isSliderDown()) {at(pen->posF());if(type==QEvent::TabletRelease)setSliderDown(false);event->accept();return true;}
        }
        return QSlider::event(event);
    }
};
}

// Own the swatch press until release, including when dragged over the canvas.
class DropCursor : public QWidget {
public:
    explicit DropCursor(QWidget *parent):QWidget(parent) {setAttribute(Qt::WA_TransparentForMouseEvents);setAttribute(Qt::WA_TranslucentBackground);resize(48,56);hide();}
    QColor color;
protected:
    void paintEvent(QPaintEvent *) override {
        QPainter p(this);p.setRenderHint(QPainter::Antialiasing);
        p.setPen(QPen(Qt::black,3));p.setBrush(color);p.drawEllipse(QRectF(13,3,30,30));
        p.setPen(QPen(Qt::white,1.5));p.drawEllipse(QRectF(13,3,30,30));
        QPolygonF arrow;arrow<<QPointF(2,51)<<QPointF(2,25)<<QPointF(22,43)<<QPointF(13,44)<<QPointF(18,53)<<QPointF(12,55)<<QPointF(8,46);
        p.setPen(QPen(Qt::black,2));p.setBrush(Qt::white);p.drawPolygon(arrow);
    }
};

class InputBarrier : public QWidget {
public:
    using QWidget::QWidget;
protected:
    bool event(QEvent *e) override {
        switch(e->type()) {
        case QEvent::TouchBegin:case QEvent::TouchUpdate:case QEvent::TouchEnd:case QEvent::TouchCancel:
        case QEvent::TabletPress:case QEvent::TabletMove:case QEvent::TabletRelease:
        case QEvent::MouseButtonPress:case QEvent::MouseMove:case QEvent::MouseButtonRelease:case QEvent::MouseButtonDblClick:
            e->accept();return true;
        default:return QWidget::event(e);
        }
    }
};

class ColorFillWorker : public QThread {
public:
    explicit ColorFillWorker(std::function<void()> work):m_work(std::move(work)) {}
protected:
    void run() override {m_work();}
private:
    std::function<void()> m_work;
};

class ColorDropButton : public TabletButton {
public:
    using TabletButton::TabletButton;
    std::function<void(QPoint)> dropped;
    std::function<void()> picked;
    std::function<void(QPoint,bool)> moved;
protected:
    bool event(QEvent *event) override {
        QPoint position;bool begin=false,end=false,cancelled=false;
        switch(event->type()) {
        case QEvent::TouchBegin:case QEvent::TouchUpdate:case QEvent::TouchEnd:case QEvent::TouchCancel: {
            auto *e=static_cast<QTouchEvent*>(event);
            if(e->touchPoints().isEmpty())return true;
            position=e->touchPoints().first().screenPos().toPoint();
            begin=event->type()==QEvent::TouchBegin;end=event->type()==QEvent::TouchEnd;
            cancelled=event->type()==QEvent::TouchCancel || e->touchPoints().size()!=1;break;
        }
        case QEvent::TabletPress:case QEvent::TabletMove:case QEvent::TabletRelease: {
            auto *e=static_cast<QTabletEvent*>(event);position=e->globalPos();
            begin=event->type()==QEvent::TabletPress;end=event->type()==QEvent::TabletRelease;break;
        }
        case QEvent::MouseButtonPress:case QEvent::MouseMove:case QEvent::MouseButtonRelease: {
            auto *e=static_cast<QMouseEvent*>(event);
            if(e->source()!=Qt::MouseEventNotSynthesized){event->accept();return true;}
            position=e->globalPos();begin=event->type()==QEvent::MouseButtonPress && e->button()==Qt::LeftButton;
            end=event->type()==QEvent::MouseButtonRelease;break;
        }
        default:return TabletButton::event(event);
        }
        if(begin){m_start=position;m_dragging=false;m_pressed=true;setDown(true);setCursor(Qt::ClosedHandCursor);}
        if(m_pressed && (position-m_start).manhattanLength()>12)m_dragging=true;
        if(m_pressed && moved)moved(position,m_dragging);
        if(cancelled){m_pressed=false;setDown(false);if(moved)moved(position,false);setCursor(Qt::OpenHandCursor);}
        if(end && m_pressed){m_pressed=false;setDown(false);if(moved)moved(position,false);setCursor(Qt::OpenHandCursor);if(m_dragging){if(dropped)dropped(position);}else if(picked)picked();}
        event->accept();return true;
    }
private:
    QPoint m_start;bool m_pressed=false,m_dragging=false;
};

// Cancel the *in-flight* brush stroke rather than issuing an Undo command.
// This cannot remove another edit from history and keeps the replacement atomic.
class ProKritaFreehandHelper : public KisToolFreehandHelper
{
public:
    using KisToolFreehandHelper::KisToolFreehandHelper;
    void abort() { if(isRunning()) cancelPaint(); }
};

KisToolProKrita::KisToolProKrita(KoCanvasBase *canvas)
    : KisToolBrush(canvas)
{
    setObjectName("tool_prokrita");
    m_nativeHelper=new ProKritaFreehandHelper(paintingInformationBuilder(),canvas->resourceManager(),kundo2_i18n("ProKrita Stroke"));
    m_nativeHelper->setSmoothness(smoothingOptions());
    resetHelper(m_nativeHelper);
    connect(m_nativeHelper,&KisToolFreehandHelper::requestExplicitUpdateOutline,this,&KisToolProKrita::explicitUpdateOutline);
    KConfigGroup cfg=KSharedConfig::openConfig()->group("ProKrita");
#ifdef Q_OS_ANDROID
    // Apply the tablet default once; keep any later preference chosen by the artist.
    if(!cfg.readEntry("penAndGestureDefaults",false)) {
        KisConfig config(false);
        config.setTouchPainting(KisConfig::TOUCH_PAINTING_DISABLED);
        cfg.writeEntry("penAndGestureDefaults",true);
    }
#endif
    m_enabled=cfg.readEntry("holdShapes",true);
    m_autoStabilizer=cfg.readEntry("autoStabilizer",true);
    m_gapPixels=qBound(0,cfg.readEntry("fillGapPixels",4),12);
    m_animateFill=cfg.readEntry("animateColorDrop",true);m_animatePanels=cfg.readEntry("animatePanels",true);
    m_layersPosition=qBound(0.,cfg.readEntry("layersAnchorPosition",0.),1.);
    m_stabilizerStrength=qBound(0,cfg.readEntry("stabilizerStrength",20),100);
    if(!cfg.readEntry("responsiveSmoothingV2",false)) {
        m_stabilizerStrength=qMin(20,m_stabilizerStrength);
        cfg.writeEntry("stabilizerStrength",m_stabilizerStrength);cfg.writeEntry("responsiveSmoothingV2",true);
    }
    m_holdMs=qBound(300,cfg.readEntry("holdMilliseconds",550),1400);
    m_hold.setSingleShot(true);
    connect(&m_hold,&QTimer::timeout,this,&KisToolProKrita::hold);
    qApp->installEventFilter(this);
}

KisToolProKrita::~KisToolProKrita() { qApp->removeEventFilter(this);m_active=false;cancel();delete m_layers.data();delete m_radial.data();delete m_quickTrigger.data();delete m_layerTrigger.data();delete m_fillWave.data();delete m_quick.data();delete m_settings.data(); }

void KisToolProKrita::activate(const QSet<KoShape*> &shapes)
{
    KisToolBrush::activate(shapes);
    m_active=true;
    if(m_layerTrigger)m_layerTrigger->hide();
    KConfigGroup config=KSharedConfig::openConfig()->group("ProKrita");
    if(!config.readEntry("tabletGestureProfileV2",false)) {
        auto *manager=KisInputProfileManager::instance();
        if(auto *profile=manager->profile("ProKrita Tablet Touch")) {
            manager->setCurrentProfile(profile);manager->saveProfiles();
            config.writeEntry("tabletGestureProfileV2",true);
        }
    }
    // Changing dock geometry can process input events; leave the tool-switch
    // callback before creating widgets so the shortcut matcher cannot re-enter.
    QTimer::singleShot(0,this,&KisToolProKrita::showPanel);
    // New-document layout restoration can select another dock after activation.
    QTimer::singleShot(750,this,&KisToolProKrita::showPanel);
    QTimer::singleShot(1800,this,&KisToolProKrita::showPanel);
}

void KisToolProKrita::showPanel()
{
    if(!m_active)return;
    if(mode()==KisTool::PAINT_MODE || m_tracking) {QTimer::singleShot(100,this,&KisToolProKrita::showPanel);return;}
    auto *window=KisPart::instance()->currentMainwindow();
    auto *c=dynamic_cast<KisCanvas2*>(canvas());
    if(!window||!c)return;
    if(auto *layers=window->findChild<QDockWidget*>("KisLayerBox"))if(layers->parentWidget()==window)layers->hide();
    if(m_settings) {if(m_quick){m_quick->show();m_quick->raise();}if(m_quickTrigger){m_quickTrigger->show();m_quickTrigger->raise();}m_panelSync.start();return;}
    disconnect(&m_panelSync,nullptr,this,nullptr);
    auto *dock=new InputBarrier(c->canvasWidget());
    dock->setAttribute(Qt::WA_AcceptTouchEvents);dock->setAttribute(Qt::WA_NoMousePropagation);
    dock->setAutoFillBackground(true);
    dock->setObjectName("prokrita_settings");dock->setMinimumWidth(280);
    m_settings=dock;
    m_panel=new QWidget(dock);
    m_panel->setObjectName("prokrita_canvas_controls");
    m_panel->setAutoFillBackground(true);
    auto *layout=new QVBoxLayout(m_panel);
    layout->setContentsMargins(8,8,8,8);
    m_panel->setStyleSheet("QPushButton {background:#454545;color:#eeeeee;border:1px solid #646464;border-radius:22px;padding:0 8px;} QPushButton:pressed,QPushButton:checked {background:#52788c;} QPushButton:disabled {color:#888888;}");
    auto *strip=new InputBarrier(c->canvasWidget());m_quick=strip;
    strip->setAttribute(Qt::WA_AcceptTouchEvents);strip->setAttribute(Qt::WA_NoMousePropagation);
    strip->setStyleSheet(m_panel->styleSheet());
    auto *quick=new QHBoxLayout(strip);quick->setContentsMargins(0,0,0,0);quick->setSpacing(4);
    auto *options=new TabletButton(QString::fromUtf8("\xe2\x8b\xaf"),strip);options->setFixedWidth(44);
    options->setToolTip(i18n("Drawing options"));quick->addWidget(options);
    auto *smudge=new TabletButton(i18n("Smudge"),strip);smudge->setCheckable(true);quick->addWidget(smudge);
    connect(smudge,&QPushButton::clicked,this,&KisToolProKrita::toggleSmudge);
    auto *brushes=new TabletButton(QString(),strip);brushes->setFixedWidth(44);
    brushes->setIcon(KisIconUtils::loadIcon("krita_tool_freehand"));brushes->setToolTip(i18n("Choose a brush"));quick->addWidget(brushes);
    connect(brushes,&QPushButton::clicked,this,[this]{triggerAction("show_brush_presets");});
    auto *layersButton=new TabletButton(i18n("Layers"),strip);layersButton->setMinimumWidth(70);quick->addWidget(layersButton);
    connect(layersButton,&QPushButton::clicked,this,[this]{QTimer::singleShot(0,this,&KisToolProKrita::toggleLayers);});
    auto *swatch=new ColorDropButton(QString(),strip);swatch->setFixedSize(44,44);swatch->setCursor(Qt::OpenHandCursor);
    swatch->setToolTip(i18n("Tap to pick a color. Drag onto the canvas to fill a region."));quick->addWidget(swatch);
    swatch->picked=[this]{triggerAction("show_color_selector");};
    swatch->dropped=[this](QPoint position){dropColor(position);};
    auto *marker=new DropCursor(strip->parentWidget());
    connect(strip,&QObject::destroyed,marker,&QObject::deleteLater);
    swatch->moved=[marker,c](QPoint position,bool visible) {
        marker->color=c->viewManager()->canvasResourceProvider()->fgColor().toQColor();
        marker->move(c->canvasWidget()->mapFromGlobal(position)-QPoint(2,51));
        marker->setVisible(visible);if(visible){marker->raise();marker->update();}
    };
    connect(options,&QPushButton::clicked,this,[this]{if(m_settings && m_quick){
        if(m_settings->isVisible()){m_settings->hide();return;}
        hideTransientPanels();
        const int available=qMax(80,canvas()->canvasWidget()->height()-m_quick->geometry().bottom()-18);
        m_settings->resize(qMin(320,canvas()->canvasWidget()->width()-20),qMin(m_panel->sizeHint().height()+8,available));
        m_settings->move(qMax(4,m_quick->geometry().right()-m_settings->width()),m_quick->geometry().bottom()+6);
        m_settings->show();m_settings->raise();
    }});
    auto *row=new QHBoxLayout;
    layout->addLayout(row);
    const auto addAction=[this,row](const QString &label,const QString &id) {
        auto *button=new TabletButton(label,m_panel);row->addWidget(button);
        connect(button,&QPushButton::clicked,this,[this,id] {
            if(m_tracking)return;
            QTimer::singleShot(0,this,[this,id]{
                if(m_tracking)return;
                auto *window=KisPart::instance()->currentMainwindow();
                if(window)if(auto *action=window->actionCollection()->action(id))if(action->isEnabled())action->trigger();
            });
        });
        return button;
    };
    auto *undo=addAction(i18n("Undo"),QStringLiteral("edit_undo"));
    auto *redo=addAction(i18n("Redo"),QStringLiteral("edit_redo"));
    auto *snap=new TabletButton(i18n("Hold to snap shapes"),m_panel);
    snap->setCheckable(true);snap->setChecked(m_enabled);layout->addWidget(snap);
    connect(snap,&QPushButton::toggled,this,[this](bool on){
        m_enabled=on;m_hold.stop();
        KSharedConfig::openConfig()->group("ProKrita").writeEntry("holdShapes",on);
    });
    auto *sizeLabel=new QLabel(m_panel);layout->addWidget(sizeLabel);
    auto *size=new TabletSlider(m_panel);size->setRange(0,1000);
    layout->addWidget(size);
    auto *opacityLabel=new QLabel(m_panel);layout->addWidget(opacityLabel);
    auto *opacity=new TabletSlider(m_panel);opacity->setRange(0,100);
    layout->addWidget(opacity);
    auto *resources=c->viewManager()->canvasResourceProvider();
    connect(size,&QSlider::valueChanged,this,[this,resources,sizeLabel](int value){
        if(!m_tracking)resources->setSize(.5*std::pow(4000.,value/1000.));
        sizeLabel->setText(i18n("Size: %1 px",QString::number(resources->size(),'f',1)));
    });
    connect(opacity,&QSlider::valueChanged,this,[this,resources,opacityLabel](int value){
        if(!m_tracking)resources->setOpacity(value/100.);
        opacityLabel->setText(i18n("Opacity: %1%",value));
    });
    auto *autoStabilizer=new TabletButton(i18n("Auto pen / mouse stabilizer"),m_panel);
    autoStabilizer->setCheckable(true);autoStabilizer->setChecked(m_autoStabilizer);layout->addWidget(autoStabilizer);
    connect(autoStabilizer,&QPushButton::toggled,this,[this](bool enabled){
        m_autoStabilizer=enabled;KSharedConfig::openConfig()->group("ProKrita").writeEntry("autoStabilizer",enabled);
        if(!enabled && !m_tracking)smoothingOptions()->setSmoothingType(KisSmoothingOptions::SIMPLE_SMOOTHING);
    });
    auto *stabilizerLabel=new QLabel(i18n("Stabilizer strength: %1%",m_stabilizerStrength),m_panel);layout->addWidget(stabilizerLabel);
    auto *stabilizer=new TabletSlider(m_panel);stabilizer->setRange(0,100);stabilizer->setValue(m_stabilizerStrength);layout->addWidget(stabilizer);
    connect(stabilizer,&QSlider::valueChanged,this,[this,stabilizerLabel](int value){
        m_stabilizerStrength=value;stabilizerLabel->setText(i18n("Stabilizer strength: %1%",value));
        KSharedConfig::openConfig()->group("ProKrita").writeEntry("stabilizerStrength",value);
    });
    auto *gapLabel=new QLabel(i18n("Gap closing: %1 px",m_gapPixels),m_panel);layout->addWidget(gapLabel);
    auto *gap=new TabletSlider(m_panel);gap->setRange(0,12);gap->setValue(m_gapPixels);layout->addWidget(gap);
    connect(gap,&QSlider::valueChanged,this,[this,gapLabel](int value){
        m_gapPixels=value;gapLabel->setText(value?i18n("Gap closing: %1 px",value):i18n("Gap closing: off"));
        KSharedConfig::openConfig()->group("ProKrita").writeEntry("fillGapPixels",value);
    });
    auto *fillAnimation=new TabletButton(i18n("ColorDrop wave animation"),m_panel);
    fillAnimation->setCheckable(true);fillAnimation->setChecked(m_animateFill);layout->addWidget(fillAnimation);
    connect(fillAnimation,&QPushButton::toggled,this,[this](bool value){m_animateFill=value;KSharedConfig::openConfig()->group("ProKrita").writeEntry("animateColorDrop",value);if(!value)delete m_fillWave.data();});
    auto *panelAnimation=new TabletButton(i18n("Panel slide animations"),m_panel);
    panelAnimation->setCheckable(true);panelAnimation->setChecked(m_animatePanels);layout->addWidget(panelAnimation);
    connect(panelAnimation,&QPushButton::toggled,this,[this](bool value){m_animatePanels=value;KSharedConfig::openConfig()->group("ProKrita").writeEntry("animatePanels",value);});
    auto *quickTrigger=new TabletButton(i18n("Quick"),c->canvasWidget());m_quickTrigger=quickTrigger;
    quickTrigger->setStyleSheet(m_panel->styleSheet());quickTrigger->setFixedSize(64,48);
    quickTrigger->move(qMax(8,c->canvasWidget()->width()-80),qMax(8,c->canvasWidget()->height()-64));
    connect(quickTrigger,&QPushButton::clicked,this,[this]{QTimer::singleShot(0,this,&KisToolProKrita::toggleQuickMenu);});
    auto *layerTrigger=new TabletButton(i18n("Layers"),c->canvasWidget());m_layerTrigger=layerTrigger;
    layerTrigger->setStyleSheet(m_panel->styleSheet());layerTrigger->setFixedSize(76,48);
    layerTrigger->move(qMax(8,c->canvasWidget()->width()-168),qMax(8,c->canvasWidget()->height()-64));layerTrigger->hide();
    connect(layerTrigger,&QPushButton::clicked,this,[this]{QTimer::singleShot(0,this,&KisToolProKrita::toggleLayers);});
    m_radial=new ProKrita::QuickRadialMenu(c->canvasWidget());
    m_panelSync.setInterval(250);
    const auto sync=[this,window,resources,size,opacity,snap,sizeLabel,opacityLabel,undo,redo,smudge,swatch] {
        if(!m_panel)return;
        const QSignalBlocker a(size),b(opacity),d(snap);
        if(!size->isSliderDown())size->setValue(qRound(std::log(qMax(.5,resources->size())/.5)/std::log(4000.)*1000));
        if(!opacity->isSliderDown())opacity->setValue(qRound(resources->opacity()*100));
        sizeLabel->setText(i18n("Size: %1 px",QString::number(resources->size(),'f',1)));
        opacityLabel->setText(i18n("Opacity: %1%",qRound(resources->opacity()*100)));
        snap->setChecked(m_enabled);m_panel->setEnabled(!m_tracking && !m_fillBusy);
        if(m_quick)m_quick->setEnabled(!m_tracking && !m_fillBusy);
        if(m_quickTrigger)m_quickTrigger->setEnabled(!m_tracking && !m_fillBusy);
        {const QSignalBlocker blocker(smudge);smudge->setChecked(m_smudge);smudge->setText(m_smudge?i18n("Paint"):i18n("Smudge"));}
        const QColor foreground=resources->fgColor().toQColor();
        swatch->setStyleSheet(QString("QPushButton {background:%1;border:2px solid #eeeeee;border-radius:22px;}").arg(foreground.name()));
        for(const auto &entry:std::vector<std::pair<QPushButton*,QString>>{{undo,QStringLiteral("edit_undo")},{redo,QStringLiteral("edit_redo")}}) {
            auto *action=window->actionCollection()->action(entry.second);
            entry.first->setEnabled(action && action->isEnabled());
        }
    };
    connect(&m_panelSync,&QTimer::timeout,this,sync);sync();
    auto *scroll=new QScrollArea(dock);scroll->setWidgetResizable(true);scroll->setFrameShape(QFrame::NoFrame);
    scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);scroll->setWidget(m_panel);
    QScroller::grabGesture(scroll->viewport(),QScroller::TouchGesture);
    auto *popupLayout=new QVBoxLayout(dock);popupLayout->setContentsMargins(0,0,0,0);popupLayout->addWidget(scroll);
    dock->hide();strip->adjustSize();strip->move(qMax(8,c->canvasWidget()->width()-strip->width()-12),12);
    strip->show();strip->raise();quickTrigger->show();quickTrigger->raise();m_panelSync.start();
}

void KisToolProKrita::hideTransientPanels()
{
    if(m_settings)m_settings->hide();
    if(m_radial)m_radial->hide();
    if(m_layers){static_cast<ProKrita::AnchoredLayersPanel*>(m_layers.data())->suspend();QTimer::singleShot(0,this,&KisToolProKrita::releaseLayers);}
}

void KisToolProKrita::releaseLayers()
{
    if(!m_layers || m_layers->isVisible())return;
    if(m_tracking || mode()==KisTool::PAINT_MODE){QTimer::singleShot(100,this,&KisToolProKrita::releaseLayers);return;}
    delete m_layers.data();
}

void KisToolProKrita::toggleLayers()
{
    if(!canvasIsCurrent() || m_tracking || m_fillBusy)return;
    if(m_layers && m_layers->isVisible()){static_cast<ProKrita::AnchoredLayersPanel*>(m_layers.data())->dismiss(m_animatePanels);return;}
    hideTransientPanels();releaseLayers();
    auto *window=KisPart::instance()->currentMainwindow();if(!window)return;
    auto *native=window->findChild<QDockWidget*>("KisLayerBox");
    if(!native){static_cast<KisCanvas2*>(canvas())->viewManager()->showFloatingMessage(i18n("Layers panel is unavailable in this workspace."),QIcon());return;}
    auto *panel=new ProKrita::AnchoredLayersPanel(canvas()->canvasWidget(),window,native,m_quick?m_quick->geometry().bottom()+8:64,m_layersPosition);
    m_layers=panel;
    panel->positionChanged=[this](qreal value){m_layersPosition=value;KSharedConfig::openConfig()->group("ProKrita").writeEntry("layersAnchorPosition",value);};
    panel->reveal(m_animatePanels);
}

void KisToolProKrita::toggleQuickMenu()
{
    if(!canvasIsCurrent() || m_tracking || m_fillBusy || !m_radial)return;
    if(m_radial->isVisible()){m_radial->hide();return;}
    hideTransientPanels();
    auto *window=KisPart::instance()->currentMainwindow();if(!window)return;
    auto *menu=static_cast<ProKrita::QuickRadialMenu*>(m_radial.data());
    const QStringList ids{"edit_undo","edit_redo","erase_action","show_brush_presets","mirror_canvas_around_canvas",QString()};
    const QStringList labels{i18n("Undo"),i18n("Redo"),i18n("Eraser"),i18n("Brushes"),i18n("Flip"),i18n("Layers")};
    for(int i=0;i<6;++i) {
        auto *action=ids[i].isEmpty()?nullptr:window->actionCollection()->action(ids[i]);
        menu->items[i]={labels[i],action?action->icon():KisIconUtils::loadIcon("layer-visible-on"),i==5 || (action && action->isEnabled())};
    }
    menu->chosen=[this,ids](int index){
        if(index==5)QTimer::singleShot(0,this,&KisToolProKrita::toggleLayers);
        else if((index==2 || index==3) && !m_active)QTimer::singleShot(0,this,[this,index,ids]{
            if(!canvasIsCurrent())return;
            KoToolManager::instance()->switchToolRequested("KritaShape/KisToolBrush");
            static_cast<KisCanvas2*>(canvas())->viewManager()->canvasResourceProvider()->setEraserMode(index==2);
            if(index==3)triggerAction(ids[index]);
        });
        else triggerAction(ids[index]);
    };
    menu->move(qMax(4,canvas()->canvasWidget()->width()-menu->width()-16),qMax(4,canvas()->canvasWidget()->height()-menu->height()-78));
    menu->show();menu->raise();menu->update();
}

bool KisToolProKrita::panelContains(QPoint point) const
{
    for(const auto &panel:{m_quick,m_settings,m_quickTrigger,m_layerTrigger,m_radial,m_layers})
        if(panel && panel->isVisible() && panel->geometry().contains(point))return true;
    return false;
}

bool KisToolProKrita::canvasIsCurrent() const
{
    if(QApplication::closingDown() || !KisPart::exists())return false;
    auto *c=dynamic_cast<KisCanvas2*>(canvas());auto *window=KisPart::instance()->currentMainwindow();
    return c && window && window->activeView()==c->imageView().data();
}

void KisToolProKrita::triggerAction(const QString &id)
{
    QTimer::singleShot(0,this,[this,id] {
        if(m_tracking || !canvasIsCurrent())return;
        if(auto *window=KisPart::instance()->currentMainwindow())
            if(auto *action=window->actionCollection()->action(id))if(action->isEnabled())action->trigger();
    });
}

void KisToolProKrita::toggleSmudge()
{
    if(m_tracking || !m_active)return;
    auto *c=static_cast<KisCanvas2*>(canvas());auto *resources=c->viewManager()->canvasResourceProvider();
    if(m_smudge) {
        resources->setPaintOpPreset(m_drawingPreset);resources->setSize(m_drawingSize);
        resources->setOpacity(m_drawingOpacity);resources->setFlow(m_drawingFlow);
        resources->setCurrentCompositeOp(m_drawingComposite);resources->setEraserMode(m_drawingEraser);
        m_smudge=false;m_drawingPreset.clear();return;
    }
    KisResourceModel model(ResourceType::PaintOpPresets);
    KisPaintOpPresetSP blender;
    for(const auto &name:QStringList{"k) Blender Basic","k) Blender Smear","k) Blender Blur"}) {
        for(auto resource:model.resourcesForName(name)) {
            auto preset=qSharedPointerDynamicCast<KisPaintOpPreset>(resource);
            if(preset && preset->paintOp().id()=="colorsmudge" && !preset->settings()->getBool("PressureColorRate",true) && !preset->settings()->getBool("MergedPaint",false)) {blender=preset;break;}
        }
        if(blender)break;
    }
    if(!blender) {c->viewManager()->showFloatingMessage(i18n("Install a Blender Basic preset to use quick smudge."),QIcon());return;}
    m_drawingPreset=resources->currentPreset();m_drawingSize=resources->size();m_drawingOpacity=resources->opacity();
    m_drawingFlow=resources->flow();m_drawingComposite=resources->currentCompositeOp();m_drawingEraser=resources->eraserMode();
    resources->setPaintOpPreset(blender);resources->setEraserMode(false);resources->setCurrentCompositeOp("normal");
    resources->setSize(qBound(16.,m_drawingSize*1.5,96.));resources->setOpacity(.8);m_smudge=true;
}

void KisToolProKrita::dropColor(const QPoint &globalPosition)
{
    if(m_tracking || !m_active || m_fillBusy)return;
    auto *c=static_cast<KisCanvas2*>(canvas());auto *widget=c->canvasWidget();
    const QPoint position=widget->mapFromGlobal(globalPosition);
    if(!widget->rect().contains(position) || panelContains(position))return;
    KisView *view=nullptr;
    for(QWidget *parent=widget;parent;parent=parent->parentWidget())if((view=qobject_cast<KisView*>(parent)))break;
    KisImageSP targetImage=image();KisNodeSP targetNode=currentNode();
    if(!view || !targetImage || !targetNode || !targetNode->isEditable() || !targetNode->paintDevice())return;
    const QPoint seed=c->coordinatesConverter()->widgetToImage(position).toPoint();
    const QRect bounds=targetImage->bounds();if(!bounds.contains(seed))return;
    auto projection=targetImage->projection();const int sequence=projection->sequenceNumber();
    const int nodeSequence=targetNode->paintDevice()->sequenceNumber();
    const QTransform transform=c->coordinatesConverter()->imageToWidgetTransform();
    KisPaintDeviceSP reference=new KisPaintDevice(*projection);
    const KoColor color=c->viewManager()->canvasResourceProvider()->fgColor();
    const int frame=targetImage->animationInterface()->currentTime();
    auto activeSelection=c->viewManager()->selection();
    const int selectionSequence=activeSelection?activeSelection->projection()->sequenceNumber():0;
    KisPixelSelectionSP selectionSnapshot=activeSelection?new KisPixelSelection(*activeSelection->projection()):nullptr;
    struct Candidate {KisPixelSelectionSP mask;QImage preview;QRect previewRect;bool repaired=false;};
    auto candidate=std::make_shared<Candidate>();
    const int gapRadius=(m_gapPixels+1)/2;
    const bool animate=m_animateFill;
    // Immutable snapshots and linear-time gap closing keep work off input/render.
    m_fillBusy=true;
    auto *worker=new ColorFillWorker([reference,bounds,seed,gapRadius,selectionSnapshot,candidate,animate] {
        KisFillPainter probe(reference);probe.setWidth(bounds.width());probe.setHeight(bounds.height());
        probe.setFillThreshold(20);probe.setOpacitySpread(100);probe.setAntiAlias(false);probe.setSizemod(0);probe.setFeather(0);
        auto mask=probe.createFloodSelection(seed.x(),seed.y(),reference,nullptr);
        const QRect region=mask->selectedExactRect().intersected(bounds);
        if(region.isEmpty())return;
        const bool escaped=region.left()<=bounds.left() && region.right()>=bounds.right() && region.top()<=bounds.top() && region.bottom()>=bounds.bottom();
        if(escaped) {
            if(!gapRadius || uint64_t(bounds.width())*bounds.height()>64ull*1024*1024)return;
            KisPixelSelectionSP freeSpace=new KisPixelSelection(reference->defaultBounds());
            probe.createSimilarColorsSelection(freeSpace,reference->pixel(seed),reference,bounds,nullptr);
            std::vector<uint8_t> pixels(size_t(bounds.width())*bounds.height());freeSpace->readBytes(pixels.data(),bounds);
            auto repaired=ProKrita::closeTinyFillGaps(pixels,bounds.width(),bounds.height(),seed.x()-bounds.x(),seed.y()-bounds.y(),gapRadius);
            if(repaired.status!=ProKrita::GapFillStatus::Filled)return;
            mask=new KisPixelSelection(reference->defaultBounds());mask->writeBytes(repaired.mask.data(),bounds);
            candidate->repaired=true;
        }
        // Overlap the antialiased contour without eating through its darkest ink.
        KisGrowUntilDarkestPixelSelectionFilter overlap(2,reference);
        overlap.process(mask,mask->selectedRect().adjusted(-2,-2,2,2));
        KisAntiAliasSelectionFilter antialias;antialias.process(mask,mask->selectedRect());
        if(selectionSnapshot)mask->applySelection(selectionSnapshot,SELECTION_INTERSECT);
        if(!mask->selectedExactRect().isEmpty()) {
            candidate->mask=mask;
            if(animate) {
                const QRect area=mask->selectedExactRect().intersected(bounds);
                const qreal scale=qMin(1.,384./qMax(area.width(),area.height()));
                const QSize size(qMax(1,qRound(area.width()*scale)),qMax(1,qRound(area.height()*scale)));
                auto thumbnail=mask->createThumbnailDevice(size.width(),size.height(),area,QRect(QPoint(),size));
                if(thumbnail->pixelSize()==1) {
                    std::vector<uint8_t> alpha(size_t(size.width())*size.height());thumbnail->readBytes(alpha.data(),QRect(QPoint(),size));
                    candidate->preview=QImage(size,QImage::Format_ARGB32_Premultiplied);candidate->previewRect=area;
                    for(int y=0;y<size.height();++y) {
                        auto *row=reinterpret_cast<QRgb*>(candidate->preview.scanLine(y));
                        for(int x=0;x<size.width();++x){const int a=alpha[size_t(y)*size.width()+x];row[x]=qRgba(a,a,a,a);}
                    }
                }
            }
        }
    });
    QPointer<KisView> safeView(view);
    connect(worker,&QThread::finished,this,[this,c,safeView,seed,color,targetImage,targetNode,projection,sequence,nodeSequence,transform,frame,activeSelection,selectionSequence,candidate]() mutable {
        m_fillBusy=false;
        if(!m_active || m_tracking || !safeView || image().data()!=targetImage.data() || currentNode()!=targetNode || !targetNode->isEditable() || projection->sequenceNumber()!=sequence || targetNode->paintDevice()->sequenceNumber()!=nodeSequence || c->coordinatesConverter()->imageToWidgetTransform()!=transform || targetImage->animationInterface()->currentTime()!=frame || c->viewManager()->selection()!=activeSelection || (activeSelection && activeSelection->projection()->sequenceNumber()!=selectionSequence))return;
        if(!candidate->mask) {c->viewManager()->showFloatingMessage(i18n("Opening too large — close the outline or drop farther inside."),QIcon());return;}
        KisSelectionSP fillSelection=new KisSelection();
        fillSelection->pixelSelection()->copyAlphaFrom(candidate->mask,candidate->mask->selectedExactRect());
        KisResourcesSnapshotSP resources=new KisResourcesSnapshot(targetImage,targetNode,canvas()->resourceManager());
        resources->setFGColorOverride(color);
        auto *visitor=new FillProcessingVisitor(nullptr,fillSelection,resources);
        visitor->setSeedPoint(seed);visitor->setSelectionOnly(true);
        visitor->setUseCustomBlendingOptions(true);visitor->setCustomOpacity(1.);visitor->setCustomCompositeOp("normal");
        auto *strategy=new KisStrokeStrategyUndoCommandBased(kundo2_i18n("ProKrita ColorDrop"),false,targetImage.data());
        const KisStrokeId stroke=targetImage->startStroke(strategy);
        if(!stroke){delete visitor;return;}
        targetImage->addJob(stroke,new KisStrokeStrategyUndoCommandBased::Data(
            KUndo2CommandSP(new KisProcessingCommand(visitor,targetNode)),false,KisStrokeJobData::SEQUENTIAL,KisStrokeJobData::EXCLUSIVE));
        targetImage->endStroke(stroke);
        if(m_animateFill && !candidate->preview.isNull()) {
            delete m_fillWave.data();
            m_fillWave=new ProKrita::FillWave(canvas()->canvasWidget(),candidate->preview,candidate->previewRect,seed,transform,color.toQColor());
            for(const auto &panel:{m_quick,m_quickTrigger,m_settings,m_radial,m_layers})if(panel && panel->isVisible())panel->raise();
        }
        qInfo()<<"ProKrita bounded ColorDrop submitted; gap repaired"<<candidate->repaired;
    });
    connect(worker,&QThread::finished,worker,&QObject::deleteLater);worker->start();
}

bool KisToolProKrita::eventFilter(QObject *watched,QEvent *event)
{
    // Observe real finger events only. Pen taps remain paint strokes; navigation
    // and native simultaneous-finger gestures continue through Krita unchanged.
    if(!canvasIsCurrent()) {
        if(m_quickTrigger)m_quickTrigger->hide();if(m_layerTrigger)m_layerTrigger->hide();
        return false;
    }
    if(!m_active && canvas()->canvasWidget()->isVisible()) {
        if(m_quickTrigger && m_quickTrigger->isHidden())m_quickTrigger->show();
        if(m_layerTrigger && m_layerTrigger->isHidden())m_layerTrigger->show();
    }
    const auto type=event->type();
    const bool press=type==QEvent::TouchBegin || type==QEvent::TabletPress || type==QEvent::MouseButtonPress;
    if(press && m_fillWave) {
        auto *widget=qobject_cast<QWidget*>(watched);
        if(widget && (widget==canvas()->canvasWidget() || canvas()->canvasWidget()->isAncestorOf(widget)))delete m_fillWave.data();
    }
    if(watched!=canvas()->canvasWidget())return false;
    if(type==QEvent::Resize && m_quick) {
        delete m_fillWave.data();m_quick->adjustSize();m_quick->move(qMax(8,canvas()->canvasWidget()->width()-m_quick->width()-12),12);
        if(m_quickTrigger)m_quickTrigger->move(qMax(8,canvas()->canvasWidget()->width()-80),qMax(8,canvas()->canvasWidget()->height()-64));
        if(m_layerTrigger)m_layerTrigger->move(qMax(8,canvas()->canvasWidget()->width()-168),qMax(8,canvas()->canvasWidget()->height()-64));
        if(m_settings)m_settings->hide();if(m_radial)m_radial->hide();
        if(m_layers)static_cast<ProKrita::AnchoredLayersPanel*>(m_layers.data())->relayout(m_quick->geometry().bottom()+8);
    }
    if(press)hideTransientPanels();
    if(type==QEvent::TouchBegin || type==QEvent::TouchUpdate || type==QEvent::TouchEnd || type==QEvent::TouchCancel) {
        auto *touch=static_cast<QTouchEvent*>(event);
        if(touch->touchPoints().isEmpty())return false;
        const QPointF position=touch->touchPoints().first().pos();
        if(type==QEvent::TouchBegin) {m_touchStart=position;m_touchClock.start();m_touchTap=true;m_touchCount=1;}
        m_touchCount=qMax(m_touchCount,touch->touchPoints().size());
        if(m_touchCount>1 || QLineF(position,m_touchStart).length()>18. || type==QEvent::TouchCancel) {m_touchTap=false;m_tapClock.invalidate();}
        if(type==QEvent::TouchEnd) {
            if(m_touchTap && m_touchClock.elapsed()<220 && !m_tracking) {
                if(m_tapClock.isValid() && m_tapClock.elapsed()<350 && QLineF(position,m_previousTap).length()<48.) {
                    m_tapClock.invalidate();triggerAction("edit_undo");qInfo()<<"ProKrita double-tap undo";
                } else {m_previousTap=position;m_tapClock.start();}
            } else m_tapClock.invalidate();
            m_touchTap=false;
        }
    }
    return false;
}

QWidget *KisToolProKrita::createOptionWidget()
{
    QWidget *widget=KisToolBrush::createOptionWidget();
    auto *enabled=new QCheckBox(i18n("ProKrita: hold to snap shapes"),widget);
    enabled->setMinimumHeight(44);
    enabled->setChecked(m_enabled);
    addOptionWidgetOption(enabled);
    connect(enabled,&QCheckBox::toggled,this,[this](bool value){
        m_enabled=value;m_hold.stop();
        KSharedConfig::openConfig()->group("ProKrita").writeEntry("holdShapes",value);
    });
    auto *delay=new QSpinBox(widget);
    delay->setRange(300,1400);delay->setSingleStep(50);delay->setSuffix(i18n(" ms"));
    delay->setMinimumHeight(44);delay->setValue(m_holdMs);
    addOptionWidgetOption(delay,new QLabel(i18n("Hold time:"),widget));
    connect(delay,QOverload<int>::of(&QSpinBox::valueChanged),this,[this](int value){
        m_holdMs=value;KSharedConfig::openConfig()->group("ProKrita").writeEntry("holdMilliseconds",value);
    });
    auto *hint=new QLabel(i18n("Draw normally. Hold still to snap, move to resize, then lift.\nRound loops favor circles; long loops stay ovals.\nUndo removes the finished shape in one step."),widget);
    hint->setWordWrap(true);addOptionWidgetOption(hint);
    return widget;
}

void KisToolProKrita::initStroke(KoPointerEvent *event)
{
    m_blockedOutside=false;
    auto *viewport=canvas()->canvasWidget();
    auto *kritaCanvas=static_cast<KisCanvas2*>(canvas());
    const QPointF position=kritaCanvas->coordinatesConverter()->imageToWidget(convertToPixelCoord(event));
    if(!viewport->rect().contains(position.toPoint()) || panelContains(position.toPoint())) {m_blockedOutside=true;return;}
    // Native freehand preserves the normal brush, pressure and smoothing path.
    if(m_autoStabilizer && !m_smudge) {
        auto options=smoothingOptions();
        options->setSmoothingType(m_stabilizerStrength<=25 ? KisSmoothingOptions::SIMPLE_SMOOTHING : KisSmoothingOptions::WEIGHTED_SMOOTHING);
        // Set once per stroke: Krita's weighted engine adapts to motion speed.
        // Scale the filter with brush size without adding a delayed pen leash.
        const qreal size=kritaCanvas->viewManager()->canvasResourceProvider()->size();
        const qreal strength=m_stabilizerStrength/100.;
        options->setSmoothnessDistance(qBound(.5,(.5+12.*strength*strength)*qBound(.8,std::sqrt(qMax(1.,size))/6.,1.5),18.));
        options->setUseScalableDistance(true);options->setSmoothPressure(false);options->setTailAggressiveness(.25);
        options->setUseDelayDistance(false);options->setFinishStabilizedCurve(false);options->setStabilizeSensors(false);
    }
    m_hold.stop();m_locked={};m_preview={};m_dirty={};m_snapped=false;
    m_points.clear();m_sensors.clear();
    m_targetImage=image();m_targetNode=currentNode();
    m_frame=m_targetImage->animationInterface()->currentTime();
    auto *c=dynamic_cast<KisCanvas2*>(canvas());
    m_imageToWidget=c->coordinatesConverter()->imageToWidgetTransform();
    bool invertible=false;m_widgetToImage=m_imageToWidget.inverted(&invertible);
    m_tracking=invertible;m_elapsed.start();
    KisToolBrush::initStroke(event);
    if(m_tracking) {record(event);m_dwell=m_points.back();}
}

bool KisToolProKrita::targetValid() const
{
    auto *c=dynamic_cast<KisCanvas2*>(canvas());
    return c && m_targetImage && image().data()==m_targetImage.data() && currentNode()==m_targetNode &&
        m_targetNode && m_targetNode->visible() && !m_targetNode->userLocked() &&
        m_targetNode->paintDevice() && m_targetImage->animationInterface()->currentTime()==m_frame &&
        c->coordinatesConverter()->imageToWidgetTransform()==m_imageToWidget;
}

void KisToolProKrita::record(KoPointerEvent *event)
{
    const QPointF pixel=convertToPixelCoord(event);
    const auto p=point(m_imageToWidget.map(pixel));
    // Bound memory on tablets producing thousands of events per second. Keep
    // endpoints and paired sensor samples together when compacting the buffer.
    if(m_points.size()>=4096) {
        std::vector<ProKrita::Point> compact;QVector<KisPaintInformation> sensors;
        for(size_t i=0;i<m_points.size();i+=2){compact.push_back(m_points[i]);sensors.append(m_sensors[int(i)]);}
        compact.push_back(m_points.back());sensors.append(m_sensors.last());
        m_points.swap(compact);m_sensors.swap(sensors);
    }
    if(!m_points.empty()&&ProKrita::distance(p,m_points.back())<.25)return;
    const auto *converter=static_cast<KisCanvas2*>(canvas())->coordinatesConverter();
    KisPaintInformation info(pixel,paintingInformationBuilder()->pressureToCurve(event->pressure()),
        event->xTilt(),event->yTilt(),event->rotation(),event->tangentialPressure(),1.,m_elapsed.elapsed(),0.);
    info.setCanvasRotation(converter->rotationAngle());
    info.setCanvasMirroredH(converter->xAxisMirrored());info.setCanvasMirroredV(converter->yAxisMirrored());
    m_points.push_back(p);m_sensors.append(info);
}

void KisToolProKrita::doStroke(KoPointerEvent *event)
{
    if(m_blockedOutside)return;
    const QPointF position=static_cast<KisCanvas2*>(canvas())->coordinatesConverter()->imageToWidget(convertToPixelCoord(event));
    if(!canvas()->canvasWidget()->rect().contains(position.toPoint()) || panelContains(position.toPoint())) {
        endStroke();m_blockedOutside=true;return;
    }
    if(!m_tracking) {KisToolBrush::doStroke(event);return;}
    if(!targetValid()) {cancel(false);m_blockedOutside=true;return;}
    const auto p=point(m_imageToWidget.map(convertToPixelCoord(event)));
    if(m_snapped) {
        m_preview=ProKrita::held(m_locked,m_anchor,p);repaintPreview();return;
    }
    KisToolBrush::doStroke(event);record(event);
    if(m_enabled && !m_smudge && ProKrita::distance(p,m_dwell)>3.) {
        m_dwell=p;m_hold.start(m_holdMs);
    }
}

void KisToolProKrita::hold()
{
    if(!m_enabled || m_smudge || !m_tracking || m_snapped || !targetValid() || m_points.size()<3 || mode()!=PAINT_MODE)return;
    // Resample by arc length in the core, independent of event frequency.
    m_locked=ProKrita::fit(m_points);
    if(!m_locked)return;
    m_preview=m_locked;m_anchor=m_points.back();
    m_winding=0;for(size_t i=1;i<m_points.size();++i)m_winding+=ProKrita::cross(m_points[i-1]-m_points[0],m_points[i]-m_points[0]);
    m_nativeHelper->abort();m_snapped=true;repaintPreview();
    qInfo()<<"ProKrita snapped shape"<<int(m_locked.kind)<<"samples"<<m_points.size();
}

QPainterPath KisToolProKrita::imagePreview() const
{
    QPainterPath path;
    const auto pts=ProKrita::outline(m_preview,m_points.empty()?ProKrita::Point{}:m_points.front(),m_winding);
    if(pts.empty())return path;
    path.moveTo(m_widgetToImage.map(point(pts.front())));
    for(size_t i=1;i<pts.size();++i)path.lineTo(m_widgetToImage.map(point(pts[i])));
    return path;
}

void KisToolProKrita::repaintPreview()
{
    const QRectF bounds=imagePreview().boundingRect();
    const QRectF next=bounds.adjusted(-32,-32,32,32);
    updateCanvasPixelRect(m_dirty.united(next));m_dirty=next;
}

void KisToolProKrita::paint(QPainter &gc,const KoViewConverter &converter)
{
    KisToolBrush::paint(gc,converter);
    if(m_snapped)paintToolOutline(&gc,pixelToView(imagePreview()));
}

void KisToolProKrita::commitShape()
{
    auto pts=ProKrita::outline(m_preview,m_points.front(),m_winding);
    if(pts.size()<2)return;
    // Tessellation controls geometric error; arc-length resampling controls
    // sensor interpolation. Neither depends on how fast the OS sends events.
    const auto srcStops=ProKrita::lengths(m_points),dstStops=ProKrita::lengths(pts);
    if(srcStops.back()<=0 || dstStops.back()<=0)return;
    // Merge sensor breakpoints into the tessellation so short pressure changes
    // survive even on a geometrically simple snapped line.
    std::vector<double> stops=dstStops;
    for(double at:srcStops)stops.push_back(at/srcStops.back()*dstStops.back());
    std::sort(stops.begin(),stops.end());
    stops.erase(std::unique(stops.begin(),stops.end()),stops.end());
    QVector<KisPaintInformation> output;
    for(double at:stops) {
        const double sourceAt=at/dstStops.back()*srcStops.back();
        const auto found=std::upper_bound(srcStops.begin(),srcStops.end(),sourceAt);
        const size_t i=std::min(m_points.size()-2,size_t(std::max<std::ptrdiff_t>(0,found-srcStops.begin()-1)));
        const qreal t=std::clamp((sourceAt-srcStops[i])/std::max(1e-9,srcStops[i+1]-srcStops[i]),0.,1.);
        KisPaintInformation info=m_sensors[int(i)];
        // The native interpolator handles tilt, rotation, time and pressure.
        info.mixOtherWithoutTime(1.-t,m_sensors[int(i+1)]);
        info.setCurrentTime(m_sensors[int(i)].currentTime()*(1-t)+m_sensors[int(i+1)].currentTime()*t);
        info.setPos(m_widgetToImage.map(point(ProKrita::sample(pts,dstStops,at))));
        output.append(info);
    }
    KisFigurePaintingToolHelper helper(kundo2_i18n("ProKrita Shape"),m_targetImage,m_targetNode,
        canvas()->resourceManager(),KisToolShapeUtils::StrokeStyleForeground,KisToolShapeUtils::FillStyleNone);
    for(int i=1;i<output.size();++i)helper.paintLine(output[i-1],output[i]);
}

void KisToolProKrita::endStroke()
{
    if(m_finishing)return;
    qInfo()<<"ProKrita stroke ended samples"<<m_points.size()<<"snapped"<<m_snapped;
    m_finishing=true;m_hold.stop();
    if(m_snapped) {
        if(targetValid())commitShape();
        // The cancelled live helper must not receive endPaint() again.
        if(currentPaintOpPreset())currentPaintOpPreset()->settings()->mouseReleaseEvent();
    } else if(m_nativeHelper->isRunning()) {
        KisToolBrush::endStroke();
    }
    m_snapped=false;m_tracking=false;
    if(!m_dirty.isEmpty())updateCanvasPixelRect(m_dirty);
    m_dirty={};m_points.clear();m_sensors.clear();m_targetNode.clear();m_targetImage.clear();
    m_finishing=false;
    if(m_smudge)QTimer::singleShot(0,this,&KisToolProKrita::toggleSmudge);
}

void KisToolProKrita::cancel(bool finishAction)
{
    const bool active=m_tracking || m_nativeHelper->isRunning();
    m_hold.stop();m_nativeHelper->abort();
    m_snapped=false;m_tracking=false;
    if(!m_dirty.isEmpty())updateCanvasPixelRect(m_dirty);
    m_dirty={};m_points.clear();m_sensors.clear();
    m_targetNode.clear();m_targetImage.clear();
    if(active) {
        if(currentPaintOpPreset())currentPaintOpPreset()->settings()->mouseReleaseEvent();
    }
    if(active && finishAction) {
        if(auto *c=dynamic_cast<KisCanvas2*>(canvas()))c->viewManager()->enableControls();
        setMode(KisTool::HOVER_MODE);
    }
}

void KisToolProKrita::requestStrokeCancellation() {cancel();}
// Keep KisToolBrush's requestStrokeEnd behavior. Every native brush stroke
// broadcasts that notification from inside startStroke(); ending ourselves in
// response interrupts initialization and recursively enters the input matcher.
void KisToolProKrita::deactivate()
{
    m_active=false;
    if(m_quick)m_quick->hide();
    hideTransientPanels();delete m_fillWave.data();
    m_panelSync.stop();if(m_settings)m_settings->hide();
    cancel();KisToolBrush::deactivate();
    if(m_quickTrigger)m_quickTrigger->setEnabled(true);
    QTimer::singleShot(0,this,[this]{
        const bool current=canvasIsCurrent();
        if(m_quickTrigger)m_quickTrigger->setVisible(current);
        if(m_layerTrigger)m_layerTrigger->setVisible(current && !m_active);
    });
}

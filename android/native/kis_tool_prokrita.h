// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "kis_tool_brush.h"
#include "prokrita_geometry.h"
#include <QElapsedTimer>
#include <QTimer>
#include <QTransform>
#include <QPointer>

class ProKritaFreehandHelper;
class KisToolProKrita : public KisToolBrush
{
    Q_OBJECT
public:
    explicit KisToolProKrita(KoCanvasBase *canvas);
    ~KisToolProKrita() override;
    QWidget *createOptionWidget() override;
    void activate(const QSet<KoShape*> &shapes) override;
    void paint(QPainter &gc, const KoViewConverter &converter) override;
    void deactivate() override;
    void requestStrokeCancellation() override;
    bool eventFilter(QObject *watched, QEvent *event) override;
protected:
    void initStroke(KoPointerEvent *event) override;
    void doStroke(KoPointerEvent *event) override;
    void endStroke() override;
private:
    void hold();
    void cancel(bool finishAction = true);
    bool targetValid() const;
    void record(KoPointerEvent *event);
    void repaintPreview();
    void commitShape();
    void showPanel();
    void toggleSmudge();
    void toggleQuickMenu();
    void toggleLayers();
    void releaseLayers();
    void hideTransientPanels();
    bool panelContains(QPoint point) const;
    bool canvasIsCurrent() const;
    void dropColor(const QPoint &globalPosition);
    void triggerAction(const QString &id);
    QPainterPath imagePreview() const;
    ProKritaFreehandHelper *m_nativeHelper = nullptr; // owned by KisToolFreehand
    QTimer m_hold;
    QTimer m_panelSync;
    QPointer<QWidget> m_panel;
    QPointer<QWidget> m_quick;
    QPointer<QWidget> m_settings;
    QPointer<QWidget> m_quickTrigger,m_layerTrigger,m_radial,m_layers,m_fillWave;
    QElapsedTimer m_elapsed;
    std::vector<ProKrita::Point> m_points;
    QVector<KisPaintInformation> m_sensors;
    KisImageSP m_targetImage;
    KisNodeSP m_targetNode;
    int m_frame = 0;
    QTransform m_imageToWidget, m_widgetToImage;
    ProKrita::Point m_anchor, m_dwell;
    ProKrita::Shape m_locked, m_preview;
    QRectF m_dirty;
    bool m_enabled = true;
    bool m_tracking = false;
    bool m_snapped = false;
    bool m_finishing = false;
    bool m_active = false;
    bool m_blockedOutside = false;
    int m_holdMs = 550;
    KisPaintOpPresetSP m_drawingPreset;
    qreal m_drawingSize=40.,m_drawingOpacity=1.,m_drawingFlow=1.;
    QString m_drawingComposite;
    bool m_drawingEraser=false,m_smudge=false;
    QElapsedTimer m_tapClock,m_touchClock;
    QPointF m_touchStart,m_previousTap;
    bool m_touchTap=false;
    int m_touchCount=0;
    bool m_autoStabilizer=true;
    bool m_fillBusy=false;
    int m_gapPixels=4;
    bool m_animateFill=true,m_animatePanels=true;
    qreal m_layersPosition=0.;
    int m_stabilizerStrength=20;
    double m_winding = 1.;
};

class KisToolProKritaFactory : public KisToolBrushFactory
{
public:
    KisToolProKritaFactory() : KisToolBrushFactory("KritaShape/KisToolBrush") {
        setToolTip(i18n("ProKrita — draw and hold for adaptive shapes"));
        setSection(ToolBoxSection::Shape);
        setIconName(koIconNameCStr("krita_tool_freehand"));
        setShortcut(QKeySequence(Qt::Key_B));
        setPriority(0);
        setActivationShapeId(KRITA_TOOL_ACTIVATION_ID);
    }
    KoToolBase *createTool(KoCanvasBase *canvas) override { return new KisToolProKrita(canvas); }
};

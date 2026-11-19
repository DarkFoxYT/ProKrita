"""Temporary use of Krita's color-smudge engine with the artist's brush kept in memory."""
from dataclasses import dataclass
from math import isfinite
from xml.etree import ElementTree

from krita import Krita, Preset
from PyQt5 import sip


K = Krita.instance()
BLENDERS = ("k) Blender Basic", "k) Blender Smear", "k) Blender Blur", "k) Blender Textured Soft")


def blender_preset():
    presets = K.resources("preset")
    def pure(resource):
        if resource is None:
            return False
        try:
            preset = ElementTree.fromstring(Preset(resource).toXML())
        except ElementTree.ParseError:
            return False
        # A customized blender may add foreground pigment or sample unrelated layers.
        return (preset.get("paintopid") == "colorsmudge"
                and preset.findtext("param[@name='PressureColorRate']") in ("false", "0")
                and preset.findtext("param[@name='MergedPaint']", "false") in ("false", "0"))
    for name in BLENDERS:
        resource = presets.get(name)
        if pure(resource):
            return resource
    # Imports and translated resource packs can retain the filename under a different display name.
    filenames = {name.lower().replace(" ", "_") + ".kpp" for name in BLENDERS}
    for resource in presets.values():
        if resource.filename().replace("\\", "/").rsplit("/", 1)[-1].lower() in filenames and pure(resource):
            return resource
    return None


def remembered(key, default, low, high):
    try:
        value = float(K.readSetting("ProKrita", key, str(default)))
    except ValueError:
        return default
    return max(low, min(high, value)) if isfinite(value) else default


@dataclass
class BrushState:
    preset: object
    previous_action: object
    size: float
    opacity: float
    flow: float
    rotation: float
    pattern_size: float
    blend: str
    eraser: bool

    @classmethod
    def capture(cls, view):
        return cls(view.currentBrushPreset(), K.action("previous_preset"), view.brushSize(), view.paintingOpacity(),
                   view.paintingFlow(), view.brushRotation(), view.patternSize(), view.currentBlendingMode(),
                   view.eraserMode())

    def restore(self, view):
        if view.currentBrushPreset() != self.preset:
            # Previous Preset returns the in-memory brush, including unsaved brush-editor changes.
            previous = self.previous_action
            if previous and not sip.isdeleted(previous) and previous.isEnabled():
                previous.trigger()
            if view.currentBrushPreset() != self.preset:
                view.setCurrentBrushPreset(self.preset)
        if view.eraserMode() != self.eraser:
            view.setEraserMode(self.eraser)
        if view.currentBlendingMode() != self.blend:
            view.setCurrentBlendingMode(self.blend)
        if view.brushSize() != self.size:
            view.setBrushSize(self.size)
        if view.paintingOpacity() != self.opacity:
            view.setPaintingOpacity(self.opacity)
        if view.paintingFlow() != self.flow:
            view.setPaintingFlow(self.flow)
        if view.brushRotation() != self.rotation:
            view.setBrushRotation(self.rotation)
        if view.patternSize() != self.pattern_size:
            view.setPatternSize(self.pattern_size)


@dataclass
class Smudge:
    window: object
    view: object
    canvas: object
    brush: BrushState
    blender: object
    continuous: bool
    blend_layer: object = None
    merged_engine: object = None
    tablet: object = None
    released: bool = False
    panning: bool = False

    def start(self):
        view = self.view
        if view.currentBrushPreset() != self.blender:
            view.setCurrentBrushPreset(self.blender)
        if view.currentBrushPreset() != self.blender:
            return False
        if view.eraserMode():
            view.setEraserMode(False)
        if view.currentBlendingMode() != "normal":
            view.setCurrentBlendingMode("normal")
        if self.blend_layer:
            self.merged_engine = Preset(view.currentBrushPreset())
            self.set_sampling(True)
        first_size = max(16., min(96., self.brush.size * 1.5))
        view.setBrushSize(remembered("smudgeSize", first_size, .01, 10000.))
        view.setPaintingOpacity(remembered("smudgeOpacity", .8, 0., 1.))
        return True

    def set_sampling(self, enabled):
        xml = ElementTree.fromstring(self.merged_engine.toXML())
        flag = xml.find("param[@name='MergedPaint']")
        if flag is None:
            flag = ElementTree.SubElement(xml, "param", name="MergedPaint", type="string")
        flag.text = "true" if enabled else "false"
        self.merged_engine.fromXML(ElementTree.tostring(xml, encoding="unicode"))

    def restore_sampling(self):
        if self.merged_engine is not None:
            self.set_sampling(False)
            self.merged_engine = None

    def finish(self, view):
        # A manual preset selection ends our ownership of the brush.
        if view.currentBrushPreset() == self.blender:
            K.writeSetting("ProKrita", "smudgeSize", str(view.brushSize()))
            K.writeSetting("ProKrita", "smudgeOpacity", str(view.paintingOpacity()))
            self.brush.restore(view)

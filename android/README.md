# ProKrita Android

The 17 November native ARM64 APK adds tiny-gap closing, a ColorDrop wave, a radial Quick menu and an anchored Layers panel. It is built and debug-signed, but this update has not been installed or exercised on the tablet because no ADB device is connected. The previous build was installed and checked on the Lenovo TB330FUP. Open **ProKrita Android** (`org.krita.prokrita`). Stock Krita remains separate.

Android app: `dist/android/ProKrita-Android-arm64-v8a.apk`. PC plugin: `dist/pc/ProKrita-PC.zip`. `ProKrita-Android-SOURCE.zip` contains source, not an installer.

## Controls

Create or open a document and select the normal Brush tool (B). A rounded control strip floats over the top-right canvas, like the PC version. There is no ProKrita docker tab. Its controls are:

- **⋯**: show/hide size, opacity, shape snapping, stabilizer, gap-closing and animation settings.
- **Smudge**: switch to a pure Blender preset for one stroke, then restore the drawing brush. Tap Paint to return early.
- **Brush icon**: open Krita's native brush-preset picker.
- **Color circle**: tap to pick a color; drag onto a region and release to fill it. A color marker and mouse-shaped pointer track the drag. ColorDrop samples visible layers, uses opaque region fill with a two-pixel edge overlap, and rejects fills that escape across the canvas. Tiny-gap closing defaults to approximately 4 image pixels, adjustable from off to 12 px under ⋯. It bridges only the fill boundary and leaves the lineart untouched. Larger openings and outside components are rejected. Near a boundary, drop farther inside. Boundary checking and gap closing run off the input thread. Krita's standalone fill-tool preferences are preserved.

**Layers:** tap Layers in the top strip, or use the floating Layers control when another tool is active. The panel opens on the right with a 150 ms slide. Drag its header vertically to reposition it while keeping the right-edge anchor; its position is remembered. Close with × or tap the canvas. The panel hosts Krita's actual layer list and controls, preserving visibility, opacity, blending, groups and native actions. It restores the native docker hidden when closed. Its body scrolls on short canvases.

**Quick:** the bottom-right Quick button opens a six-action radial menu: Undo, Redo, Eraser, Brushes, Flip and Layers. Tap an action or press, slide and release over it. The center closes the menu. Disabled native actions are disabled in the menu. Quick and Layers remain accessible when switching away from Brush; Eraser/Brushes return to Brush as needed.

**ColorDrop wave:** successful fills display a short, mask-clipped wave from the drop point. The native fill is submitted immediately; the animation is cosmetic and never becomes part of the artwork. Its preview mask is limited to 384×384 pixels and its duration to 220 ms. New pen/touch input cancels it. ColorDrop waves and panel slides have independent switches under ⋯.

Controls consume pen/touch/mouse input. Strokes stop when entering their bounds, preventing painting behind controls. The overlay follows canvas resizing. Extra settings open as a canvas overlay too.

**Auto pen / mouse stabilizer** defaults to a light 20% helper using native Basic smoothing. Stronger settings above 25% use a short weighted filter, capped at 18 screen pixels. Pressure stays direct and delayed stabilization is disabled. Adjust strength under ⋯ or disable the helper. Shape recognition uses raw motion samples, so smoothing does not distort fitted geometry.

Draw and hold still for 550 ms to snap lines, arcs, circles, ovals, triangles, quadrilaterals, polylines or curves. Moderately uneven loops favor circles; longer loops stay ovals. Move the held pen to resize/rotate. Circles retain equal radii.

**Undo:** two quick single-finger taps on the canvas, or one simultaneous two-finger tap. Pen taps draw. The automatically selected **ProKrita Tablet Touch** profile also provides three-finger Redo, four-finger canvas-only mode and two-finger navigation. Finger painting defaults off.

## Validation and limits

For this update, native Android compilation and APK packaging succeeded. Host tests cover small versus large gaps on every side, outside drops, blank canvases, internal barriers, invalid inputs and unchanged source lineart (128,178 assertions); the existing geometry suite also passed. Offscreen Qt tests additionally cover native docker ownership/restoration, anchored dragging, slide completion, resize during dismissal, short canvases, window teardown, all six radial actions, disabled actions, pen/touch taps, and clipped/self-expiring fill waves. AddressSanitizer passed the widget checks. Device validation for the new selection-mask commit path, full native Layers integration, cross-tool access and Android animation performance is pending. Gap repair is bounded to 64 megapixels to limit tablet memory use; larger documents retain ordinary bounded fill.

On the previous 16 November build, tablet checks confirmed the overlay, brush picker, smudge preset switch, ColorDrop fill, double-tap Undo and settings. Native pen logs recorded completed circles, ovals and lines. Automated stylus injection does not reproduce complete motion reliably on this tablet; physical pen evidence is used instead.

Desktop threshold-preview adjustment during ColorDrop, repeat-fill controls, shading palette, editable shape handles and clipping shortcuts remain unported. Stylus eraser and the full gesture/pressure matrix need broader testing. See `dist/VALIDATION.txt`.

## Rebuild

Pinned Krita v5.2.16: `7d9aefc93f15998eca13458bb9a065e2cca77c02`. Linux/WSL, SDK 35, Build Tools 35.0.0, NDK 27.3.13750724 and JDK 17 are used.

Run `bash android/setup-linux.sh`, source its generated environment, then `bash android/build-android.sh`. The build applies the native port, downloads pinned dependencies, builds Krita and packages a debug APK. Preserve the signing key for in-place updates. Other ABIs are not validated here.

Geometry tests use CMake in `android/tests`. The patcher accepts only the pinned revision and is repeatable. No Python installation or Android plugin loader is needed on the tablet.

Optional host widget checks (Qt 5 Widgets/Test development packages): `cmake -S android/tests -B build/widgets -DPROKRITA_WIDGET_TESTS=ON`, build, then run CTest. These are offscreen checks, not a substitute for tablet testing.

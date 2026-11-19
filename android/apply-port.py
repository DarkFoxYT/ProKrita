#!/usr/bin/env python3
"""Apply the native tool to the pinned upstream Krita source without touching PC files."""
from pathlib import Path
import argparse
import shutil
import subprocess

REVISION = "7d9aefc93f15998eca13458bb9a065e2cca77c02"  # upstream v5.2.16

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Clean or previously patched Krita v5.2.16 checkout")
    args = parser.parse_args()
    source = args.source.resolve()
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise SystemExit(f"Expected Krita v5.2.16 ({REVISION}), got {revision}. Refusing an unreviewed source version.")
    basic = source / "plugins/tools/basictools"
    # Check every insertion point before changing the checkout.
    replacements = {
        basic / "CMakeLists.txt": ("    kis_tool_brush.cc\n", "    kis_tool_brush.cc\n    kis_tool_prokrita.cpp\n"),
        basic / "default_tools.cc": ('#include "kis_tool_brush.h"\n', '#include "kis_tool_brush.h"\n#include "kis_tool_prokrita.h"\n'),
        source / "packaging/android/apk/build.gradle": ('applicationIdSuffix ".debug"', 'applicationIdSuffix ".prokrita"'),
        source / "packaging/android/apk/flavors/debug/AndroidManifest.xml": ('android:label="Krita Debug"', 'android:label="ProKrita Android"'),
        source / "krita/data/input/CMakeLists.txt": ('    kritadefault.profile\n', '    kritadefault.profile\n    prokritatablet.profile\n'),
        source / "packaging/android/apk/src/org/krita/android/MainActivity.java": ('        Log.i(TAG, "TouchSlop: "', '        ProKritaStartup.show(this);\n        Log.i(TAG, "TouchSlop: "'),
    }
    edits = {}
    for path, (before, after) in replacements.items():
        text = path.read_text(encoding="utf-8")
        if after not in text:
            if text.count(before) != 1:
                raise SystemExit(f"Unexpected insertion point in {path}")
            text = text.replace(before, after)
        edits[path] = text
    factory = basic / "default_tools.cc"
    before = "    KoToolRegistry::instance()->add(new KisToolBrushFactory());\n"
    after = "    KoToolRegistry::instance()->add(new KisToolProKritaFactory());\n"
    # Migrate the earlier development patch which registered a separate tool.
    if before + after in edits[factory]:
        edits[factory] = edits[factory].replace(before + after, after)
    elif after not in edits[factory]:
        if edits[factory].count(before) != 1:
            raise SystemExit("Unexpected brush factory registration")
        edits[factory] = edits[factory].replace(before, after)
    for path, text in edits.items():
        if path.read_text(encoding="utf-8") != text:
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
    for name in ("kis_tool_prokrita.cpp", "kis_tool_prokrita.h", "prokrita_geometry.h", "prokrita_gap_fill.h", "prokrita_canvas_widgets.h"):
        original, destination = Path(__file__).parent / "native" / name, basic / name
        if not destination.exists() or destination.read_bytes() != original.read_bytes():
            shutil.copyfile(original, destination)
    original = Path(__file__).parent / "profiles/prokritatablet.profile"
    destination = source / "krita/data/input/prokritatablet.profile"
    if not destination.exists() or destination.read_bytes() != original.read_bytes():
        shutil.copyfile(original, destination)
    original = Path(__file__).parent / "native/ProKritaStartup.java"
    destination = source / "packaging/android/apk/src/org/krita/android/ProKritaStartup.java"
    if not destination.exists() or destination.read_bytes() != original.read_bytes():
        shutil.copyfile(original, destination)
    print(f"Applied ProKrita native Android tool to {source} ({revision})")

if __name__ == "__main__":
    main()

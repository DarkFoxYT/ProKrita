"""Package the PC plugin and Android development source as distinct artifacts."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def archive(destination, files):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as output:
        for source, name in sorted(files, key=lambda item: item[1]):
            output.write(source, name)
    with zipfile.ZipFile(destination) as output:
        assert output.testzip() is None
        assert all("__pycache__" not in name for name in output.namelist())
    return {"file": str(destination.relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}

pc_files = [(path, str(path.relative_to(ROOT)).replace("\\", "/"))
            for path in (ROOT / "prokrita").glob("*.py")]
pc_files += [(ROOT / name, name) for name in ("prokrita.desktop", "README.md", "README-Install.txt")]
pc_files += [(path, path.name) for path in ROOT.glob("ProKrita-*.gif")]
pc = archive(ROOT / "dist/pc/ProKrita-PC.zip", pc_files)
android_root = ROOT / "android"
source_files = [(android_root / name, name) for name in ("README.md", "LICENSE", "apply-port.py", "build-android.sh", "setup-linux.sh")]
source_files += [(path, str(path.relative_to(android_root)).replace("\\", "/"))
                 for pattern in ("native/*.h", "native/*.cpp", "native/*.java", "tests/*.cpp", "tests/*.java", "tests/CMakeLists.txt", "profiles/*.profile")
                 for path in android_root.glob(pattern)]
android = archive(ROOT / "dist/android/ProKrita-Android-SOURCE.zip", source_files)
apk_path = ROOT / "dist/android/ProKrita-Android-arm64-v8a.apk"
apk = None
if apk_path.exists():
    with zipfile.ZipFile(apk_path) as built:
        if "AndroidManifest.xml" not in built.namelist() or not any(
                name.startswith("lib/arm64-v8a/") and name.endswith(".so") for name in built.namelist()):
            raise RuntimeError("Android APK is missing its manifest or ARM64 libraries")
        if built.testzip() is not None:
            raise RuntimeError("Android APK failed its ZIP integrity check")
    apk = {"file": str(apk_path.relative_to(ROOT)).replace("\\", "/"),
           "sha256": hashlib.sha256(apk_path.read_bytes()).hexdigest()}
receipt_path = ROOT / "android/device-validation.json"
receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else None
verified = bool(apk and receipt and receipt.get("apk_sha256") == apk["sha256"])
manifest = {"pc": {**pc, "format": "Krita Python plugin ZIP"},
            "android": {**android, "format": "native development source, NOT an APK",
                        "apk_built": apk is not None, "apk": apk, "device_tested": verified,
                        "device_validation": receipt if verified else None}}
(ROOT / "dist/versions.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2))

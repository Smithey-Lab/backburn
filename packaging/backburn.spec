# PyInstaller spec — builds a single-folder distribution of the viewer.
#   pyinstaller packaging/backburn.spec
# Output: dist/backburn/backburn(.exe) plus the scenarios folder beside it.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
block_cipher = None

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "backburn" / "data"), "backburn/data"),
        (str(ROOT / "scenarios"), "scenarios"),
    ],
    hiddenimports=["pygame", "PIL", "numpy"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pytest", "jsonschema"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="backburn",
    console=True,            # keeps the CLI usable; the viewer opens its own window
    icon=str(ROOT / "packaging" / "icon.ico") if (ROOT / "packaging" / "icon.ico").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="backburn")

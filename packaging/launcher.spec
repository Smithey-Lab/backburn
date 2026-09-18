from pathlib import Path
ROOT = Path(SPECPATH).resolve().parent
a = Analysis([str(ROOT / "backburn_update.py")], pathex=[str(ROOT)],
             excludes=["numpy", "pygame", "PIL", "pytest"], datas=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="Backburn-Setup", console=False,
          icon=str(ROOT / "packaging/icon.ico"))

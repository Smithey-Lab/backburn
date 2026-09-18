from pathlib import Path
ROOT = Path(SPECPATH).resolve().parent
build = Analysis([str(ROOT / 'packaging/game_entry.py')], pathex=[str(ROOT)],
    datas=[(str(ROOT / 'backburn/data'), 'backburn/data'), (str(ROOT / 'scenarios'), 'scenarios')],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pytest', 'jsonschema'], hiddenimports=[])
pyz = PYZ(build.pure)
exe = EXE(pyz, build.scripts, [], exclude_binaries=True, name='Backburn', console=False,
    icon=str(ROOT / 'packaging/icon.ico'))
coll = COLLECT(exe, build.binaries, build.datas, name='Backburn')

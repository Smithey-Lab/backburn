# Packaging

`backburn.spec` builds a **one-folder** PyInstaller distribution: `dist/backburn/` with
`backburn.exe` (Windows) or `backburn` (Linux/macOS), the Python runtime, NumPy, Pillow,
pygame, the balance data and the shipped scenarios.

```
py -3.12 -m venv .venv && .venv\Scripts\activate
packaging\build_windows.bat
dist\backburn\backburn.exe                       # opens the viewer on Prairie Fire
dist\backburn\backburn.exe view scenarios\wall_of_fire.json
dist\backburn\backburn.exe run scenarios\refinery_row.json --ticks 600 --gif out.gif
```

Notes
- One-folder, not one-file: one-file builds unpack ~100 MB to a temp dir on every launch
  and trip antivirus heuristics far more often. Zip the folder to share it.
- PyInstaller must run **on Windows to produce a Windows build**; it does not cross-compile.
- Expect ~90–130 MB. NumPy and pygame are most of it.
- SmartScreen will warn on an unsigned executable. Signing is out of scope for a personal
  build; "More info → Run anyway" is the workaround.
- To add an icon, place `icon.ico` in this folder; the spec picks it up automatically.
- The spec is verified in CI on Linux (build only, no smoke run of the window).

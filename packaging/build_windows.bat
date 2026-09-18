@echo off
REM Build a Windows distribution of the Backburn viewer.
REM Run from the repository root in a venv with requirements-dev.txt installed.
python -m pip install -r requirements-dev.txt || exit /b 1
python -m pytest -q || exit /b 1
pyinstaller --noconfirm --clean packaging\backburn.spec || exit /b 1
echo.
echo Built dist\backburn\backburn.exe  (double-click to play; run with arguments for the CLI)

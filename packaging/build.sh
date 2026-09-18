#!/usr/bin/env sh
# Build a distribution of the Backburn viewer for the current OS (Linux/macOS).
set -e
python -m pip install -r requirements-dev.txt
python -m pytest -q
pyinstaller --noconfirm --clean packaging/backburn.spec
echo "Built dist/backburn/backburn"

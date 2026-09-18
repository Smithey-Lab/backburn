"""Build a checksummed Windows release."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    for spec in ("backburn", "launcher"):
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", f"packaging/{spec}.spec"],
            cwd=ROOT,
            check=True,
        )
    dist = ROOT / "dist"
    shutil.make_archive(str(dist / "Backburn-Windows-x64"), "zip", dist / "Backburn")
    lines = []
    for name in ("Backburn-Windows-x64.zip", "Backburn-Setup.exe"):
        lines.append(f"{hashlib.sha256((dist / name).read_bytes()).hexdigest()}  {name}")
    (dist / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

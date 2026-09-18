# Build Windows release

From the repository root with the development requirements installed:

    python tools/build_release.py

Output: dist/Backburn-Windows-x64.zip, dist/Backburn-Setup.exe, dist/SHA256SUMS.txt.
The game is a one-folder build. The launcher is a small one-file installer that fetches
and verifies the game from GitHub Releases. Run the Setup executable for shortcuts and
updates; the zip alone is portable. Python is not required on the player's computer.

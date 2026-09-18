"""Standard-library installer and updater. Releases come only from this repository."""

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import uuid
import webbrowser
import zipfile
from pathlib import Path

REPO = "Smithey-Lab/backburn"
ASSET = "Backburn-Windows-x64.zip"
ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Backburn"
API = f"https://api.github.com/repos/{REPO}/releases/latest"


def fetch(url):
    if not url.startswith(("https://api.github.com/", f"https://github.com/{REPO}/releases/download/")):
        raise ValueError("Unexpected release URL")
    request = urllib.request.Request(url, headers={"User-Agent": "Backburn-Launcher/1"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def latest_release():
    release = json.loads(fetch(API))
    tag = release["tag_name"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Unsupported release version")
    assets = {a["name"]: a for a in release["assets"]}
    return tag, assets[ASSET]["browser_download_url"], assets["SHA256SUMS.txt"]["browser_download_url"]


def current(root=ROOT):
    try:
        data = json.loads((root / "current.json").read_text(encoding="utf-8"))
        target = (root / data["folder"]).resolve()
        if target.is_relative_to((root / "versions").resolve()) and (target / "Backburn.exe").is_file():
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def install_archive(archive, digest, version, root=ROOT):
    """Verify before extracting; activate a complete version and retain old versions."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise ValueError("Invalid release version")
    actual = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    if actual != digest.lower():
        raise ValueError("Download checksum mismatch; existing installation is unchanged")
    versions = root / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="staging-", dir=versions) as temp:
        staging = Path(temp)
        with zipfile.ZipFile(archive) as bundle:
            if sum(i.file_size for i in bundle.infolist()) > 1024**3:
                raise ValueError("Release is too large")
            for info in bundle.infolist():
                target = (staging / info.filename).resolve()
                if (
                    not target.is_relative_to(staging.resolve())
                    or ":" in info.filename
                    or "\\" in info.filename
                ):
                    raise ValueError("Unsafe archive path")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Archive links are not allowed")
            bundle.extractall(staging)
        if not (staging / "Backburn.exe").is_file():
            raise ValueError("Release is missing Backburn.exe")
        folder = f"{version}-{actual[:12]}"
        destination = versions / folder
        if not destination.exists():
            # Windows tempfile directories use private ACLs. Copy into a fresh
            # installation directory so files inherit the destination's normal
            # permissions rather than carrying temporary-directory ACLs forever.
            incoming = versions / ("incoming-" + uuid.uuid4().hex)
            shutil.copytree(staging, incoming)
            incoming.rename(destination)
        data = {"version": version, "folder": f"versions/{folder}", "sha256": actual}
        temporary = root / "current.tmp"
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(root / "current.json")
    return data


def download_install(release, root=ROOT):
    version, url, hashes_url = release
    lines = fetch(hashes_url).decode("utf-8").splitlines()
    digest = next(
        (
            line.split()[0]
            for line in lines
            if len(line.split()) == 2 and line.split()[1].lstrip("*") == ASSET
        ),
        None,
    )
    if digest is None or not re.fullmatch("[a-fA-F0-9]{64}", digest):
        raise ValueError("Release has no valid checksum")
    with tempfile.TemporaryDirectory(prefix="backburn-download-") as temp:
        archive = Path(temp) / ASSET
        archive.write_bytes(fetch(url))
        return install_archive(archive, digest, version, root)


def install_launcher(root=ROOT):
    if not getattr(sys, "frozen", False):
        return
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "Backburn Launcher.exe"
    source = Path(sys.executable)
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    script = """$shell = New-Object -ComObject WScript.Shell
$locations = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($location in $locations) {
  $shortcut = $shell.CreateShortcut((Join-Path $location 'Backburn.lnk'))
  $shortcut.TargetPath = $env:BACKBURN_LAUNCHER_PATH
  $shortcut.WorkingDirectory = $env:BACKBURN_INSTALL_PATH
  $shortcut.Description = 'Play Backburn and install updates from GitHub'
  $shortcut.Save()
}
"""
    env = dict(os.environ, BACKBURN_LAUNCHER_PATH=str(destination), BACKBURN_INSTALL_PATH=str(root))
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main():
    import tkinter as tk
    from tkinter import messagebox

    window = tk.Tk()
    window.title("Backburn / Launcher")
    window.geometry("620x430")
    window.resizable(False, False)
    window.configure(bg="#10191d")
    events = queue.Queue()
    release = [None]
    busy = [False]
    tk.Label(window, text="B A C K B U R N", font=("Segoe UI", 30, "bold"), fg="#eae8d8", bg="#10191d").pack(
        pady=(32, 4)
    )
    tk.Label(
        window, text="W I L D F I R E   C O M M A N D", font=("Segoe UI", 11), fg="#ee9d52", bg="#10191d"
    ).pack()
    status = tk.StringVar(value="Checking GitHub for the latest release…")
    tk.Label(
        window,
        textvariable=status,
        font=("Segoe UI", 12),
        fg="#bacfc8",
        bg="#10191d",
        wraplength=550,
        height=4,
    ).pack(pady=12)

    def work(fn, name):
        def execute():
            try:
                events.put((name, fn()))
            except Exception as exc:
                events.put(("error", str(exc)))

        threading.Thread(target=execute, daemon=True).start()

    def play():
        data = current()
        if not data:
            return
        executable = ROOT / data["folder"] / "Backburn.exe"
        subprocess.Popen([str(executable)], cwd=executable.parent)
        window.destroy()

    def update():
        if busy[0] or release[0] is None:
            return
        busy[0] = True
        update_button.config(state="disabled")
        status.set("Downloading and verifying the release. Your saves stay in place…")

        def perform():
            data = download_install(release[0])
            install_launcher()
            return data

        work(perform, "installed")

    play_button = tk.Button(
        window,
        text="PLAY INSTALLED GAME",
        command=play,
        font=("Segoe UI", 13, "bold"),
        bg="#ee9d52",
        fg="#10191d",
        relief="flat",
        width=36,
        height=2,
        state="normal" if current() else "disabled",
    )
    play_button.pack(pady=4)
    update_button = tk.Button(
        window,
        text="Install / update",
        command=update,
        font=("Segoe UI", 11),
        bg="#263b3d",
        fg="#eae8d8",
        relief="flat",
        width=43,
        height=2,
        state="disabled",
    )
    update_button.pack(pady=5)
    tk.Button(
        window,
        text="Release notes on GitHub",
        command=lambda: webbrowser.open(f"https://github.com/{REPO}/releases"),
        bg="#10191d",
        fg="#7cc9b3",
        relief="flat",
    ).pack()
    tk.Label(
        window,
        text="No account needed • No admin required • Offline play after installation",
        bg="#10191d",
        fg="#99ada8",
        font=("Segoe UI", 9),
    ).pack(pady=13)

    def poll():
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == "release":
                    release[0] = data
                    installed = current()
                    same = installed and installed["version"] == data[0]
                    status.set(
                        f"{data[0]} is installed. Ready to play."
                        if same
                        else f"{data[0]} is available. "
                        + ("Update when you're ready." if installed else "Install Backburn to this computer.")
                    )
                    update_button.config(
                        state="disabled" if same else "normal",
                        text="Up to date"
                        if same
                        else ("Install update" if installed else "Install Backburn"),
                    )
                elif kind == "installed":
                    busy[0] = False
                    status.set(f"{data['version']} is ready. A Backburn shortcut is on your desktop.")
                    play_button.config(state="normal")
                    update_button.config(text="Up to date", state="disabled")
                else:
                    busy[0] = False
                    status.set("Update unavailable. Installed games can still be played.\n" + str(data)[:180])
                    play_button.config(state="normal" if current() else "disabled")
                    if release[0]:
                        update_button.config(state="normal")
        except queue.Empty:
            pass
        window.after(100, poll)

    def close():
        if busy[0] and not messagebox.askyesno(
            "Download in progress", "Cancel and close? Your installed game will stay unchanged."
        ):
            return
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", close)
    work(latest_release, "release")
    poll()
    window.mainloop()


if __name__ == "__main__":
    if "--install-local" in sys.argv:
        i = sys.argv.index("--install-local")
        install_archive(Path(sys.argv[i + 1]), sys.argv[i + 2], sys.argv[i + 3])
        install_launcher()
    else:
        main()

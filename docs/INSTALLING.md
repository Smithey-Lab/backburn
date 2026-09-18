# Install and update

Download Backburn-Setup.exe from https://github.com/Smithey-Lab/backburn/releases/latest.
Open it and click Install Backburn. It installs for your Windows account without admin
rights and creates Desktop and Start Menu shortcuts named Backburn.

The shortcut opens the launcher. Play installed game works without an internet connection.
The launcher checks GitHub for a newer version. Click Install update when one is available;
updates are never applied in the middle of a running game.

Locations:

- Launcher: `%LOCALAPPDATA%\Backburn\Backburn Launcher.exe`
- Game versions: `%LOCALAPPDATA%\Backburn\versions`
- Saves/settings/scores: `%LOCALAPPDATA%\Backburn\userdata`
- Active version record: `%LOCALAPPDATA%\Backburn\current.json`

The updater verifies SHA-256, rejects unsafe archive entries, stages a complete game, and
then switches the active version. Old versions and saves remain intact. If an update fails,
the previous game can still launch. A newer installer can refresh the launcher itself.

For portable play, extract the zip and run Backburn.exe. Python is not required.
Windows binaries are unsigned; the first launch may show an unknown publisher warning.

To uninstall, close the game and launcher, remove the Backburn shortcuts, and remove the
Backburn folder under Local AppData. Back up userdata first if you want to retain saves.

For a bug report, include the game version, scenario and reproduction steps. If the game
crashes, its log is in userdata/crash.log. Review logs for private information before sharing.

# Security policy

The latest published version receives security fixes. Report vulnerabilities privately
through the repository's Security tab (Report a vulnerability). Do not include credentials
in issues. Never publish save files containing personal information.

The launcher uses HTTPS GitHub Releases from Smithey-Lab/backburn and verifies the release
SHA-256 before extracting. Archive paths and symlinks are validated. New versions are
staged before activation; failed downloads leave the installed version available. Saves
live outside version directories. Checksums detect corruption; they do not protect against
a compromised GitHub maintainer account. Windows binaries are currently unsigned.

The game works offline and has no telemetry, account, or multiplayer service. The launcher
contacts GitHub only to check and download releases. No savegames are uploaded.

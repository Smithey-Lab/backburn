# Repository workflow

`main` holds releases. `develop` is the integration branch. Work uses `feature/...` or
`fix/...` branches and pull requests. Required CI checks run lint/format checks, tests on
Windows and Linux, dependency auditing, and Windows packaging. CodeQL scans Python.
Actions are pinned to commits; Dependabot checks Python and Actions dependencies weekly.

Branch protections require passing checks, pull requests, resolved discussions and linear
history. Force pushes and deletion are disabled. Review count is zero for a solo maintainer;
add required reviewers when another maintainer joins. Administrators also follow the rules.

To publish a release, update both version strings in pyproject.toml and backburn/__init__.py,
update docs/RELEASE_NOTES.md, merge passing changes to main, and push a matching vX.Y.Z tag.
The Release workflow repeats tests and auditing, builds Windows artifacts, smoke-tests the
packaged game, writes SHA256SUMS.txt, and publishes GitHub Release assets.

Never commit credentials or local saves. Secret scanning and push protection are enabled
on GitHub. SECURITY.md describes private reporting and update trust boundaries.

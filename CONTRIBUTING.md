# Contributing

Use a feature or fix branch and open a pull request against develop. Release changes go
from develop to main. Run pytest, ruff check, ruff format --check and the generated-table
check before opening a PR. See docs/REPOSITORY.md for release and security practices.

Keep simulation changes independent of rendering. Add behavioral tests for mechanics,
save compatibility and updater safety. Use original assets with clear provenance.
Do not include local saves, private research documents, tokens or large build artifacts.
The project currently retains all rights reserved; discuss external contributions first.

# PebbleOS Versioning

PebbleOS uses semantic versioning (`MAJOR.MINOR.PATCH`).

- `MAJOR`: breaking behavior or interface changes.
- `MINOR`: backward-compatible feature additions.
- `PATCH`: backward-compatible fixes and hardening.

## Source of Truth

- Repository version: `VERSION`
- Human changelog: `CHANGELOG.md`
- Runtime-visible constants: `pebble_system/lib/base.peb`

All three should be updated in one change when cutting a release.

## Release Process

1. Update `VERSION`.
2. Add a new section to `CHANGELOG.md`.
3. Sync runtime-visible version constants in `system/lib/base.peb`.
4. Verify `uname -r`, `uname -v`, and `version` output.
5. Run tests:
   - `python -m unittest discover -s tests`
   - optional slow suite as needed
6. Tag release in git:
   - `git tag vX.Y.Z`
   - `git push origin vX.Y.Z`

## Current Baseline

- Current release baseline is `0.2.0`.

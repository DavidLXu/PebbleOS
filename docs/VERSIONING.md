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
5. Verify the supported Python baseline:
   - PebbleOS currently supports Python 3.9+
6. Run tests:
   - `python3 -m unittest discover -s tests`
   - `PEBBLE_RUN_SLOW_TESTS=1 python3 -m unittest discover -s tests` for releases that touch shell, process, job-control, or TTY behavior
7. Confirm boot/runtime safety defaults still match the docs:
   - `system/...` remains read-only unless explicitly unlocked
   - HTTPS certificate failures still fail closed unless `PEBBLE_INSECURE_TLS=1` or `--insecure-tls` is used
   - boot failures still exit non-zero
8. Tag release in git:
   - `git tag vX.Y.Z`
   - `git push origin vX.Y.Z`

## Current Baseline

- Current release baseline is `0.2.0`.

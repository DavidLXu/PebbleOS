# Changelog

All notable changes to PebbleOS are documented in this file.

Versioning policy: [docs/VERSIONING.md](docs/VERSIONING.md)

## [0.1.2] - 2026-03-06

Maintenance release focused on simulator and shell/runtime quality improvements.

- Updated version metadata to `0.1.2` across repository and runtime-visible APIs.
- Added interactive `physics` command (`system/bin/physics.peb`) with 2D text rendering, gravity/drag tuning, object collisions, and `air`/`liquid`/`solid` material regions.
- Fixed `physics` interactive state loss by persisting simulator world/object state between input cycles.
- Added detailed physics simulator documentation (`docs/PHYSICS_SIMULATOR.md`) including command grammar, mechanics, examples, and text screenshots.
- Included related shell/help and regression test updates for recent runtime features.

## [0.1.1] - 2026-03-06

Maintenance release that consolidates pending PebbleOS runtime, shell, userland,
and documentation updates accumulated after `0.1.0`.

- Version metadata updated to `0.1.1` across repository and runtime-visible APIs.
- Includes all currently staged workspace improvements and fixes since `0.1.0`.

## [0.1.0] - 2026-03-06

Initial public baseline for the modern PebbleOS architecture.

### Core runtime and language

- Established Pebble as the runtime control language for system behavior, not only for user scripts.
- Added interpreter mode (`run`) and bytecode VM mode (`exec`) with compatible program launch semantics.
- Added structured VM state with explicit value stack and frame stack.
- Added bytecode snapshot/restore plumbing for VM task lifecycle.
- Expanded language features to include:
  - assignments, indexing, arithmetic/comparison operators, loops, functions
  - list/dict support and module imports
  - minimal error handling with `try/except`, `except err`, and `raise`
  - bootstrap class support (constructor, instance methods, bound methods)
  - bootstrap first-class function values

### Memory model

- Introduced Pebble-visible runtime memory layer:
  - `memory` module for explicit cell-based RAM operations
  - `heap` module for object allocation over runtime memory
- Added block ops (`copy`, `move`, `slice`, `store`, `compare`, `zero`) and allocation marks/resets.
- Kept model intentionally simple (arena-style semantics) to support iterative runtime evolution.

### Process/thread/scheduler direction

- Added VM-backed task scheduler model with foreground/background execution paths.
- Added shell-level task controls (`jobs`, `fg`, `bg`, `ps`), including process inspection.
- Added bootstrap thread API:
  - `thread_spawn_source`, `thread_join`, `thread_status`, `thread_self`, `thread_list`
- Added bootstrap mutex API:
  - `mutex_create`, `mutex_lock`, `mutex_try_lock`, `mutex_unlock`, `mutex_list`
- Added thread state surface including `blocked-mutex`.

### Filesystem and execution modes

- Moved from flat storage model to rooted path model with directories and mounts.
- Added selectable filesystem backends:
  - `hostfs`, `mfs`, `mfs-import`, `vfs-import`, `vfs-persistent`
- Added Pebble runtime filesystem behaviors for in-memory and virtualized flows.
- Mounted `system/...` runtime tree for Pebble-managed OS scripts and commands.

### Shell and userland

- Migrated shell behavior into Pebble runtime command layer (`system/shell.peb`) while keeping Python bootloader thin.
- Added Unix-style shell primitives:
  - PATH lookup
  - redirection (`>`, `>>`, `<`, `2>`, `2>&1`)
  - pipelines (`|`)
  - background launch (`&`)
- Added compatibility launchers (`run`, `exec`, `runbg`, `execbg`) and direct command model.
- Added extensive command set under `system/bin`, including editors, file tools, process viewers, and scripting helpers.
- Added `top`/`htop` task viewers and `tty` command support.
- Added `pebble` launcher and script execution compatibility flows.
- Added minimal `gcc` command translating a small C subset to runnable Pebble code.

### Terminal and device abstraction

- Added bootstrap `/dev`-style device paths through fd layer:
  - `/dev/tty`, `/dev/stdin`, `/dev/stdout`, `/dev/stderr`, `/dev/null`
- Added runtime-level terminal write/flush/mode helpers and key-read behaviors.
- Improved attached foreground task terminal ownership behavior.

### Documentation and architecture

- Added/expanded architecture docs:
  - filesystem model
  - memory/runtime direction
  - language reference
  - ABI/process/thread/launcher/TTY semantics
  - Pebble vs Python language gap tracking
- Clarified host boundary: Python as bootloader/substrate, Pebble as visible OS/runtime layer.

### Quality and validation

- Built broad automated test coverage across:
  - language parsing/execution
  - bytecode VM behavior
  - shell command behavior
  - filesystem modes and runtime integration
  - process/thread/mutex bootstrap behavior
- Added targeted regression tests for command semantics and runtime compatibility changes.

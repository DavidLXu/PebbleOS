# Pebble OS Implementation Plan

## Goal

This document records the implementation plan and current execution state for
turning Pebble OS into a more Linux-like, Pebble-native system.

The high-level direction remains:

- keep Python as a minimal host substrate
- move policy and visible system behavior into Pebble
- define Pebble-visible kernel/runtime interfaces first
- add Linux-like process, fd, and shell behavior in stages

## Current Status

### Completed

#### Phase 0: ABI boundary

Completed:

- host function inventory and syscall-family classification
- ABI notes in [`docs/ABI.md`](docs/ABI.md)
- first Pebble kernel/module split

Key files:

- [`docs/ABI.md`](docs/ABI.md)
- [`pebble_system/kernel/syscall.peb`](pebble_system/kernel/syscall.peb)
- [`pebble_system/lib/base.peb`](pebble_system/lib/base.peb)

#### Phase 1: Pebble kernel split and stable runtime ABI

Completed in transition form:

- nested Pebble module imports such as `system.kernel.proc`
- Pebble kernel module entry points for base constants, syscall inventory, and
  process wrappers
- runtime compatibility exports preserved through
  [`pebble_system/runtime.peb`](pebble_system/runtime.peb)

Key files:

- [`pebble_bootloader/lang.py`](pebble_bootloader/lang.py)
- [`pebble_system/runtime.peb`](pebble_system/runtime.peb)
- [`pebble_system/kernel/proc.peb`](pebble_system/kernel/proc.peb)

#### Phase 2: Real process model before more commands

Implemented as a minimal but usable process-model skeleton:

- unified host-side `HostProcessRecord` view
- process fields: `pid`, `ppid`, `pgid`, `sid`, `cwd`, `argv`, `exit_status`
- `ps`, `jobs`, `fg` routed through shared process-state collection
- `wait_process`, `reap_process`, and `wait_child_process`
- `SIGCHLD`, `SIGINT`, `SIGTSTP` event recording surface
- foreground process-group tracking and child-process listing

Key files:

- [`docs/PROCESS.md`](docs/PROCESS.md)
- [`pebble_bootloader/shell.py`](pebble_bootloader/shell.py)
- [`pebble_system/kernel/proc.peb`](pebble_system/kernel/proc.peb)

#### Phase 3: File descriptor, redirection, and pipe layer

Completed in a pragmatic first version:

- minimal fd host ABI:
  - `fd_open`
  - `fd_read`
  - `fd_write`
  - `fd_close`
  - `fd_stat`
  - `fd_readdir`
- Pebble runtime wrappers:
  - `sys_fd_open`
  - `sys_fd_read`
  - `sys_fd_write`
  - `sys_fd_close`
  - `sys_fd_stat`
  - `sys_fd_readdir`
- shell redirection:
  - `>`
  - `>>`
  - `<`
  - `2>`
  - `2>&1`
- multi-stage pipelines
- fd-backed stdio routing in the host shell
- file-backed and pipe-backed fd records using the same shell-side table
- `/system/bin/*.peb` external command model for text and filesystem utilities
- Pebble userland commands:
  - `echo`
  - `wc`
  - `cat`
  - `head`
  - `tail`
  - `ls`
  - `pwd`
  - `mkdir`
  - `rmdir`
  - `time`
  - `sync`
  - `touch`
  - `edit`
  - `rm`
  - `cp`
  - `mv`
  - `lang`

Key files:

- [`pebble_bootloader/shell.py`](pebble_bootloader/shell.py)
- [`pebble_system/runtime.peb`](pebble_system/runtime.peb)
- [`pebble_system/shell.peb`](pebble_system/shell.peb)
- [`pebble_system/bin/echo.peb`](pebble_system/bin/echo.peb)
- [`pebble_system/bin/wc.peb`](pebble_system/bin/wc.peb)
- [`pebble_system/lib/cli.peb`](pebble_system/lib/cli.peb)

### In Progress

## Remaining Plan

### Phase 4: Linux-like shell and base userland

Plan:

1. Reduce shell builtins toward:
   - `cd`
   - `exit`
   - `jobs`
   - `fg`
   - `bg`
   - `export`
   - `set`
2. Add command search paths and external command dispatch conventions.
3. Move more utilities into Pebble userland files under `system/bin`.
4. Add Linux-style text and filesystem utilities beyond the current shell
   builtins.

Acceptance:

- the shell becomes thinner
- command composition feels more Linux-like
- more visible system behavior lives in Pebble files, not Python

### Phase 5: Init and service management

Plan:

1. Add Pebble init entrypoint.
2. Add service definitions and service lifecycle control.
3. Add persistent boot/runtime logging.

### Phase 6: Security and multi-user skeleton

Plan:

1. Add user/group model placeholders.
2. Add ownership and mode bits to filesystem metadata.
3. Add capability-oriented checks for privileged operations.

### Phase 7: Filesystem maturity and mount layout

Plan:

1. Add richer metadata and mount records.
2. Add special filesystems such as `procfs` and `tmpfs`.
3. Make Pebble-side filesystem policy more independent from host layout.

### Phase 8: Networking as a Pebble service layer

Plan:

1. Define Pebble-visible networking ABI.
2. Keep Python only as socket substrate.
3. Build user-space networking tools on top.

### Phase 9: Package management and self-hosting toolchain

Plan:

1. Add package metadata and installation layout.
2. Add package manager commands.
3. Add Pebble-written tooling for inspecting and building Pebble code.

### Phase 10: Shrink Python further

Plan:

1. Remove remaining shell-policy leakage from Python.
2. Keep Python focused on VM and host primitive substrate only.
3. Make Pebble the normal place where OS behavior evolves.

## Notes

- This document is an execution record, not a frozen spec.
- ABI details live in [`docs/ABI.md`](docs/ABI.md).
- Process-model details live in [`docs/PROCESS.md`](docs/PROCESS.md).

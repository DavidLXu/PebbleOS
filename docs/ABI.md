# Pebble OS ABI

## Goal

Pebble OS now treats the Python bootloader as a host substrate, not as the
place where OS policy should live. New system features should first define a
Pebble-visible ABI and only then map missing primitives to the host.

This document is the phase-0 inventory and migration target for that ABI.

## Design Rules

- Pebble shell and runtime code should call Pebble kernel modules first.
- Python should expose atomic host primitives, not shell-specific policy.
- Host functions should be classified into syscall families.
- New features should extend the Pebble ABI before they extend Python.

## Current Host Function Inventory

### `fs`

- `raw_list_files`
- `raw_file_exists`
- `raw_create_file`
- `raw_modify_file`
- `raw_delete_file`
- `raw_file_time`
- `raw_read_file`
- `raw_write_file`
- `raw_directory_exists`
- `raw_make_directory`
- `raw_remove_directory`
- `raw_directory_empty`
- `list_files`
- `file_time`
- `file_exists`
- `directory_exists`
- `create_file`
- `modify_file`
- `delete_file`
- `make_directory`
- `remove_directory`
- `directory_empty`
- `cwd`
- `chdir`
- `filesystem_file_count`
- `filesystem_total_bytes`
- `filesystem_sync`

### `proc`

- `run_program`
- `exec_program`
- `start_background_job`
- `list_background_jobs`
- `list_processes`
- `foreground_job`
- `vm_create_task`
- `vm_step_task`
- `vm_task_status`
- `vm_take_task_output`
- `vm_snapshot_task`
- `vm_restore_task`
- `vm_drop_task`

### `term`

- `term_write`
- `term_flush`
- `term_clear`
- `term_move`
- `term_hide_cursor`
- `term_show_cursor`
- `term_read_key`
- `term_read_key_timeout`
- `term_rows`
- `term_cols`

### `clock`

- `current_time`

### `error`

- `runtime_error`

## Pebble Kernel Modules

Milestone 1 introduces these Pebble entry points:

- [`pebble_system/lib/base.peb`](pebble_system/lib/base.peb)
- [`pebble_system/kernel/syscall.peb`](pebble_system/kernel/syscall.peb)
- [`pebble_system/kernel/proc.peb`](pebble_system/kernel/proc.peb)
- [`pebble_system/kernel/thread.peb`](pebble_system/kernel/thread.peb)
- [`pebble_system/kernel/term.peb`](pebble_system/kernel/term.peb)

These modules currently provide:

- errno constants
- process state constants
- default process context shape
- syscall-family inventory
- transition wrappers for process-oriented shell commands
- transition wrappers for terminal and TTY control

## Target Syscall Families

- `fs`
- `proc`
- `thread`
- `term`
- `clock`
- `memory`
- `service`
- `net`

## Transitional Mapping

For now, Pebble kernel modules still delegate to the existing host functions.
This is intentional. The boundary has been named and documented before deeper
refactors such as a unified process table or fd layer.

## Terminal And TTY ABI

Pebble terminal programs should prefer
[`pebble_system/kernel/term.peb`](pebble_system/kernel/term.peb)
instead of calling host-exposed `term_*` names directly.

Current terminal syscalls:

- `term.write`
- `term.flush`
- `term.clear`
- `term.move`
- `term.hide_cursor`
- `term.show_cursor`
- `term.read_key`
- `term.read_key_timeout`
- `term.rows`
- `term.cols`
- `term.owner_pgid`
- `term.mode`
- `term.state`

Current TTY state fields:

- `owner_pgid`
- `mode`
- `interactive`
- `foreground_raw`
- `rows`
- `cols`

This keeps interactive programs such as `nano`, `bash`, and `top` on a
Pebble-visible ABI even though raw terminal access is still bridged by the
Python host substrate.

## Thread ABI

Milestone A exposes a bootstrap thread ABI through
[`pebble_system/kernel/thread.peb`](pebble_system/kernel/thread.peb).

Current thread syscalls:

- `thread.spawn_source`
- `thread.join`
- `thread.status`
- `thread.self`
- `thread.yield`
- `thread.list`

Current sync syscalls:

- `sync.mutex_create`
- `sync.mutex_lock`
- `sync.mutex_try_lock`
- `sync.mutex_unlock`
- `sync.mutex_list`

This initial thread layer is intentionally built on the existing VM task
scheduler. It is a process-shared execution bootstrap, not a final POSIX thread
implementation. The first sync layer is similarly bootstrap-oriented: mutexes
are thread-owned, blocked lockers surface as `blocked-mutex`, and wakeup is
coordinated by the host scheduler.

## Device FD Bootstrap

The host substrate now exposes a minimal device-file bridge through the
existing fd API. Pebble programs can open these logical device paths with
`fd_open()` / `sys_fd_open()`:

- `/dev`
- `/dev/tty`
- `/dev/stdin`
- `/dev/stdout`
- `/dev/stderr`
- `/dev/null`

This is still a bootstrap device model rather than a full `devfs`, but it
shrinks the terminal bootloader boundary toward generic fd/device semantics.
Pebble runtime path functions now also expose these device nodes through the
visible filesystem view, so commands such as `cd /dev`, `ls`, and `find /dev`
can inspect them even though the underlying host filesystem does not contain a
real on-disk `/dev` directory.

## Error Model

Milestone 1 standardizes the Pebble-side symbolic errno table:

- `OK = 0`
- `PERM = 1`
- `NOENT = 2`
- `IO = 5`
- `AGAIN = 11`
- `NOMEM = 12`
- `BUSY = 16`
- `EXIST = 17`
- `NOTDIR = 20`
- `ISDIR = 21`
- `INVAL = 22`
- `NOSYS = 38`

Python still raises `PebbleError` internally. Pebble modules should increasingly
translate host failures into stable Pebble-visible error conventions.

# Pebble OS Filesystem

## Overview

Pebble OS exposes a unified Pebble-side filesystem API, but it can run on top
of multiple storage backends.

The public behavior lives in [`system/runtime.peb`](/Users/xulixin/LX_OS/pebble_system/runtime.peb).
Shell commands call Pebble runtime helpers such as:

- `list_files()`
- `file_exists(name)`
- `directory_exists(name)`
- `read_file(name)`
- `write_file(name, text)`
- `delete_file(name)`
- `make_directory(name)`
- `remove_directory(name)`
- `file_time(name)`

Those helpers route to one of the filesystem modes below.

The visible shell model is now a rooted filesystem, not a single flat folder.
Pebble supports:

- absolute paths like `/docs/note.txt`
- relative paths from the current working directory
- `.` and `..` normalization inside the Pebble OS root
- mounted runtime files under `system/...`
- directories through `cd`, `pwd`, `mkdir`, and `rmdir`

## Modes

### `hostfs`

`hostfs` is the direct host-backed mode.

- User files and directories live under [`pebble_disk/`](/Users/xulixin/LX_OS/pebble_disk)
- Python and the host OS can inspect them directly
- `run` and `nano` operate on real host files
- This is the fastest and simplest mode

Use `hostfs` when you want speed, easy debugging, and transparent host files.

### `mfs`

`mfs` is the Pebble memory filesystem mode.

- The session starts from an empty Pebble-managed filesystem
- User files and directories live only in memory during the session
- The VFS backing image is intercepted by the bootloader and kept in memory
- Nothing is written to [`pebble_disk/`](/Users/xulixin/LX_OS/pebble_disk) unless you explicitly run `sync`

Use `mfs` when you want a fast Pebble-native filesystem without automatic
persistence.

### `mfs-import`

`mfs-import` boots from the host filesystem once and then switches into the
Pebble memory filesystem.

- At first access, Pebble imports current host user files into the in-memory filesystem
- The running session then reads and writes only the in-memory Pebble store
- Host files are the import source, not a live mirror
- Nothing is written back to disk unless you explicitly run `sync`

Use `mfs-import` when you want a fast in-memory session that starts from the
current host-visible files.

### `vfs-import`

`vfs-import` boots from the host filesystem and then switches into Pebble's
virtual filesystem for the session.

- At boot, Pebble imports current host user files into a virtual disk image
- The running session then reads and writes the virtual filesystem
- Host files are the boot source, not a live mirror
- The VFS backing image is written to disk automatically

Use `vfs-import` when you want to experiment with Pebble-native filesystem
behavior without preserving a long-lived VFS state.

### `vfs-persistent`

`vfs-persistent` keeps Pebble's virtual filesystem as the source of truth.

- User files live in a Pebble VFS backing store
- The VFS persists across boots
- Host user files are no longer the active authoritative store
- The backing store lives in `.__pebble_vfs__.db`

Use `vfs-persistent` when you want Pebble itself to define filesystem behavior.

## Why Multiple Modes Exist

Pebble OS intentionally keeps both a practical host-backed mode and a
Pebble-native evolution path.

- `hostfs` is stable and fast
- `mfs*` modes keep Pebble semantics but avoid repeated host backing-store writes
- `vfs-*` modes are slower, but Pebble controls more of the semantics and persistence

This allows filesystem innovation in Pebble without forcing every workflow
through the slower VFS path.

## Synchronization Model

The modes are not continuously synchronized.

- `hostfs` reads and writes host files directly
- `mfs` starts empty, runs entirely in memory, and only writes a snapshot if you call `sync`
- `mfs-import` imports host files once, runs entirely in memory, and only writes a snapshot if you call `sync`
- `vfs-import` imports host files and directory structure at boot, then diverges for that session
- `vfs-persistent` reloads the saved VFS image and does not re-import unless you choose another mode

This avoids hidden merge rules and avoids losing Pebble-native metadata that
does not map cleanly onto host files.

## Explicit Sync

Pebble OS exposes a runtime-level `sync` command for memory filesystems.

- `sync` is available in `mfs` and `mfs-import`
- It writes the current in-memory filesystem snapshot into `.__pebble_vfs__.db`
- It does not merge back into ordinary host files under [`pebble_disk/`](/Users/xulixin/LX_OS/pebble_disk)

This makes persistence explicit instead of automatic.

## Shadow Files

In Pebble-managed filesystem modes, user files are not always real host files. Some existing runtime
paths still need host-visible files, so Pebble OS uses temporary shadow files.

Current uses:

- `run` for executing a VFS-backed program through the existing host path
- `nano` for editing a VFS-backed file through the existing editor flow

The flow is:

1. Copy a Pebble VFS file into a temporary host file
2. Run the existing program/editor path on that file
3. Copy the result back into the VFS if needed
4. Delete the temporary file

These shadow files are implementation details, not the real source of truth.

## Host Bridge

The Python bootloader still provides a raw host bridge. Pebble runtime code
uses these lower-level operations to build the public filesystem behavior:

- `raw_list_files()`
- `raw_file_exists(name)`
- `raw_directory_exists(name)`
- `raw_create_file(name, text)`
- `raw_modify_file(name, text)`
- `raw_delete_file(name)`
- `raw_make_directory(name)`
- `raw_remove_directory(name)`
- `raw_directory_empty(name)`
- `raw_read_file(name)`
- `raw_write_file(name, text)`
- `raw_file_time(name)`
- `capture_text()`
- `run_program(name, argv)`
- `runtime_error(message)`

Pebble runtime then decides whether those raw operations back `hostfs` directly
or feed into the Pebble VFS layer.

## Mounted System Paths

`system/...` remains special.

- Runtime files under [`pebble_system/`](/Users/xulixin/LX_OS/pebble_system) are mounted as `system/...`
- They are used for bootstrapping and core system behavior
- Even in `mfs`, `mfs-import`, and VFS modes, these files remain host-backed

This keeps the runtime boot path stable while allowing user files to move into
Pebble's own virtual filesystem.

## Current Working Directory

Pebble shell now keeps a current working directory and shows it in the prompt.

Examples:

- `pwd` prints the current Pebble path
- `cd /` moves to the root
- `cd demos` moves relative to the current directory
- `cd ../notes` normalizes relative navigation
- `mkdir notes`
- `touch notes/todo.txt`
- `cat system/runtime.peb` reads the mounted runtime tree from any working directory

Path normalization prevents escaping the Pebble OS root.

## VFS Directory Representation

Pebble's virtual filesystem stores directories using internal marker entries.
That lets the VFS preserve empty directories as well as regular files, while
still storing everything in a simple Pebble-managed backing database.

## Layer Split

Filesystem behavior is split across two layers:

- [`pebble_bootloader/`](/Users/xulixin/LX_OS/pebble_bootloader) provides raw host operations, mount handling, and the in-memory interception used by `mfs` and `mfs-import`
- [`system/runtime.peb`](/Users/xulixin/LX_OS/pebble_system/runtime.peb) defines the Pebble-visible filesystem API, path normalization, VFS logic, and mode selection

This keeps the Python layer small while moving filesystem policy into Pebble.

# PebbleOS Shell And Login Semantics

This document describes how a PebbleOS shell session is created and initialized as of March 1, 2026.

## Interactive Shell

The interactive shell is implemented by the host bootloader plus the Pebble-managed shell runtime:

- host entry: [`pebble_bootloader/shell.py`](pebble_bootloader/shell.py)
- shell behavior: [`pebble_system/shell.peb`](pebble_system/shell.peb)

The host owns terminal and process substrate details. Pebble owns command behavior, help text, launcher policy, and most visible shell semantics.

## Login Initialization

When a shell session starts, PebbleOS ensures these files exist:

- `/etc/profile`
- `/etc/passwd`
- `/etc/group`
- `/etc/fstab`

Then the shell loads `/etc/profile` into the current shell session.

Current default placeholders are minimal bootstrap files, not full multi-user account management.

## Environment Behavior

The current shell session keeps a mutable environment map.

Relevant commands:

- `set`
- `export`
- `env`
- `source FILE`

Single-command assignment prefixes also work, for example:

```sh
FOO=bar env
```

That assignment applies only to the launched command.

## `source` And `sh`

PebbleOS currently has two file-driven shell entrypoints:

- `source FILE`
- `sh FILE`

Current semantics:

- `source FILE` runs the file in the current shell session
- `sh FILE` currently maps to the same session behavior

This is an intentional bootstrap compromise. A future shell process model may turn `sh FILE` into a separate shell process with its own session state.

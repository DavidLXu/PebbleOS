# PebbleOS Launcher Semantics

This document defines the user-visible command launch rules for PebbleOS as of March 1, 2026.

## Lookup Order

When the interactive shell receives a command, it resolves it in this order:

1. Shell builtins in [`/Users/xulixin/LX_OS/pebble_system/shell.peb`](/Users/xulixin/LX_OS/pebble_system/shell.peb)
2. `PATH` lookup for `/system/bin/*.peb` and `/system/sbin/*.peb`
3. `/bin/...` compatibility mapping to `/system/bin/...`
4. Direct Pebble program launch from the current directory, with `.peb` implied when omitted

Examples:

- `echo hello`
- `wc notes.txt`
- `demo`
- `demo.peb`
- `/bin/sh script.sh`

## Preferred Launch Model

Preferred user-facing launch syntax is:

- `COMMAND [ARGS...]`
- `COMMAND &`

Background execution is controlled by shell syntax, not by separate launcher commands.

## Compatibility Launchers

The following commands still exist, but they are compatibility entry points rather than the preferred interface:

- `run FILE [ARGS...]`
- `exec FILE [ARGS...]`
- `runbg FILE [ARGS...]`
- `execbg FILE [ARGS...]`

Their purpose is to expose explicit interpreter-vs-bytecode behavior during the bootstrap phase.

Longer term, PebbleOS should move toward one launcher surface where:

- the shell chooses the execution path
- `&` is the normal background mechanism
- execution mode is an implementation detail unless the user explicitly asks for it

## `/bin/sh`

PebbleOS now exposes a standard shell entrypoint:

- `sh`
- `/bin/sh`

Current behavior is intentionally minimal:

- `sh FILE` executes shell commands from `FILE`
- `/bin/sh FILE` maps to the same command

This is a compatibility shell entrypoint, not yet a fully separate subshell process.

# Pebble OS Process Model

## Milestone 1 State

Pebble OS does not yet have a full Linux-like process table. It currently runs
with two host-backed execution forms:

- VM-backed bytecode tasks
- host-managed background worker jobs

Milestone 1 adds a Pebble-visible transition layer in
[`/Users/xulixin/LX_OS/pebble_system/kernel/proc.peb`](/Users/xulixin/LX_OS/pebble_system/kernel/proc.peb)
so shell commands stop depending directly on raw host function names.

## Pebble Process Context Shape

The transition context shape is:

- `pid`
- `ppid`
- `pgid`
- `sid`
- `cwd`
- `argv`
- `env`
- `uid`
- `gid`
- `umask`
- `path`

## Process States

- `ready`
- `running`
- `foreground`
- `done`
- `halted`
- `error`

## Next Steps

- unify VM tasks and background jobs into one process table
- add exit status and wait semantics
- add process groups and sessions
- route signals through the Pebble process model

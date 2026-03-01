# Pebble OS Threading

## Goal

Pebble OS threads should move toward Linux-style task semantics without
pretending the current VM is already a full POSIX process/thread kernel.

The intended direction is:

- processes own shared resources
- threads are the scheduler-visible execution units inside a process
- threads share process memory, cwd, env, and fd tables
- threads have their own execution state, stack, blocking reason, and exit data

## Current Milestone

Milestone A introduces a bootstrap thread API on top of the existing Pebble VM
task scheduler.

What it provides now:

- thread state constants
- a Pebble kernel thread module
- a Pebble kernel mutex module
- runtime wrappers for thread spawn/join/self/yield/list
- runtime wrappers for mutex create/lock/try-lock/unlock/list
- host-backed thread records built on VM tasks

What it does not claim yet:

- full `pthread_create(func, args)` support
- preemptive scheduling
- condition variables
- per-thread signal masks
- true multi-threaded shared-heap safety guarantees

## Data Model

### Process

- `pid`
- `ppid`
- `pgid`
- `sid`
- `cwd`
- `env`
- `fds`
- `threads`

### Thread

- `tid`
- `pid`
- `tgid`
- `state`
- `name`
- `argv`
- `cwd`
- `exit_status`
- `blocked_on`
- `attached`

## Thread States

- `ready`
- `running`
- `blocked-input`
- `blocked-tty`
- `blocked-mutex`
- `halted`
- `error`

These are bootstrap scheduler states. They intentionally align with the
existing VM task model so that later work can refine semantics instead of
rewriting the interface again.

## Pebble API

### Bootstrap spawn API

Current bootstrap creation uses source text because Pebble does not yet have a
stable first-class callable/thread ABI:

- `thread_spawn_source(name, source, argv) -> tid`

This creates a new VM-backed thread record in the current process.

### Lifecycle API

- `thread_join(tid) -> record`
- `thread_status(tid) -> state`
- `thread_self() -> tid`
- `thread_yield() -> 0`
- `thread_list() -> [record, ...]`

### Bootstrap mutex API

- `mutex_create() -> mid`
- `mutex_lock(mid) -> 0`
- `mutex_try_lock(mid) -> 1|0`
- `mutex_unlock(mid) -> 0`
- `mutex_list() -> [record, ...]`

The current bootstrap mutex model is intentionally small:

- mutex ownership is tracked by `tid`
- blocked lockers enter `blocked-mutex`
- unlocking wakes the next waiting thread
- `mutex_try_lock()` is non-blocking
- re-lock by the current owner succeeds as a bootstrap compatibility shortcut

`thread_join()` currently cooperatively steps the target thread until it reaches
a terminal state and then returns a record with at least:

- `tid`
- `pid`
- `state`
- `exit_status`
- `outputs`

## Linux Direction

The intended later evolution is:

1. `thread_spawn_source(...)`
2. `thread_spawn(func, args)`
3. richer mutex/condvar/futex-like waiting
4. thread-aware `top`/`ps`
5. signal mask and richer scheduler policy

Pebble OS should eventually resemble a Linux model where the scheduler runs
threads, while processes remain resource containers and signal/TTY ownership
boundaries.

## TTY And Threading

TTY ownership remains process-group based, not thread based.

- foreground read permission belongs to the foreground process group
- a specific thread may block on tty input
- blocking and wakeup affect the thread
- ownership and signal routing remain attached to process/session state

## Host Substrate Boundary

The Python bootloader should only provide:

- VM-backed thread creation
- thread stepping and status inspection
- thread output capture
- blocking/wakeup integration with tty/stdin

It should not grow a second shell-specific thread model.

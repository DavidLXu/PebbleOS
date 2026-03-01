# Pebble vs Python 3.x

This document compares Pebble to Python 3.x from the perspective of building a
modern operating system in Pebble-first layers.

## Baseline

Pebble already has enough to bootstrap a system:

- modules
- functions
- lists and dicts
- interpreter and bytecode execution
- a growing runtime ABI for process, TTY, fd, and threading work

But Pebble still lacks a number of Python 3.x features that matter once system
code grows larger.

## Priority Order

The ranking below is not about language completeness. It is about what most
blocks OS work.

### Priority 1: Error recovery

Python 3.x has:

- `try/except/finally`
- `raise`
- exception classes

Pebble needed at least a minimal recovery path so system code can probe, fall
back, and continue instead of aborting whole programs on the first runtime
error.

Current status:

- minimal `try: ... except:` is now supported
- minimal `raise expression` is now supported
- minimal `except err:` binding is now supported with string error payloads

Still missing:

- `finally`
- typed exception handling

### Priority 2: First-class callable/thread-friendly functions

Python 3.x has:

- first-class functions
- lambdas
- closures

Pebble still creates threads from source strings. That is workable for
bootstraping but not acceptable as the long-term API for a Linux-like system.

Needed next:

- `thread_spawn(func, args)`
- callable values
- eventually closures or a constrained equivalent

### Priority 3: Better structured data

Python 3.x has:

- classes
- dataclasses
- tuples
- enums

Pebble currently leans on dicts for everything. That works, but process,
thread, fd, mount, and service records get hard to reason about quickly.

Needed next:

- a lightweight record/struct form
- a small enum/constant pattern

### Priority 4: Stronger synchronization model

Python 3.x plus the host environment offers:

- `threading.Lock`
- `Condition`
- queues

Pebble now has bootstrap mutexes, but OS work will soon want:

- condition variables or channels
- timed waits
- sleep/wake coordination

### Priority 5: More expressive module and import behavior

Python 3.x has:

- `from ... import ...`
- aliasing
- package layouts

Pebble modules work, but system-scale code would benefit from:

- explicit export/import conventions
- cleaner namespace control

### Priority 6: Tooling parity

Python 3.x has a mature formatter/linter/test ecosystem. Pebble does not.

Needed later:

- formatter
- checker/linter
- module inspector
- package/build helpers

## Current Direction

Pebble should not try to clone all of Python 3.x. It should add the subset that
most reduces friction when writing:

- kernel/runtime modules
- shell and service code
- device and filesystem code
- concurrent programs

## Recommended Next Language Milestones

1. Minimal error handling: `try/except`
2. `raise`
3. First-class function values for `thread_spawn(func, args)`
4. `condvar` or channel-friendly primitives
5. lightweight records/structs

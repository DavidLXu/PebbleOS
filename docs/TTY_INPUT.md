# PebbleOS TTY And Input Model

This document summarizes the current PebbleOS terminal input model as of March 1, 2026.

## Why This Exists

PebbleOS now has interactive Pebble programs that need more than simple line input:

- `bash` uses `input()`
- `nano` uses `read_key()` and `read_key_timeout()`
- foreground jobs need `Ctrl-C`, `Ctrl-Z`, and terminal ownership semantics

That means the system needs a model for:

- keyboard response requests
- task blocking and wakeup
- raw mode vs cooked mode
- frontmost TTY ownership
- buffering fast key input so interactive apps do not lose keystrokes

## Two Kinds Of Input Requests

PebbleOS currently treats terminal input as two different kinds of request.

### 1. Line input

This is the `input()` path.

Characteristics:

- line-oriented
- prompt-driven
- better matched to shell-style interaction
- uses cooked terminal behavior while waiting for the line

Examples:

- `bash`
- any Pebble program that calls `input("name: ")`

### 2. Key input

This is the `read_key()` / `read_key_timeout()` path.

Characteristics:

- key-by-key
- suitable for editors, TUIs, and real-time interaction
- uses raw terminal behavior while the foreground task owns the TTY

Examples:

- `nano`
- `top`
- terminal games and live dashboards

## Cooked Mode vs Raw Mode

These are terminal I/O modes.

### Cooked mode

The terminal helps with input before the program receives it.

Behavior:

- input is line-buffered
- typed characters are echoed by the terminal
- line editing is mostly handled by the terminal
- better for `input()`

PebbleOS uses cooked mode when a foreground task is blocked on line input.

### Raw mode

The program handles keyboard input directly.

Behavior:

- input arrives one key at a time
- no normal line buffering
- terminal echo is not used in the normal shell style
- better for `read_key()` and `read_key_timeout()`

PebbleOS uses raw mode when a foreground task is blocked on TTY key input.

## Scheduler-Visible Task States

Foreground VM tasks can now block inside the scheduler model instead of falling out to a special host-only path.

Relevant task states:

- `ready`
- `running`
- `blocked-input`
- `blocked-tty`
- `halted`
- `error`

Meaning:

- `blocked-input`: waiting for a cooked-mode line from stdin
- `blocked-tty`: waiting for raw-mode terminal key input

## Keyboard Response Requests

Interactive Pebble programs do not read the host terminal directly from the VM scheduler thread.

Instead, they raise a scheduler-visible request:

- `input()` raises a line-input block request
- `read_key()` / `read_key_timeout()` raise a TTY-key block request

The foreground task remains the owner of the request, but the main foreground-attached loop is responsible for:

- waiting on the real terminal
- collecting the user input
- placing the result back into the task
- resuming the VM task

This keeps terminal access centralized while still letting the task stay in the VM task model.

## Frontmost TTY Ownership

PebbleOS currently treats the frontmost attached VM task as the TTY owner.

Practical rule:

- only the attached foreground task gets real terminal read access
- background tasks do not get interactive keyboard input

This is the current bridge between shell-style foreground control and a more Linux-like future TTY model.

## Key Queue

Fast typing exposed a problem with single-key handoff:

- one key arrived
- the task resumed
- the outer loop returned
- another key arrived during redraw

That could cause visible glitches or dropped responsiveness in `nano`.

PebbleOS now uses a per-task key queue for foreground TTY tasks.

Behavior:

- when a task is `blocked-tty`, the foreground loop reads the first available key
- it then drains any already-available extra keys without waiting
- all collected keys are stored in the task queue
- the task consumes them one by one on later `read_key()` calls

This reduces host/VM round-trips and makes high-speed typing more stable.

## Why `nano` Needed This

`nano` is a full-screen terminal program:

- it redraws the screen
- it moves the cursor
- it consumes individual keys

If terminal mode flips between raw and cooked for every single keystroke, fast input can leak into normal terminal echo behavior and corrupt the visual experience.

The current model improves this by:

- holding raw mode while a foreground task is waiting on TTY keys
- returning to cooked mode only when line input is needed or the task exits/detaches
- using a per-task key queue instead of a single-key handoff

## Current Scope

What is implemented now:

- scheduler-visible `blocked-input`
- scheduler-visible `blocked-tty`
- foreground task ownership of terminal input
- raw/cooked switching based on request type
- per-task key queue for fast foreground key input

What is not fully finished yet:

- a complete TTY device/fd abstraction
- full process-group TTY permission enforcement
- background-task TTY policy beyond the current practical restrictions
- richer `ps` / `top` display of blocked input states everywhere

## Related Files

- host terminal and scheduler bridge: [`pebble_bootloader/shell.py`](pebble_bootloader/shell.py)
- shell/login behavior: [`docs/SHELL_LOGIN.md`](docs/SHELL_LOGIN.md)
- launcher behavior: [`docs/LAUNCHER.md`](docs/LAUNCHER.md)
- runtime key APIs: [`pebble_system/runtime.peb`](pebble_system/runtime.peb)
- Pebble editor: [`pebble_system/nano.peb`](pebble_system/nano.peb)

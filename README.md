# Pebble OS

This project is a tiny operating system simulator with:

- a flat filesystem where every file lives in one folder
- selectable filesystem backends for fast host storage or Pebble-managed virtual storage
- a shell with Linux-style commands for the flat Pebble OS filesystem
- a tiny language called `Pebble`

## Pebble language

`Pebble` is line-based and uses Python-style blocks with exactly four spaces
per indentation level.

Statements:

- `name = expression`
- `items[index] = expression`
- `print expression`
- `import math`
- `pass`
- `break`
- `continue`
- `if expression:`
- `elif expression:`
- `else:`
- `while expression:`
- `for name in range(...):`
- `def name(arg1, arg2):`
- `return expression`

Expressions support:

- integer literals
- float literals
- `True`, `False`, `None`
- string literals
- list literals
- dict literals
- variables
- function calls
- indexing
- parentheses
- `+`
- `-`
- `*`
- `/`
- `<`
- `>`
- `==`
- `!=`
- `<=`
- `>=`
- `and`
- `or`
- `not`

Example:

```text
data = []
append(data, "peb")
append(data, "ble")
name = data[0] + data[1]

i = 0
while i < len(data):
    print data[i]
    i = i + 1

write_file("hello.txt", name)
print read_file("hello.txt")
```

Rules:

- blocks must be indented by exactly four spaces
- comparisons return `1` for true and `0` for false
- `True`, `False`, and `None` follow Python-style truthiness
- `dict` values support indexing and assignment with `data[key]`
- `if`, `elif`, and `else` use integer truthiness: `0` is false, nonzero is true
- `while` uses Python-style block syntax
- `for` supports iterating over `range(...)`, list values, strings, and dict keys
- file I/O stays inside the flat filesystem folder
- builtins: `len`, `append`, `range`, `read_file`, `write_file`, `str`, `int`, `float`, `input`, `argv`, `keys`

## Run the shell

```bash
python3 main.py
```

You can choose the filesystem backend at boot:

```bash
python3 main.py --fs-mode hostfs
python3 main.py --fs-mode vfs-import
python3 main.py --fs-mode vfs-persistent
```

The default is `hostfs`.

Pebble OS also mounts the host system-runtime directory
[`pebble_system/`](/Users/xulixin/LX_OS/pebble_system) as `system/...` for
bootstrapping Pebble-managed runtime files.

At startup, Python now acts as the hidden bootloader layer in
[`pebble_bootloader/`](/Users/xulixin/LX_OS/pebble_bootloader): it loads [`system/runtime.peb`](/Users/xulixin/LX_OS/pebble_system/runtime.peb),
injects [`system/shell.peb`](/Users/xulixin/LX_OS/pebble_system/shell.peb) source as data,
calls `boot()`, and only then enters the interactive Pebble OS shell.
The bootloader now delegates the shell prompt, intro text, help text, and
all built-in command behavior to [`system/shell.peb`](/Users/xulixin/LX_OS/pebble_system/shell.peb).
The shared Pebble runtime lives in [`system/runtime.peb`](/Users/xulixin/LX_OS/pebble_system/runtime.peb),
and the default `nano` command now launches the runtime-managed editor in
[`system/nano.peb`](/Users/xulixin/LX_OS/pebble_system/nano.peb).

## Filesystem Modes

Pebble OS now has a unified Pebble-side filesystem API with multiple backends.
Shell commands call the same Pebble functions for file operations, but those
functions can target different storage modes.

### `hostfs`

`hostfs` is the fast and simple mode.

- User files live directly as normal host files under [`pebble_disk/`](/Users/xulixin/LX_OS/pebble_disk)
- Python can see and edit those files directly
- `run` and `nano` operate on the real host files without any conversion step
- New Pebble-native filesystem features may need separate compatibility work if
  you want them to behave exactly the same here

Use this mode when you want speed, simple debugging, and direct visibility from
Python or the host OS.

### `vfs-import`

`vfs-import` creates a Pebble-managed virtual filesystem from the current host
files at boot.

- At startup, Pebble reads the current host user files and imports them into a
  virtual disk image
- The session then runs against the Pebble virtual filesystem
- The host files are the source for boot import, not a continuously synced mirror

Use this mode when you want to experiment with Pebble-native filesystem rules
without committing to a long-lived virtual disk state.

### `vfs-persistent`

`vfs-persistent` keeps using the Pebble virtual disk image across boots.

- User files live in a backing store file inside [`pebble_disk/`](/Users/xulixin/LX_OS/pebble_disk)
- The backing store persists across sessions
- Host user files are not the source of truth after the VFS has been created

Use this mode when you want Pebble itself to own filesystem behavior and state.

### Why two backends

This split is intentional:

- `hostfs` is the stable and fast mode
- `vfs-*` modes are the Pebble-native evolution path

That means Pebble OS can keep a practical host-backed mode while still growing a
filesystem that Pebble can redefine for itself.

### Synchronization model

The modes are not continuously synchronized.

- `hostfs` reads and writes host files directly
- `vfs-import` imports host files at boot, then runs inside the VFS for that session
- `vfs-persistent` loads the saved VFS image and does not re-import host files unless you switch modes

This avoids hidden merge rules, metadata conflicts, and accidental loss of
Pebble-native filesystem features that do not map cleanly onto host files.

### Shadow files

In VFS modes, Pebble user files are not real host files. Today `run` and `nano`
still depend on host-visible paths, so Pebble OS uses temporary shadow files as
a bridge:

- copy a Pebble VFS file into a temporary host file
- run the existing program/editor flow on that host file
- copy the result back into the VFS if needed
- delete the temporary file

These shadow files are implementation details. They are not the source of truth
for user files.

To support that runtime-managed shell, Pebble also exposes a small host bridge
to `system/shell.peb`:

- `raw_list_files()`
- `raw_file_exists(name)`
- `raw_create_file(name, text)`
- `raw_modify_file(name, text)`
- `raw_delete_file(name)`
- `raw_read_file(name)`
- `raw_write_file(name, text)`
- `raw_file_time(name)`
- `capture_text()`
- `run_program(name, argv)`
- `term_write(text)`
- `term_flush()`
- `term_clear()`
- `term_move(row, col)`
- `term_hide_cursor()`
- `term_show_cursor()`
- `term_read_key()`
- `term_read_key_timeout(ms)`
- `term_rows()`
- `term_cols()`
- `runtime_error(message)`

Pebble then defines the public filesystem behavior inside
[`system/runtime.peb`](/Users/xulixin/LX_OS/pebble_system/runtime.peb) by routing
those raw host functions through the selected backend.

Useful commands:

- `ls`
- `time`
- `run demo.peb`
- `exec demo.peb`
- `run system/atari_pong.peb`
- `touch demo.peb`
- `edit demo.peb`
- `cat demo.peb`
- `nano demo.peb`
- `run demo.peb`
- `rm demo.peb`
- `lang`
- `exit`

`run` now accepts extra arguments after the program name. For example:

- `run nano.peb note.txt`
- `run demo.peb "two words"`

Pebble now has two execution modes for programs:

- `run FILE [ARGS...]` executes Pebble source through the runtime interpreter
- `exec FILE [ARGS...]` compiles Pebble source to bytecode and runs it through the bytecode VM

Pebble now supports floating-point literals and mixed integer/float arithmetic
for `+`, `-`, `*`, `/`, comparisons, `str()`, `int()`, and `float()`.

The shared Pebble runtime also provides Pebble-implemented math helpers:

- `abs(x)`
- `pow(x, n)` for non-negative integer exponents
- `sqrt(x)` as integer floor square root
- `sin(deg)`, `cos(deg)`, `tan(deg)` using degree input

Trigonometric functions return fixed-point integers scaled by `10000`, so:

- `sin(30)` returns `5000`
- `cos(60)` returns `5000`
- `tan(45)` returns `10000`

Pebble now also supports a small built-in module system:

```text
import math
print math.sin(30)
print math.abs(-7)
```

Current built-in modules:

- `math`: `abs`, `pow`, `sqrt`, `sin`, `cos`, `tan`
- `text`: `len`, `repeat`, `lines`, `join`, `first_line`
- `os`: `list`, `exists`, `read`, `write`, `delete`, `time`
- `random`: `seed`, `next`, `range`

Pebble can also import user modules from Pebble files in the active filesystem:

```text
import mymodule
print mymodule.VALUE
print mymodule.twice(7)
```

This loads `mymodule.peb` and exposes its globals and functions through the
module object.

Pebble programs receive:

- `ARGC` as the argument count
- `ARGV` as a list of argument strings
- `argv(i)` as a convenience builtin for fetching one argument

When creating or editing a file, finish input with a single `.` on its own line.

## Run tests

```bash
python3 -m unittest discover -s tests
```

## Project history

### Phase 1: Toy OS origin

Pebble OS started as a toy operating system experiment with one simple rule:
the filesystem should feel like a single folder.

The first version focused on the minimum OS loop:

- create files
- modify files
- delete files
- list files
- run a tiny language from files stored in the flat disk

At that stage, the project was mainly a Python shell around a tiny disk folder.

### Phase 2: Pebble language appears

The next major step was creating the Pebble language itself. It started as an
extremely small language with only:

- variables
- arithmetic with `+`, `-`, `*`
- `print`

That first version was enough to prove that files in the OS could contain
programs, but it was still far from being able to evolve the system.

### Phase 3: Pebble grows into a usable language

Pebble then moved closer to Python-style syntax and a more practical scripting
model by adding:

- four-space indentation
- `if`, `elif`, `else`
- `for`
- `while`
- functions and `return`
- comparisons
- strings
- lists
- dicts
- indexing and assignment
- comments
- `break`, `continue`, `pass`

These changes turned Pebble from a calculator-like toy language into a small
general-purpose scripting language that could read files, transform text, and
manage structured state.

### Phase 4: Shift toward self-hosting

Once the language became expressive enough, the project started moving from
"toy OS" toward a self-hosting direction. That shift included:

- renaming the system to `Pebble OS`
- separating the visible OS from the hidden Python host layer
- renaming the Python package to `pebble_bootloader`
- mounting `system/...` as the Pebble-managed runtime area
- moving shell behavior into Pebble files instead of hardcoding it in Python

### Phase 5: Python becomes the hidden bootloader

The Python layer is now intentionally treated as a hidden bootloader. It still
provides the capabilities Pebble does not yet implement for itself:

- interpreting Pebble code
- low-level filesystem access
- terminal I/O and raw key input
- host bridge builtins

### Phase 6: Visible system behavior moves into Pebble

At the same time, more and more visible system behavior has moved upward into
Pebble itself:

- `system/runtime.peb` is the shared runtime and standard-library layer
- `system/shell.peb` owns shell command behavior, help, intro, and prompt
- `system/nano.peb` is the runtime-managed editor
- `system/atari_pong.peb` adds a Pebble-written Atari-style terminal game to the system tools
- Pebble programs can now run in two modes: direct interpreter execution with `run`, and bytecode execution with `exec`
- Pebble language now supports floating-point numbers and numeric division in both interpreter and bytecode modes
- Pebble now supports both built-in modules and file-based user modules through a common import mechanism
- Pebble runtime math now includes pure-Pebble `abs`, `pow`, `sqrt`, `sin`, `cos`, and `tan` helpers without a Python math bridge
- the shell can read host local time through a small bridge and expose it as a runtime-managed `time` command
- Pebble terminal programs can poll input with a timeout, which gives Pebble a basic real-time game loop capability
- the filesystem exposes per-file timestamps so `ls` can show a time for each file
- Pebble OS supports both direct host files `hostfs` and Pebble-managed virtual filesystems `vfs` through a unified Pebble filesystem layer

### Current meaning of the architecture

Pebble OS has already crossed an important bootstrap boundary: ordinary shell
behavior and more of the visible system now live in Pebble rather than in
Python.

Python is still the host, but Pebble now controls much more of the system's
own behavior and can increasingly be used to evolve the system itself.

## Roadmap

The long-term direction is to keep shrinking the Python bootloader and keep
moving system behavior into Pebble.

### Completed

- Flat Pebble OS filesystem with explicit mounts
- Pebble language with Python-style indentation and basic control flow
- File I/O, strings, lists, and dicts
- Runtime-managed shell command layer in `system/shell.peb`
- Shared runtime helpers in `system/runtime.peb`
- Runtime-managed full-screen editor in `system/nano.peb`
- Hidden bootloader architecture in `pebble_bootloader/`
- Skill support for adding new Pebble OS system commands

### Near-term

- Move more user tools from `pebble_disk/` into `system/*.peb`
- Add more Linux-style shell commands such as `cp`, `echo`, and `pwd`
- Clean up the shell surface so only the preferred command set remains
- Expand `system/runtime.peb` into a more stable standard library

### Mid-term

- Build Pebble-written tokenizer, inspector, and rewriter tools under `system/`
- Define a stable "core Pebble" subset for bootstrapping work
- Use Pebble tools to transform and generate more Pebble runtime code
- Reduce how often runtime improvements require bootloader changes

### Long-term

- Make Pebble the main place where Pebble OS evolves
- Keep Python as a very small host VM and hardware/terminal bridge
- Build a stronger self-hosting toolchain in Pebble
- Move from "Pebble programs running inside the OS" toward "Pebble managing the OS"

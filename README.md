# Pebble OS

This project is a tiny operating system simulator with:

- a flat filesystem where every file lives in one folder
- a shell with Linux-style commands for the flat Pebble OS filesystem
- a tiny language called `Pebble`

## Pebble language

`Pebble` is line-based and uses Python-style blocks with exactly four spaces
per indentation level.

Statements:

- `name = expression`
- `items[index] = expression`
- `print expression`
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
- builtins: `len`, `append`, `range`, `read_file`, `write_file`, `str`, `int`, `input`, `argv`, `keys`

## Run the shell

```bash
python3 main.py
```

The shell stores everything in [`pebble_disk/`](/Users/xulixin/LX_OS/pebble_disk).

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

To support that runtime-managed shell, Pebble also exposes a small host bridge
to `system/shell.peb`:

- `list_files()`
- `file_exists(name)`
- `create_file(name, text)`
- `modify_file(name, text)`
- `delete_file(name)`
- `capture_text()`
- `run_program(name, argv)`
- `term_write(text)`
- `term_flush()`
- `term_clear()`
- `term_move(row, col)`
- `term_hide_cursor()`
- `term_show_cursor()`
- `term_read_key()`
- `term_rows()`
- `term_cols()`

Useful commands:

- `ls`
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

Pebble OS started as a toy operating system experiment with one simple rule:
the filesystem should feel like a single folder. The first version focused on
the minimum OS loop:

- create files
- modify files
- delete files
- list files
- run a tiny language from files stored in the flat disk

At that stage, the project was mainly a Python shell around a tiny disk folder.

The next step was creating the Pebble language itself. It started as an
extremely small language with only:

- variables
- arithmetic with `+`, `-`, `*`
- `print`

That first language was enough to prove that files in the OS could contain
programs, but it was far from being able to improve itself.

From there, Pebble gradually moved closer to Python-style syntax and a more
usable programming model:

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

Those changes turned Pebble from a calculator-like toy language into a small
general-purpose scripting language that could read files, transform text, and
manage structured state.

After the language became expressive enough, the project shifted from "toy OS"
toward "self-hosting direction". That change had a few major parts:

- renaming the system to `Pebble OS`
- separating the visible OS from the hidden Python host layer
- renaming the Python package to `pebble_bootloader`
- mounting `system/...` as the Pebble-managed runtime area
- moving shell behavior into Pebble files instead of hardcoding it in Python

The Python layer is now intentionally treated as a hidden bootloader. It still
provides the things Pebble cannot yet provide for itself:

- interpreting Pebble code
- low-level filesystem access
- terminal I/O and raw key input
- host bridge builtins

But the visible system logic has been pushed upward into Pebble:

- `system/runtime.peb` is the shared runtime and standard-library layer
- `system/shell.peb` owns shell command behavior, help, intro, and prompt
- `system/nano.peb` is the runtime-managed editor

This means the system has already crossed an important bootstrap boundary:
Pebble OS no longer depends on Python for ordinary shell command behavior.
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

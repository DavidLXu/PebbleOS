# Pebble OS

This project is a tiny operating system simulator with:

- a rooted filesystem with directories and mounted subtrees
- selectable filesystem backends for fast host storage or Pebble-managed virtual storage
- a shell with Linux-style commands for the Pebble OS filesystem
- a tiny language called `Pebble`

Detailed architecture notes:

- filesystem: [FILESYSTEM.md](/Users/xulixin/LX_OS/FILESYSTEM.md)
- memory/runtime: [MEMORY.md](/Users/xulixin/LX_OS/MEMORY.md)
- language: [LANG.md](/Users/xulixin/LX_OS/LANG.md)
- ABI: [docs/ABI.md](/Users/xulixin/LX_OS/docs/ABI.md)
- process model: [docs/PROCESS.md](/Users/xulixin/LX_OS/docs/PROCESS.md)
- launcher semantics: [docs/LAUNCHER.md](/Users/xulixin/LX_OS/docs/LAUNCHER.md)
- shell/login semantics: [docs/SHELL_LOGIN.md](/Users/xulixin/LX_OS/docs/SHELL_LOGIN.md)

## Pebble language

Pebble is the system language for Pebble OS. It supports Python-style
four-space indentation, interpreter mode with `run`, bytecode mode with `exec`,
built-in modules like `math`, `memory`, and `heap`, and file-based user
modules. `run` and `exec` still exist, but they are now compatibility launchers
alongside the preferred direct-command model described in
[docs/LAUNCHER.md](/Users/xulixin/LX_OS/docs/LAUNCHER.md).

For the full syntax, builtins, modules, examples, and execution model, see
[LANG.md](/Users/xulixin/LX_OS/LANG.md).

## Run the shell

```bash
python3 main.py
```

You can choose the filesystem backend at boot:

```bash
python3 main.py --fs-mode hostfs
python3 main.py --fs-mode mfs
python3 main.py --fs-mode mfs-import
python3 main.py --fs-mode vfs-import
python3 main.py --fs-mode vfs-persistent
```

The default is `hostfs`.

Filesystem modes:

- `hostfs`: use the host-backed rooted filesystem directly
- `mfs`: start with an empty Pebble memory filesystem for the current session
- `mfs-import`: import host files into the Pebble memory filesystem, then keep running in memory
- `vfs-import`: import host files into a Pebble VFS at boot, then run from the VFS
- `vfs-persistent`: keep Pebble's virtual filesystem as the persistent source of truth

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

Pebble OS supports multiple filesystem backends behind one Pebble runtime API.

- `hostfs`: direct host-backed rooted filesystem with directories
- `mfs`: empty in-memory Pebble filesystem for one session, with optional `sync`
- `mfs-import`: import host files once into the in-memory Pebble filesystem, then keep running in memory
- `vfs-import`: import host files into a Pebble VFS at boot, then run from the VFS
- `vfs-persistent`: keep Pebble's virtual filesystem as the persistent source of truth

For the full design, synchronization model, shadow-file bridge, and raw host
bridge details, see [FILESYSTEM.md](/Users/xulixin/LX_OS/FILESYSTEM.md).

Useful commands:

- `ls`
- `cd demos`
- `pwd`
- `mkdir docs`
- `rmdir docs`
- `time`
- `run demo.peb`
- `exec demo.peb`
- `demo`
- `demo &`
- `sh script.sh`
- `bash script.sh`
- `bash -c "echo hello"`
- `run system/clock_tick.peb`
- `run system/count_tick.peb`
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

Pebble now has two explicit execution modes for programs:

- `run FILE [ARGS...]` executes Pebble source through the runtime interpreter
- `exec FILE [ARGS...]` compiles Pebble source to bytecode and runs it through the bytecode VM

These are compatibility entry points. The preferred shell model is:

- `COMMAND [ARGS...]`
- `COMMAND &`

PebbleOS also provides a standard compatibility shell command:

- `sh FILE`
- `/bin/sh FILE`

Pebble language details, math/module support, user imports, and builtins are
documented in [LANG.md](/Users/xulixin/LX_OS/LANG.md). Pebble memory layers are
documented in [MEMORY.md](/Users/xulixin/LX_OS/MEMORY.md).

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

- a Pebble-managed kernel ABI split with process metadata and syscall-facing modules
- shell redirection and multi-stage pipelines for command composition
- a `/system/bin/*.peb` external command model, with most file and text commands moved out of shell builtins
- fd-backed stdio routing and pipe-backed shell execution so files, redirection, and pipelines share one host-side I/O model
- PATH-based external command lookup, `/bin` compatibility mapping, and first Linux-like userland commands such as `env`, `which`, and `find`
- Phase 4 userland expansion with `grep`, filtered `find`, multi-arg `which`, richer `env`, and a minimal `kill` path wired into the process model
- shell/session features closer to a real login shell: `bg`, `source`, automatic `/etc/profile` loading, and default `/etc/passwd`, `/etc/group`, `/etc/fstab` placeholders
- unified launcher behavior for Pebble programs: direct `.peb` execution by command name and `&` background launch syntax, while `run/exec/runbg/execbg` remain compatibility entry points
- a Pebble-native `top` command for live process snapshots on top of the shared process table
- a standard `sh` compatibility command at `/system/bin/sh.peb`, `/bin/sh` path mapping, and formal launcher/login documentation in `docs/LAUNCHER.md` and `docs/SHELL_LOGIN.md`
- a Pebble-native `bash.peb` front-end that can run scripts, `bash -c COMMAND`, and a simple interactive REPL without adding a Python-only command path
- interactive `bash` now runs on the attached foreground terminal path, so `bash` REPL uses Pebble `input()` directly instead of the scheduler task fallback

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
- `system/clock_tick.peb` and `system/count_tick.peb` add Pebble-written ticking demo apps that update once per second
- `system/atari_pong.peb` adds a Pebble-written Atari-style terminal game to the system tools
- Pebble OS now uses a rooted path model with directories, `cd`, `pwd`, `mkdir`, and `rmdir`, while still mounting `system/...` as the runtime subtree
- Pebble programs can now run in two modes: direct interpreter execution with `run`, and bytecode execution with `exec`
- Pebble language now supports floating-point numbers and numeric division in both interpreter and bytecode modes
- Pebble now supports both built-in modules and file-based user modules through a common import mechanism
- Pebble runtime math now includes pure-Pebble `abs`, `pow`, `sqrt`, `sin`, `cos`, and `tan` helpers without a Python math bridge
- Pebble runtime now includes a Pebble-native virtual RAM layer through `import memory`, giving programs explicit alloc/read/write behavior without changing the Python host memory model
- Pebble runtime now adds block memory operations, a Pebble-native `heap` allocator, and a more explicit bytecode VM frame/value stack model to move execution semantics further away from raw Python object management
- the shell can read host local time through a small bridge and expose it as a runtime-managed `time` command
- shell and filesystem timestamps now include seconds, and `run`/`exec` stream program output as it is produced instead of buffering until process exit
- foreground programs now support terminal control shortcuts at the system level: `Ctrl-C` interrupts and returns to the shell, while `Ctrl-Z` detaches the job into the background without leaving the terminal in raw mode
- Pebble terminal programs can poll input with a timeout, which gives Pebble a basic real-time game loop capability
- the filesystem exposes per-file timestamps so `ls` can show a time for each file
- Pebble OS supports both direct host files `hostfs` and Pebble-managed virtual filesystems `vfs` through a unified Pebble filesystem layer
- Pebble language imports now support nested module paths like `system.kernel.proc`, and Pebble OS now has the first kernel-module split for ABI inventory, errno definitions, and process transition wrappers

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

- Rooted Pebble OS filesystem with directories and explicit `system/...` mount behavior
- Pebble language with Python-style indentation and basic control flow
- File I/O, strings, lists, and dicts
- Runtime-managed shell command layer in `system/shell.peb`
- Shared runtime helpers in `system/runtime.peb`
- Runtime-managed full-screen editor in `system/nano.peb`
- Hidden bootloader architecture in `pebble_bootloader/`
- Skill support for adding new Pebble OS system commands

### Near-term

- Move more user tools from `pebble_disk/` into `system/*.peb`
- Add more Linux-style shell commands such as `echo` and `find`
- Clean up the shell surface so only the preferred command set remains
- Expand `system/runtime.peb` into a more stable standard library

### Mid-term

- Build Pebble-written tokenizer, inspector, and rewriter tools under `system/`
- Define a stable "core Pebble" subset for bootstrapping work
- Use Pebble tools to transform and generate more Pebble runtime code
- Reduce how often runtime improvements require bootloader changes

### Long-term

- Make Pebble the main place where Pebble OS evolves

## Toward A Working System

From the current bootstrap point, the shortest credible path to a minimum
working modern system is:

### Stage 1: Observable single-user system

- establish a stable process/task model
- expose process inspection and lifecycle commands
- standardize foreground/background semantics
- keep shell, runtime, and scheduler behavior visible from Pebble

### Stage 2: System service layer

- add standard streams, logging, timers, and event delivery
- define a stable runtime ABI for apps and services
- add long-running Pebble services managed by the system

### Stage 3: Isolation and safety

- split tasks into protected process domains
- define capability or permission boundaries for files, terminal, and services
- prevent one crashing program from corrupting unrelated tasks

### Stage 4: Program model and packaging

- define executable/module metadata
- add a package manager and dependency rules
- support reproducible app loading and versioned runtime interfaces

### Stage 5: Practical OS services

- networking
- configuration management
- service management
- better filesystem metadata and durability
- debugger, profiler, and better system diagnostics

### Stage 6: Modern usability

- robust TTY behavior with pipes, redirection, and richer job control
- interactive apps that can detach and reattach cleanly
- eventually a graphics/windowing layer if Pebble OS grows past terminal-first use

### Current execution step

The current repository has started Stage 1 by adding:

- resumable bytecode VM tasks
- a Pebble runtime scheduler data model
- foreground detach to background with `F1`
- a system-visible `ps` command for process inspection
- Keep Python as a very small host VM and hardware/terminal bridge
- Build a stronger self-hosting toolchain in Pebble
- Move from "Pebble programs running inside the OS" toward "Pebble managing the OS"

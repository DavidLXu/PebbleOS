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
- tty/input semantics: [docs/TTY_INPUT.md](/Users/xulixin/LX_OS/docs/TTY_INPUT.md)
- Pebble vs Python 3.x language gaps: [docs/PEBBLE_LANGUAGE_GAPS.md](/Users/xulixin/LX_OS/docs/PEBBLE_LANGUAGE_GAPS.md)

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

Default discovery now runs the fast suite and skips the slower shell/TTY
integration coverage. To include the full shell runtime regression set, run:

```bash
PEBBLE_RUN_SLOW_TESTS=1 python3 -m unittest discover -s tests
```

## Evolution

### Before the modern-system push

PebbleOS began as a toy OS experiment with a very small goal: store files,
edit them, and run a tiny language from them.

The first phases were:

- a flat single-folder filesystem with create, modify, delete, and list
- the first Pebble language with only variables, arithmetic, and `print`
- a gradual move to Python-like syntax with indentation, functions, loops,
  conditionals, strings, lists, dicts, comments, and modules
- the rename from a toy OS into `Pebble OS`
- the split between the visible Pebble system and the hidden Python host
- the move of shell behavior from Python into Pebble files under `system/...`

That was the bootstrap phase. The important result was not “modern OS”
features yet, but a different architecture: Pebble stopped being just a guest
language and started becoming the language that drives the system itself.

### Preparing for a modern system

Once self-hosting became realistic, the project shifted from “Pebble can grow
Pebble” to “PebbleOS can start acting like a real system.”

That preparation phase added:

- rooted paths, directories, mounts, and multiple filesystem backends
- interpreter and bytecode execution modes
- Pebble-native memory, heap, and a resumable bytecode VM
- a Pebble runtime scheduler model with foreground/background task handling
- `F1` detach, `jobs`, `fg`, and now `ps` for process inspection
- a stronger userland layout with `system/bin`, `system/lib`, and `system/kernel`
- shell features closer to a Unix-like environment: PATH lookup, redirection,
  pipelines, `sh`, `bash`, login/profile loading, and more external commands
- terminal and TTY work so interactive Pebble programs depend less on ad hoc
  Python-only paths
- a minimal `/dev` bootstrap through the fd layer, with `/dev/tty`,
  `/dev/stdin`, `/dev/stdout`, `/dev/stderr`, and `/dev/null` available as
  device-style paths
- the Pebble runtime filesystem view now exposes `/dev` as a visible virtual
  directory, so `cd /dev`, `ls`, and `find /dev` can inspect device nodes even
  before a full `devfs` mount layer exists
- Pebble runtime output helpers now route through `/dev/stdout`, and a
  Pebble-native `tty` command can inspect the current `/dev/tty` state without
  adding a Python-only shell command
- Pebble userland now includes `top` and `htop` process viewers, with `top`
  keeping a simple live task table and `htop` adding a denser interactive
  dashboard over the same Pebble-visible process and thread ABI
- `ls` now shows only the current directory's direct children instead of dumping the full recursive file set, and the Pebble-native `tree` command now uses a host-accelerated renderer in `hostfs` mode with the Pebble implementation kept as a fallback for other filesystem backends
- Pebble userland now includes a `pebble` launcher in `system/bin`, so `pebble demo.peb` forces interpreter mode while direct program launch can continue to use the VM-oriented path separately
- thread design is now documented in `docs/THREADING.md`, and Pebble exposes a first bootstrap thread ABI built on the VM scheduler with `thread_spawn_source`, `thread_join`, `thread_status`, `thread_self`, and `thread_list`
- the bootstrap threading layer now includes Pebble-visible mutex syscalls and runtime wrappers, with blocked lockers surfacing as `blocked-mutex` and waking back into the VM scheduler when ownership is released
- Pebble language priorities are now tracked against Python 3.x in `docs/PEBBLE_LANGUAGE_GAPS.md`, and the first OS-driven upgrade is in place: minimal `try: ... except:` error recovery in both interpreter and bytecode execution
- Pebble error handling has advanced another step toward Python-style system code: `raise expression` now works in both interpreter and bytecode modes, still with a deliberately minimal runtime-error model
- Pebble error recovery now also supports `except err:` bindings, with the bound value exposed as a stringified runtime error so system code can log or branch on failures without a full exception-class hierarchy yet
- Pebble now has bootstrap first-class function values for user-defined functions, and the thread API has started using them via `thread_spawn(func, args)` instead of only `thread_spawn_source(...)`

This is the current transition point: PebbleOS is no longer just a bootstrap
demo, but it is not yet a full modern system either. It now has enough
runtime, shell, process, and terminal structure to start building one on top
of the Pebble-managed layers.

## Toward A Working System

The next stages are:

### Stage 1: Observable single-user system

- stabilize the process/task model
- expose lifecycle commands beyond inspection
- unify `ps`, `jobs`, foreground attach, and background execution

### Stage 2: System service layer

- standard streams, logging, timers, and event delivery
- long-running Pebble-managed services
- a clearer runtime ABI for apps and system modules

### Stage 3: Isolation and safety

- protected process domains
- capability or permission boundaries
- failure isolation between programs

### Stage 4: Program model and packaging

- executable and module metadata
- package management and dependency rules
- reproducible loading with versioned interfaces

### Stage 5: Practical OS services

- networking
- configuration and service management
- stronger filesystem metadata and durability
- debugger, profiler, and better diagnostics

### Stage 6: Modern usability

- richer TTY behavior with solid job control
- interactive apps that detach and reattach cleanly
- eventually a graphical layer if PebbleOS grows beyond terminal-first use

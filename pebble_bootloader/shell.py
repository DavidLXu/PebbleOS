from __future__ import annotations

import cmd
import os
import queue
import select
import shutil
import shlex
import time
import sys
import termios
import threading
import tty
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pebble_bootloader.fs import FileSystemError, FlatFileSystem
from pebble_bootloader.lang import PebbleBytecodeInterpreter, PebbleError, PebbleInterpreter


VALID_FS_MODES = {"hostfs", "mfs", "mfs-import", "vfs-import", "vfs-persistent"}


@dataclass
class BackgroundJob:
    job_id: int
    command: str
    program: str
    argv: list[str]
    exec_mode: str
    cwd: str
    outputs: list[str] = field(default_factory=list)
    consumed_outputs: int = 0
    status: str = "running"
    error: str | None = None
    thread: threading.Thread | None = None
    ppid: int = 1
    pgid: int = 0
    sid: int = 1


@dataclass
class VMTask:
    task_id: int
    command: str
    program: str
    argv: list[str]
    cwd: str
    interpreter: PebbleBytecodeInterpreter
    outputs_consumed: int = 0
    status: str = "ready"
    error: str | None = None
    attached: bool = False
    ppid: int = 1
    pgid: int = 0
    sid: int = 1


@dataclass
class HostProcessRecord:
    pid: int
    kind: str
    state: str
    command: str
    program: str
    argv: list[str]
    cwd: str
    ppid: int = 1
    pgid: int = 0
    sid: int = 1
    attached: bool = False
    exit_status: int = 0
    error: str | None = None


class PebbleShell(cmd.Cmd):
    intro = (
        "Pebble OS shell\n"
        "Filesystem: rooted paths with directories\n"
        "Type 'help' for commands."
    )
    prompt = "pebble-os> "

    def __init__(self, root: Path, fs_mode: str = "hostfs") -> None:
        super().__init__()
        if fs_mode not in VALID_FS_MODES:
            raise ValueError(f"invalid fs mode '{fs_mode}'")
        self.fs = FlatFileSystem(root)
        self.fs_mode = fs_mode
        self.cwd = "/"
        self.mfs_blob: str | None = None
        self._jobs: dict[int, BackgroundJob] = {}
        self._jobs_lock = threading.Lock()
        self._next_job_id = 1
        self._vm_tasks: dict[int, VMTask] = {}
        self._vm_lock = threading.Lock()
        self._vm_snapshots: dict[int, dict[str, object]] = {}
        self._next_vm_snapshot_id = 1
        self._pending_signal_events: list[dict[str, object]] = []
        self._signal_notified_pids: set[int] = set()
        self._foreground_pgid: int | None = None
        self._detach_requested = threading.Event()
        self._main_thread_id = threading.get_ident()
        self._terminal_owner_thread_id: int | None = None
        self._shell_terminal_settings = self._capture_terminal_settings()
        self.fs.mount("system", Path(__file__).resolve().parent.parent / "pebble_system")
        self._refresh_shell_state()
        self._vm_scheduler = threading.Thread(target=self._vm_scheduler_loop, name="pebble-vm-scheduler", daemon=True)
        self._vm_scheduler.start()

    def do_help(self, arg: str) -> None:
        self._dispatch_runtime_command("help", arg)

    def do_exit(self, arg: str) -> bool:
        return self._dispatch_runtime_command("exit", arg)

    def do_EOF(self, arg: str) -> bool:
        print()
        return self.do_exit(arg)

    def cmdloop(self, intro: str | None = None) -> None:
        if intro is not None:
            self.intro = intro
        self.preloop()
        old_completer = None
        readline_ready = False
        if self.use_rawinput and getattr(self, "completekey", None):
            try:
                import readline

                old_completer = readline.get_completer()
                readline.set_completer(self.complete)
                readline.parse_and_bind(self.completekey + ": complete")
                readline_ready = True
            except ImportError:
                readline_ready = False
        try:
            if self.intro:
                for line in str(self.intro).splitlines():
                    self._emit_runtime_output(line)
            stop = False
            while not stop:
                try:
                    line = input("\r" + self.prompt)
                except EOFError:
                    line = "EOF"
                except KeyboardInterrupt:
                    self._restore_shell_terminal()
                    self._reset_to_prompt_line()
                    print("^C", flush=True)
                    continue
                line = self.precmd(line)
                stop = bool(self.onecmd(line))
                stop = self.postcmd(stop, line)
            self.postloop()
        finally:
            if readline_ready:
                try:
                    import readline

                    readline.set_completer(old_completer)
                except ImportError:
                    pass
            self._terminal_owner_thread_id = None
            self._restore_shell_terminal()

    def completenames(self, text: str, *ignored) -> list[str]:
        commands = [
            "help",
            "ls",
            "cd",
            "pwd",
            "mkdir",
            "rmdir",
            "time",
            "sync",
            "touch",
            "edit",
            "cat",
            "rm",
            "cp",
            "mv",
            "run",
            "runbg",
            "exec",
            "execbg",
            "ps",
            "jobs",
            "fg",
            "nano",
            "lang",
            "exit",
        ]
        return [name for name in commands if name.startswith(text)]

    def completedefault(self, text: str, line: str, begidx: int, endidx: int) -> list[str]:
        command, arg_index = self._completion_context(line, begidx)
        if command in {"cd", "mkdir", "rmdir"}:
            return self._complete_paths(text, directories_only=True, fuzzy=(command == "cd"))
        if command == "ls":
            return self._complete_paths(text, directories_only=False, fuzzy=True)
        if command in {"touch", "edit", "cat", "rm", "run", "runbg", "exec", "execbg", "nano"}:
            return self._complete_paths(
                text,
                directories_only=False,
                fuzzy=(command in {"run", "runbg", "exec", "execbg"}),
                recursive_fuzzy=(command in {"run", "runbg", "exec", "execbg"}),
            )
        if command in {"cp", "mv"}:
            return self._complete_paths(text, directories_only=(arg_index > 0), fuzzy=False)
        if command == "fg":
            return self._complete_job_ids(text)
        if command == "help":
            return self.completenames(text)
        return []

    def postcmd(self, stop: bool, line: str) -> bool:
        self._refresh_shell_state()
        return super().postcmd(stop, line)

    def emptyline(self) -> None:
        pass

    def default(self, line: str) -> bool | None:
        text = line.strip()
        if not text:
            return None
        parts = text.split(None, 1)
        command = parts[0]
        arg = ""
        if len(parts) > 1:
            arg = parts[1]
        if self._dispatch_runtime_command(command, arg):
            return True
        return None

    def _completion_context(self, line: str, begidx: int) -> tuple[str, int]:
        prefix = line[:begidx]
        try:
            parts = shlex.split(prefix)
        except ValueError:
            parts = prefix.split()
        if not parts:
            return "", 0
        if prefix.endswith(" "):
            return parts[0], len(parts) - 1
        return parts[0], max(0, len(parts) - 2)

    def _complete_paths(
        self,
        text: str,
        directories_only: bool,
        fuzzy: bool = False,
        recursive_fuzzy: bool = False,
    ) -> list[str]:
        logical_prefix = text or ""
        absolute = logical_prefix.startswith("/")
        if "/" in logical_prefix:
            dir_text, partial = logical_prefix.rsplit("/", 1)
            search_dir = "/" + dir_text.strip("/") if absolute else dir_text
        else:
            dir_text = ""
            partial = logical_prefix
            search_dir = "/" if absolute else "."
        try:
            directory_logical = self._normalize_user_path(search_dir)
            directory_host = self._logical_path_to_host(directory_logical)
        except FileSystemError:
            return []
        if not directory_host.exists() or not directory_host.is_dir():
            return []

        prefix_matches: list[str] = []
        fuzzy_matches: list[str] = []
        partial_folded = partial.lower()
        for child in sorted(directory_host.iterdir(), key=lambda item: item.name):
            if directories_only and not child.is_dir():
                continue
            if dir_text:
                suggestion = dir_text.rstrip("/") + "/" + child.name
            elif absolute:
                suggestion = "/" + child.name
            else:
                suggestion = child.name
            if child.is_dir():
                suggestion = suggestion + "/"
            if child.name.startswith(partial):
                prefix_matches.append(suggestion)
                continue
            if fuzzy and len(partial_folded) > 0 and partial_folded in child.name.lower():
                fuzzy_matches.append(suggestion)
        matches = prefix_matches + fuzzy_matches
        if matches or not recursive_fuzzy or len(partial_folded) == 0 or "/" in logical_prefix:
            return matches

        recursive_matches: list[str] = []
        for suggestion in self._visible_path_suggestions(directories_only):
            folded = suggestion.lower().rstrip("/")
            if partial_folded in folded and suggestion not in recursive_matches:
                recursive_matches.append(suggestion)
        return recursive_matches

    def _visible_path_suggestions(self, directories_only: bool) -> list[str]:
        suggestions: list[str] = []
        seen: set[str] = set()
        for name in self.fs.list_files():
            if name.startswith("system/"):
                suggestion = name
            elif self.cwd == "/":
                suggestion = name
            else:
                prefix = self.cwd.strip("/") + "/"
                if not name.startswith(prefix):
                    continue
                suggestion = name[len(prefix) :]
            if suggestion not in seen:
                seen.add(suggestion)
                suggestions.append(suggestion)
            parts = suggestion.split("/")
            current = ""
            for part in parts[:-1]:
                current = part if current == "" else current + "/" + part
                dir_suggestion = current + "/"
                if dir_suggestion not in seen:
                    seen.add(dir_suggestion)
                    suggestions.append(dir_suggestion)
        if directories_only:
            return [item for item in suggestions if item.endswith("/")]
        return suggestions

    def _complete_job_ids(self, text: str) -> list[str]:
        prefix = text or ""
        with self._jobs_lock:
            ids = sorted(self._jobs)
        with self._vm_lock:
            ids.extend(sorted(self._vm_tasks))
        return [str(job_id) for job_id in ids if str(job_id).startswith(prefix)]

    def _refresh_shell_state(self) -> None:
        try:
            prompt = self._system_shell_call("shell_prompt")
            intro = self._system_shell_call("shell_intro")
        except (FileSystemError, PebbleError):
            prompt = None
            intro = None
        if isinstance(prompt, str) and prompt:
            self.prompt = prompt
        if isinstance(intro, str) and intro:
            self.intro = intro

    def _dispatch_runtime_command(self, command: str, arg: str) -> bool:
        try:
            argv = self._split_args(arg)
            result = self._system_shell_call("shell_dispatch", [command, argv], consume_output=True)
            if isinstance(result, str) and result.startswith("__cwd__:"):
                self.cwd = result[8:]
                return False
            return result == "__exit__"
        except KeyboardInterrupt:
            print("^C")
            return False
        except (FileSystemError, PebbleError, ValueError) as exc:
            print(exc)
            return False

    def _system_shell_call(
        self,
        function_name: str,
        args: list[object] | None = None,
        consume_output: bool = False,
    ) -> object:
        runtime_source = self.fs.read_file("system/runtime.peb")
        shell_source = self.fs.read_file("system/shell.peb")
        source = runtime_source + "\n" + shell_source
        runtime = self._make_runtime(consume_output=consume_output)
        initial_globals: dict[str, object] = {
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "SYSTEM_SHELL_PATH": "system/shell.peb",
            "SYSTEM_SHELL_SOURCE": shell_source,
            "FS_MODE": self.fs_mode,
            "CWD": self.cwd,
        }
        call_parts: list[str] = []
        if args:
            for index, value in enumerate(args):
                name = f"__arg_{index}"
                initial_globals[name] = value
                call_parts.append(name)
        call_expr = f"{function_name}(" + ", ".join(call_parts) + ")"
        runtime.execute(source + f"\n__result = {call_expr}\n", initial_globals=initial_globals)
        return runtime.globals.get("__result")

    def _make_runtime(self, consume_output: bool) -> PebbleInterpreter:
        consumer = None
        if consume_output:
            consumer = self._emit_runtime_output
        return PebbleInterpreter(
            self.fs.root,
            input_provider=input,
            output_consumer=consumer,
            path_resolver=self._resolve_user_path_to_host,
            host_functions={
                "raw_list_files": self._host_list_files,
                "raw_file_exists": self._host_raw_file_exists,
                "raw_create_file": self._host_raw_create_file,
                "raw_modify_file": self._host_raw_modify_file,
                "raw_delete_file": self._host_raw_delete_file,
                "raw_file_time": self._host_raw_file_time,
                "raw_read_file": self._host_raw_read_file,
                "raw_write_file": self._host_raw_write_file,
                "raw_directory_exists": self._host_raw_directory_exists,
                "raw_make_directory": self._host_raw_make_directory,
                "raw_remove_directory": self._host_raw_remove_directory,
                "raw_directory_empty": self._host_raw_directory_empty,
                "list_files": self._host_list_files,
                "file_time": self._host_file_time,
                "file_exists": self._host_file_exists,
                "directory_exists": self._host_directory_exists,
                "create_file": self._host_create_file,
                "modify_file": self._host_modify_file,
                "delete_file": self._host_delete_file,
                "make_directory": self._host_make_directory,
                "remove_directory": self._host_remove_directory,
                "directory_empty": self._host_directory_empty,
                "capture_text": self._host_capture_text,
                "run_program": self._host_run_program,
                "exec_program": self._host_exec_program,
                "start_background_job": self._host_start_background_job,
                "list_background_jobs": self._host_list_background_jobs,
                "list_processes": self._host_list_processes,
                "list_process_records": self._host_list_process_records,
                "list_child_processes": self._host_list_child_processes,
                "current_foreground_pgid": self._host_current_foreground_pgid,
                "list_signal_events": self._host_list_signal_events,
                "drain_signal_events": self._host_drain_signal_events,
                "foreground_job": self._host_foreground_job,
                "wait_process": self._host_wait_process,
                "wait_child_process": self._host_wait_child_process,
                "reap_process": self._host_reap_process,
                "vm_create_task": self._host_vm_create_task,
                "vm_step_task": self._host_vm_step_task,
                "vm_task_status": self._host_vm_task_status,
                "vm_take_task_output": self._host_vm_take_task_output,
                "vm_snapshot_task": self._host_vm_snapshot_task,
                "vm_restore_task": self._host_vm_restore_task,
                "vm_drop_task": self._host_vm_drop_task,
                "cwd": self._host_cwd,
                "chdir": self._host_chdir,
                "filesystem_file_count": self._host_filesystem_file_count,
                "filesystem_total_bytes": self._host_filesystem_total_bytes,
                "filesystem_sync": self._host_filesystem_sync,
                "term_write": self._host_term_write,
                "term_flush": self._host_term_flush,
                "term_clear": self._host_term_clear,
                "term_move": self._host_term_move,
                "term_hide_cursor": self._host_term_hide_cursor,
                "term_show_cursor": self._host_term_show_cursor,
                "term_read_key": self._host_term_read_key,
                "term_read_key_timeout": self._host_term_read_key_timeout,
                "term_rows": self._host_term_rows,
                "term_cols": self._host_term_cols,
                "current_time": self._host_current_time,
                "runtime_error": self._host_runtime_error,
            },
        )

    def _split_args(self, arg: str) -> list[str]:
        if not arg.strip():
            return []
        try:
            return shlex.split(arg)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    def _host_list_files(self, args: list[object], line_number: int) -> list[str]:
        if args:
            raise PebbleError(f"line {line_number}: list_files() expected 0 arguments but got {len(args)}")
        return self.fs.list_files()

    def _host_file_time(self, args: list[object], line_number: int) -> str:
        name = self._require_string_arg("file_time", args, line_number, 1)
        try:
            path = self._resolve_user_path_to_host(name)
            if not path.exists():
                raise FileSystemError(f"file '{name}' does not exist")
            return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d, %H:%M:%S")
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_raw_file_time(self, args: list[object], line_number: int) -> str:
        name = self._require_string_arg("raw_file_time", args, line_number, 1)
        try:
            path = self._resolve_storage_path_to_host(name)
            if not path.exists():
                raise FileSystemError(f"file '{name}' does not exist")
            return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d, %H:%M:%S")
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_raw_file_exists(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("raw_file_exists", args, line_number, 1)
        try:
            if self.fs_mode in {"mfs", "mfs-import"} and name == ".__pebble_vfs__.db":
                return int(self.mfs_blob is not None)
            return int(self._resolve_storage_path_to_host(name).is_file())
        except FileSystemError:
            return 0

    def _host_raw_read_file(self, args: list[object], line_number: int) -> str:
        name = self._require_string_arg("raw_read_file", args, line_number, 1)
        if self.fs_mode in {"mfs", "mfs-import"} and name == ".__pebble_vfs__.db":
            if self.mfs_blob is None:
                raise PebbleError(f"line {line_number}: file '{name}' does not exist")
            return self.mfs_blob
        try:
            return self._resolve_storage_path_to_host(name).read_text(encoding="utf-8")
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        except FileNotFoundError as exc:
            raise PebbleError(f"line {line_number}: file '{name}' does not exist") from exc

    def _host_raw_write_file(self, args: list[object], line_number: int) -> str:
        name, text = self._require_name_and_text("raw_write_file", args, line_number)
        if self.fs_mode in {"mfs", "mfs-import"} and name == ".__pebble_vfs__.db":
            self.mfs_blob = text
            return text
        try:
            path = self._resolve_storage_path_to_host(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return text

    def _host_raw_directory_exists(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("raw_directory_exists", args, line_number, 1)
        try:
            return int(self._resolve_storage_path_to_host(name).is_dir())
        except FileSystemError:
            return 0

    def _host_raw_create_file(self, args: list[object], line_number: int) -> int:
        name, text = self._require_name_and_text("raw_create_file", args, line_number)
        try:
            path = self._resolve_storage_path_to_host(name)
            if path.exists():
                raise FileSystemError(f"file '{name}' already exists")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_raw_modify_file(self, args: list[object], line_number: int) -> int:
        name, text = self._require_name_and_text("raw_modify_file", args, line_number)
        try:
            path = self._resolve_storage_path_to_host(name)
            if not path.exists():
                raise FileSystemError(f"file '{name}' does not exist")
            path.write_text(text, encoding="utf-8")
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_raw_delete_file(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("raw_delete_file", args, line_number, 1)
        try:
            path = self._resolve_storage_path_to_host(name)
            if not path.exists():
                raise FileSystemError(f"file '{name}' does not exist")
            path.unlink()
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_raw_make_directory(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("raw_make_directory", args, line_number, 1)
        try:
            path = self._resolve_storage_path_to_host(name)
            if path.exists():
                raise FileSystemError(f"directory '{name}' already exists")
            path.mkdir(parents=True, exist_ok=False)
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_raw_remove_directory(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("raw_remove_directory", args, line_number, 1)
        try:
            path = self._resolve_storage_path_to_host(name)
            if not path.exists() or not path.is_dir():
                raise FileSystemError(f"directory '{name}' does not exist")
            path.rmdir()
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_raw_directory_empty(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("raw_directory_empty", args, line_number, 1)
        try:
            path = self._resolve_storage_path_to_host(name)
            if not path.exists() or not path.is_dir():
                raise FileSystemError(f"directory '{name}' does not exist")
            return int(not any(path.iterdir()))
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_file_exists(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("file_exists", args, line_number, 1)
        try:
            if self.fs_mode in {"mfs", "mfs-import"} and name == ".__pebble_vfs__.db":
                return int(self.mfs_blob is not None)
            return int(self._resolve_user_path_to_host(name).is_file())
        except FileSystemError:
            return 0

    def _host_create_file(self, args: list[object], line_number: int) -> int:
        name, text = self._require_name_and_text("create_file", args, line_number)
        try:
            path = self._resolve_user_path_to_host(name)
            if path.exists():
                raise FileSystemError(f"file '{name}' already exists")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_make_directory(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("make_directory", args, line_number, 1)
        try:
            path = self._resolve_user_path_to_host(name)
            if path.exists():
                raise FileSystemError(f"directory '{name}' already exists")
            path.mkdir(parents=True, exist_ok=False)
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_modify_file(self, args: list[object], line_number: int) -> int:
        name, text = self._require_name_and_text("modify_file", args, line_number)
        try:
            path = self._resolve_user_path_to_host(name)
            if not path.exists():
                raise FileSystemError(f"file '{name}' does not exist")
            path.write_text(text, encoding="utf-8")
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_remove_directory(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("remove_directory", args, line_number, 1)
        try:
            path = self._resolve_user_path_to_host(name)
            if not path.exists() or not path.is_dir():
                raise FileSystemError(f"directory '{name}' does not exist")
            path.rmdir()
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_delete_file(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("delete_file", args, line_number, 1)
        try:
            path = self._resolve_user_path_to_host(name)
            if not path.exists():
                raise FileSystemError(f"file '{name}' does not exist")
            path.unlink()
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_directory_exists(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("directory_exists", args, line_number, 1)
        try:
            return int(self._resolve_user_path_to_host(name).is_dir())
        except FileSystemError:
            return 0

    def _host_directory_empty(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("directory_empty", args, line_number, 1)
        try:
            path = self._resolve_user_path_to_host(name)
            if not path.exists() or not path.is_dir():
                raise FileSystemError(f"directory '{name}' does not exist")
            return int(not any(path.iterdir()))
        except (FileSystemError, OSError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_capture_text(self, args: list[object], line_number: int) -> str:
        if args:
            raise PebbleError(f"line {line_number}: capture_text() expected 0 arguments but got {len(args)}")
        print("enter text, finish with a single '.' on its own line")
        lines: list[str] = []
        while True:
            line = input("... ")
            if line == ".":
                return "\n".join(lines)
            lines.append(line)

    def _host_run_program(self, args: list[object], line_number: int) -> int:
        if len(args) != 2:
            raise PebbleError(f"line {line_number}: run_program() expected 2 arguments but got {len(args)}")
        if not isinstance(args[0], str):
            raise PebbleError(f"line {line_number}: run_program() expects a string file name")
        argv = args[1]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise PebbleError(f"line {line_number}: run_program() expects a list of string arguments")
        try:
            self._run_program(args[0], argv, exec_mode="interp")
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_exec_program(self, args: list[object], line_number: int) -> int:
        if len(args) != 2:
            raise PebbleError(f"line {line_number}: exec_program() expected 2 arguments but got {len(args)}")
        if not isinstance(args[0], str):
            raise PebbleError(f"line {line_number}: exec_program() expects a string file name")
        argv = args[1]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise PebbleError(f"line {line_number}: exec_program() expects a list of string arguments")
        try:
            self._run_program(args[0], argv, exec_mode="bytecode")
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_start_background_job(self, args: list[object], line_number: int) -> int:
        if len(args) != 3:
            raise PebbleError(f"line {line_number}: start_background_job() expected 3 arguments but got {len(args)}")
        if not isinstance(args[0], str) or not isinstance(args[2], str):
            raise PebbleError(f"line {line_number}: start_background_job() expects string arguments")
        argv = args[1]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise PebbleError(f"line {line_number}: start_background_job() expects a list of string arguments")
        mode = args[2]
        if mode not in {"interp", "bytecode"}:
            raise PebbleError(f"line {line_number}: invalid background job mode")
        try:
            return self._start_background_job(args[0], argv, mode)
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_list_background_jobs(self, args: list[object], line_number: int) -> list[str]:
        if args:
            raise PebbleError(f"line {line_number}: list_background_jobs() expected 0 arguments but got {len(args)}")
        return self._list_background_jobs()

    def _host_list_processes(self, args: list[object], line_number: int) -> list[str]:
        if args:
            raise PebbleError(f"line {line_number}: list_processes() expected 0 arguments but got {len(args)}")
        return self._list_processes()

    def _host_list_process_records(self, args: list[object], line_number: int) -> list[dict[str, object]]:
        if args:
            raise PebbleError(f"line {line_number}: list_process_records() expected 0 arguments but got {len(args)}")
        return [self._process_record_to_dict(record) for record in self._collect_process_records()]

    def _host_list_signal_events(self, args: list[object], line_number: int) -> list[dict[str, object]]:
        if args:
            raise PebbleError(f"line {line_number}: list_signal_events() expected 0 arguments but got {len(args)}")
        return list(self._pending_signal_events)

    def _host_list_child_processes(self, args: list[object], line_number: int) -> list[dict[str, object]]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: list_child_processes() expects 1 integer argument")
        return [self._process_record_to_dict(record) for record in self._child_process_records(args[0])]

    def _host_current_foreground_pgid(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: current_foreground_pgid() expected 0 arguments but got {len(args)}")
        if self._foreground_pgid is None:
            return 0
        return self._foreground_pgid

    def _host_drain_signal_events(self, args: list[object], line_number: int) -> list[dict[str, object]]:
        if args:
            raise PebbleError(f"line {line_number}: drain_signal_events() expected 0 arguments but got {len(args)}")
        events = list(self._pending_signal_events)
        self._pending_signal_events = []
        return events

    def _host_foreground_job(self, args: list[object], line_number: int) -> list[str]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: foreground_job() expects 1 integer argument")
        try:
            return self._foreground_job(args[0])
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_wait_process(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: wait_process() expects 1 integer argument")
        try:
            return self._wait_process(args[0])
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_wait_child_process(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: wait_child_process() expects 1 integer argument")
        try:
            return self._wait_child_process(args[0])
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_reap_process(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: reap_process() expects 1 integer argument")
        try:
            return self._reap_process(args[0])
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_vm_create_task(self, args: list[object], line_number: int) -> int:
        if len(args) != 2:
            raise PebbleError(f"line {line_number}: vm_create_task() expected 2 arguments but got {len(args)}")
        if not isinstance(args[0], str):
            raise PebbleError(f"line {line_number}: vm_create_task() expects source text as the first argument")
        argv = args[1]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise PebbleError(f"line {line_number}: vm_create_task() expects a list of string arguments")
        runtime = self._make_runtime(consume_output=False)
        interpreter = PebbleBytecodeInterpreter(
            self.fs.root,
            input_provider=lambda prompt="": (_ for _ in ()).throw(
                PebbleError("input() is not available in scheduler-driven bytecode tasks")
            ),
            output_consumer=None,
            path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, self.cwd)),
            host_functions=runtime.host_functions,
        )
        initial_globals = {
            "ARGV": list(argv),
            "ARGC": len(argv),
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "FS_MODE": self.fs_mode,
            "CWD": self.cwd,
        }
        try:
            interpreter.prepare(args[0], initial_globals=initial_globals)
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        task_id = self._next_job_id
        self._next_job_id = self._next_job_id + 1
        with self._vm_lock:
            self._vm_tasks[task_id] = VMTask(
                task_id=task_id,
                command="vm",
                program="<source>",
                argv=list(argv),
                cwd=self.cwd,
                interpreter=interpreter,
            )
        return task_id

    def _host_vm_step_task(self, args: list[object], line_number: int) -> int:
        if len(args) != 2 or not isinstance(args[0], int) or not isinstance(args[1], int):
            raise PebbleError(f"line {line_number}: vm_step_task() expects task id and step count integers")
        with self._vm_lock:
            task = self._vm_tasks.get(args[0])
        if task is None:
            raise PebbleError(f"line {line_number}: vm task {args[0]} does not exist")
        if args[1] < 0:
            raise PebbleError(f"line {line_number}: vm_step_task() step count cannot be negative")
        if task.status in {"halted", "error"}:
            return 0
        try:
            task.status = "running"
            count = task.interpreter.run_steps(args[1])
            task.status = "halted" if task.interpreter.vm_state.halted else "ready"
            return count
        except PebbleError as exc:
            task.status = "error"
            task.error = str(exc)
            return 0

    def _host_vm_task_status(self, args: list[object], line_number: int) -> str:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: vm_task_status() expects one integer task id")
        with self._vm_lock:
            task = self._vm_tasks.get(args[0])
        if task is None:
            raise PebbleError(f"line {line_number}: vm task {args[0]} does not exist")
        if task.status == "error" and task.error is not None:
            return "error: " + task.error
        return task.status

    def _host_vm_take_task_output(self, args: list[object], line_number: int) -> list[str]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: vm_take_task_output() expects one integer task id")
        with self._vm_lock:
            task = self._vm_tasks.get(args[0])
        if task is None:
            raise PebbleError(f"line {line_number}: vm task {args[0]} does not exist")
        outputs = list(task.interpreter.output[task.outputs_consumed :])
        task.outputs_consumed = len(task.interpreter.output)
        return outputs

    def _host_vm_snapshot_task(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: vm_snapshot_task() expects one integer task id")
        with self._vm_lock:
            task = self._vm_tasks.get(args[0])
        if task is None:
            raise PebbleError(f"line {line_number}: vm task {args[0]} does not exist")
        snapshot_id = self._next_vm_snapshot_id
        self._next_vm_snapshot_id = self._next_vm_snapshot_id + 1
        self._vm_snapshots[snapshot_id] = task.interpreter.snapshot()
        return snapshot_id

    def _host_vm_restore_task(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: vm_restore_task() expects one integer snapshot id")
        snapshot = self._vm_snapshots.get(args[0])
        if snapshot is None:
            raise PebbleError(f"line {line_number}: vm snapshot {args[0]} does not exist")
        runtime = self._make_runtime(consume_output=False)
        interpreter = PebbleBytecodeInterpreter(
            self.fs.root,
            input_provider=lambda prompt="": (_ for _ in ()).throw(
                PebbleError("input() is not available in scheduler-driven bytecode tasks")
            ),
            output_consumer=None,
            path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, self.cwd)),
            host_functions=runtime.host_functions,
        )
        interpreter.restore(snapshot)
        task_id = self._next_job_id
        self._next_job_id = self._next_job_id + 1
        with self._vm_lock:
            self._vm_tasks[task_id] = VMTask(
                task_id=task_id,
                command="vm",
                program="<restored>",
                argv=[],
                cwd=self.cwd,
                interpreter=interpreter,
                outputs_consumed=len(interpreter.output),
                status="halted" if interpreter.vm_state.halted else "ready",
            )
        return task_id

    def _host_vm_drop_task(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: vm_drop_task() expects one integer task id")
        with self._vm_lock:
            if self._vm_tasks.pop(args[0], None) is None:
                raise PebbleError(f"line {line_number}: vm task {args[0]} does not exist")
        return 0

    def _host_cwd(self, args: list[object], line_number: int) -> str:
        if args:
            raise PebbleError(f"line {line_number}: cwd() expected 0 arguments but got {len(args)}")
        return self.cwd

    def _host_chdir(self, args: list[object], line_number: int) -> str:
        name = self._require_string_arg("chdir", args, line_number, 1)
        try:
            logical = self._normalize_user_path(name)
            host_path = self._logical_path_to_host(logical)
            if not host_path.exists() or not host_path.is_dir():
                raise FileSystemError(f"directory '{name}' does not exist")
            self.cwd = logical
            return self.cwd
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_filesystem_file_count(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(
                f"line {line_number}: filesystem_file_count() expected 0 arguments but got {len(args)}"
            )
        return len(self.fs.list_files())

    def _host_filesystem_total_bytes(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(
                f"line {line_number}: filesystem_total_bytes() expected 0 arguments but got {len(args)}"
            )
        total = 0
        for name in self.fs.list_files():
            total = total + self._logical_path_to_host("/" + name).stat().st_size
        return total

    def _host_filesystem_sync(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: filesystem_sync() expected 0 arguments but got {len(args)}")
        if self.fs_mode not in {"mfs", "mfs-import"}:
            return 0
        if self.mfs_blob is None:
            return 0
        backing = self.fs.resolve_path(".__pebble_vfs__.db")
        backing.write_text(self.mfs_blob, encoding="utf-8")
        return 1

    def _host_term_write(self, args: list[object], line_number: int) -> str:
        text = self._require_string_arg("term_write", args, line_number, 1)
        sys.stdout.write(text)
        return text

    def _host_term_flush(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: term_flush() expected 0 arguments but got {len(args)}")
        sys.stdout.flush()
        return 0

    def _host_term_clear(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: term_clear() expected 0 arguments but got {len(args)}")
        sys.stdout.write("\x1b[2J\x1b[H")
        return 0

    def _host_term_move(self, args: list[object], line_number: int) -> int:
        if len(args) != 2 or not isinstance(args[0], int) or not isinstance(args[1], int):
            raise PebbleError(f"line {line_number}: term_move() expects 2 integer arguments")
        sys.stdout.write(f"\x1b[{args[0]};{args[1]}H")
        return 0

    def _host_term_hide_cursor(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: term_hide_cursor() expected 0 arguments but got {len(args)}")
        sys.stdout.write("\x1b[?25l")
        return 0

    def _host_term_show_cursor(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: term_show_cursor() expected 0 arguments but got {len(args)}")
        sys.stdout.write("\x1b[?25h")
        return 0

    def _host_term_read_key(self, args: list[object], line_number: int) -> str:
        if args:
            raise PebbleError(f"line {line_number}: term_read_key() expected 0 arguments but got {len(args)}")
        return self._read_terminal_key(line_number, None)

    def _host_term_read_key_timeout(self, args: list[object], line_number: int) -> str:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: term_read_key_timeout() expects 1 integer argument")
        timeout_ms = args[0]
        if timeout_ms < 0:
            raise PebbleError(f"line {line_number}: term_read_key_timeout() timeout cannot be negative")
        return self._read_terminal_key(line_number, timeout_ms / 1000.0)

    def _read_terminal_key(self, line_number: int, timeout_seconds: float | None) -> str:
        if not self._terminal_access_allowed():
            return ""
        if not sys.stdin.isatty():
            raise PebbleError(f"line {line_number}: term_read_key() requires an interactive terminal")
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        interrupt_requested = False
        timed_out = False
        result = ""
        try:
            tty.setraw(fd)
            if timeout_seconds is not None:
                ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
                if not ready:
                    timed_out = True
                    result = ""
            if not timed_out:
                first = sys.stdin.read(1)
                if first == "\x1b":
                    ready, _, _ = select.select([sys.stdin], [], [], 0.03)
                    if not ready:
                        interrupt_requested = True
                    else:
                        second = sys.stdin.read(1)
                        if second == "O":
                            third = sys.stdin.read(1)
                            if third == "P":
                                self._detach_requested.set()
                                result = ""
                            else:
                                result = "ESC"
                        elif second == "[":
                            third = sys.stdin.read(1)
                            if third == "[":
                                fourth = sys.stdin.read(1)
                                if fourth == "A":
                                    self._detach_requested.set()
                                    result = ""
                                else:
                                    result = "ESC"
                            elif third == "A":
                                result = "UP"
                            elif third == "B":
                                result = "DOWN"
                            elif third == "C":
                                result = "RIGHT"
                            elif third == "D":
                                result = "LEFT"
                            elif third == "H":
                                result = "HOME"
                            elif third == "F":
                                result = "END"
                            elif third in {"1", "3", "4", "5", "6", "7", "8"}:
                                fourth = sys.stdin.read(1)
                                if fourth == "~":
                                    if third in {"1", "7"}:
                                        result = "HOME"
                                    elif third == "3":
                                        result = "DELETE"
                                    elif third in {"4", "8"}:
                                        result = "END"
                                    elif third == "5":
                                        result = "PAGEUP"
                                    elif third == "6":
                                        result = "PAGEDOWN"
                        else:
                            result = "ESC"
                elif first == "\x03":
                    interrupt_requested = True
                elif first == "\x1a":
                    self._detach_requested.set()
                    result = ""
                elif first in {"\r", "\n"}:
                    result = "ENTER"
                elif first == "\x7f":
                    result = "BACKSPACE"
                elif first == "\x18":
                    result = "^X"
                elif first == "\x0f":
                    result = "^O"
                else:
                    result = first
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        if interrupt_requested:
            raise KeyboardInterrupt
        return result

    def _terminal_access_allowed(self) -> bool:
        current = threading.get_ident()
        owner = self._terminal_owner_thread_id
        if owner is not None:
            return current == owner
        return current == self._main_thread_id

    def _capture_terminal_settings(self):
        if not sys.stdin.isatty():
            return None
        try:
            return termios.tcgetattr(sys.stdin.fileno())
        except termios.error:
            return None

    def _restore_shell_terminal(self) -> None:
        if self._shell_terminal_settings is None or not sys.stdin.isatty():
            return
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._shell_terminal_settings)
        except termios.error:
            return

    def _emit_runtime_output(self, text: str) -> None:
        if sys.stdout.isatty():
            sys.stdout.write(text + "\r\n")
            sys.stdout.flush()
            return
        print(text, flush=True)

    def _reset_to_prompt_line(self) -> None:
        if not sys.stdout.isatty():
            return
        sys.stdout.write("\r\n")
        sys.stdout.flush()

    def _host_term_rows(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: term_rows() expected 0 arguments but got {len(args)}")
        return shutil.get_terminal_size((80, 24)).lines

    def _host_term_cols(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: term_cols() expected 0 arguments but got {len(args)}")
        return shutil.get_terminal_size((80, 24)).columns

    def _host_current_time(self, args: list[object], line_number: int) -> str:
        if args:
            raise PebbleError(f"line {line_number}: current_time() expected 0 arguments but got {len(args)}")
        return datetime.now().strftime("%Y-%m-%d, %H:%M:%S")

    def _host_runtime_error(self, args: list[object], line_number: int) -> int:
        message = self._require_string_arg("runtime_error", args, line_number, 1)
        raise PebbleError(f"line {line_number}: {message}")

    def _create_vm_task(self, name: str, extra_args: list[str], command_name: str, cwd: str | None = None) -> int:
        cwd_value = self.cwd if cwd is None else cwd
        program = self._normalize_user_path(name, cwd_value)
        runtime_source = self.fs.read_file("system/runtime.peb")
        source = runtime_source + "\n" + self.fs.read_file(program.lstrip("/"))
        runtime = self._make_runtime(consume_output=False)
        interpreter = PebbleBytecodeInterpreter(
            self.fs.root,
            input_provider=lambda prompt="": (_ for _ in ()).throw(
                PebbleError("input() is not available in scheduler-driven bytecode tasks")
            ),
            output_consumer=None,
            path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, cwd_value)),
            host_functions=runtime.host_functions,
        )
        initial_globals = {
            "ARGV": list(extra_args),
            "ARGC": len(extra_args),
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "FS_MODE": self.fs_mode,
            "CWD": cwd_value,
        }
        interpreter.prepare(source, initial_globals=initial_globals)
        with self._vm_lock:
            task_id = self._next_job_id
            self._next_job_id = self._next_job_id + 1
            self._vm_tasks[task_id] = VMTask(
                task_id=task_id,
                command=command_name + " " + program,
                program=program,
                argv=list(extra_args),
                cwd=cwd_value,
                interpreter=interpreter,
                ppid=1,
                pgid=task_id,
                sid=1,
            )
        return task_id

    def _vm_scheduler_loop(self) -> None:
        while True:
            time.sleep(0.01)
            with self._vm_lock:
                tasks = [self._vm_tasks[key] for key in sorted(self._vm_tasks)]
            for task in tasks:
                if task.status in {"halted", "error"} or task.attached:
                    continue
                try:
                    task.status = "running"
                    task.interpreter.run_steps(10)
                    task.status = "halted" if task.interpreter.vm_state.halted else "ready"
                    self._notify_sigchld_for_task(task)
                except PebbleError as exc:
                    task.status = "error"
                    task.error = str(exc)
                    self._notify_sigchld_for_task(task)

    def _attach_foreground_vm_task(self, task_id: int) -> bool:
        with self._vm_lock:
            task = self._vm_tasks.get(task_id)
        if task is None:
            raise PebbleError(f"job {task_id} does not exist")
        task.attached = True
        self._foreground_pgid = task.pgid
        self._terminal_owner_thread_id = self._main_thread_id
        while True:
            if task.status not in {"halted", "error"}:
                try:
                    task.status = "running"
                    task.interpreter.run_steps(5)
                    task.status = "halted" if task.interpreter.vm_state.halted else "ready"
                    self._notify_sigchld_for_task(task)
                except PebbleError as exc:
                    task.status = "error"
                    task.error = str(exc)
                    self._notify_sigchld_for_task(task)
            self._emit_new_vm_output(task)
            if task.status in {"halted", "error"}:
                task.attached = False
                if self._foreground_pgid == task.pgid:
                    self._foreground_pgid = None
                self._terminal_owner_thread_id = None
                self._emit_new_vm_output(task)
                with self._vm_lock:
                    self._vm_tasks.pop(task_id, None)
                if task.error is not None:
                    raise PebbleError(task.error)
                if task.outputs_consumed == 0:
                    print("(no output)")
                return False
            action = self._poll_foreground_job_action()
            if action == "interrupt":
                self._emit_signal_event("SIGINT", task.task_id, task.pgid, "vm", "foreground")
                task.attached = False
                if self._foreground_pgid == task.pgid:
                    self._foreground_pgid = None
                self._terminal_owner_thread_id = None
                with self._vm_lock:
                    self._vm_tasks.pop(task_id, None)
                print("^C")
                print("[system] program interrupted", flush=True)
                return False
            if action == "detach":
                self._emit_signal_event("SIGTSTP", task.task_id, task.pgid, "vm", "foreground")
                task.attached = False
                if self._foreground_pgid == task.pgid:
                    self._foreground_pgid = None
                self._terminal_owner_thread_id = None
                return True
            time.sleep(0.02)

    def _emit_new_vm_output(self, task: VMTask) -> None:
        while task.outputs_consumed < len(task.interpreter.output):
            self._emit_runtime_output(task.interpreter.output[task.outputs_consumed])
            task.outputs_consumed = task.outputs_consumed + 1

    def _run_program(self, name: str, extra_args: list[str], exec_mode: str = "interp", cwd: str | None = None) -> None:
        if name in {"nano.peb", "system/nano.peb"} or not sys.stdin.isatty():
            _, output, error = self._execute_program(name, extra_args, exec_mode=exec_mode, cwd=cwd)
            if error is not None:
                if error == "__interrupt__":
                    print("^C")
                    print("[system] program interrupted", flush=True)
                    return
                raise PebbleError(error)
            if not output:
                return
            return
        try:
            job_id = self._create_vm_task(name, extra_args, "exec" if exec_mode == "bytecode" else "run", cwd=cwd)
        except (FileSystemError, PebbleError):
            job_id = self._start_background_job(
                name,
                extra_args,
                exec_mode,
                command_name="exec" if exec_mode == "bytecode" else "run",
            )
            self._detach_requested.clear()
            detached = self._attach_foreground_job(job_id)
            self._restore_shell_terminal()
            self._reset_to_prompt_line()
            if detached:
                print(f"[{job_id}] background", flush=True)
            return
        self._detach_requested.clear()
        detached = self._attach_foreground_vm_task(job_id)
        self._restore_shell_terminal()
        self._reset_to_prompt_line()
        if detached:
            print(f"[{job_id}] background", flush=True)

    def _execute_program(
        self,
        name: str,
        extra_args: list[str],
        exec_mode: str = "interp",
        cwd: str | None = None,
        output_consumer=None,
        input_provider=None,
    ) -> tuple[bool, list[str], str | None]:
        cwd_value = self.cwd if cwd is None else cwd
        source_name = name.lstrip("/")
        runtime_source = self.fs.read_file("system/runtime.peb")
        source = runtime_source + "\n" + self.fs.read_file(source_name)
        initial_globals = {
            "ARGV": extra_args,
            "ARGC": len(extra_args),
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "FS_MODE": self.fs_mode,
            "CWD": cwd_value,
        }
        provider = input if input_provider is None else input_provider
        if exec_mode == "bytecode":
            interpreter = PebbleBytecodeInterpreter(
                self.fs.root,
                input_provider=provider,
                output_consumer=output_consumer or self._emit_runtime_output,
                path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, cwd_value)),
                host_functions=self._make_runtime(consume_output=False).host_functions,
            )
        else:
            interpreter = PebbleInterpreter(
                self.fs.root,
                input_provider=provider,
                output_consumer=output_consumer or self._emit_runtime_output,
                path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, cwd_value)),
                host_functions=self._make_runtime(consume_output=False).host_functions,
            )
        interactive_program = name in {"nano.peb", "system/nano.peb"}
        if interactive_program and extra_args:
            target_file = extra_args[0]
            self._logical_path_to_host(self._normalize_user_path(target_file, cwd_value))
            try:
                file_content = self.fs.read_file(target_file.lstrip("/"))
            except FileSystemError:
                file_content = ""
            initial_globals["TARGET_FILE"] = target_file
            initial_globals["FILE_CONTENT"] = file_content

        try:
            output = interpreter.execute(source, initial_globals=initial_globals)
        except KeyboardInterrupt:
            return interactive_program, [], "__interrupt__"
        except (FileSystemError, PebbleError) as exc:
            return interactive_program, [], str(exc)
        return interactive_program, output, None

    def _start_background_job(
        self,
        name: str,
        extra_args: list[str],
        exec_mode: str,
        command_name: str | None = None,
    ) -> int:
        if name in {"nano.peb", "system/nano.peb"}:
            raise PebbleError("interactive programs cannot run in the background")
        program = self._normalize_user_path(name)
        cwd_value = self.cwd
        with self._jobs_lock:
            job_id = self._next_job_id
            self._next_job_id = self._next_job_id + 1
            job = BackgroundJob(
                job_id=job_id,
                command=(command_name or ("execbg" if exec_mode == "bytecode" else "runbg")) + " " + program,
                program=program,
                argv=list(extra_args),
                exec_mode=exec_mode,
                cwd=cwd_value,
                ppid=1,
                pgid=job_id,
                sid=1,
            )
            self._jobs[job_id] = job

        def worker() -> None:
            try:
                _, _, error = self._execute_program(
                    job.program,
                    job.argv,
                    exec_mode=job.exec_mode,
                    cwd=job.cwd,
                    output_consumer=job.outputs.append,
                    input_provider=lambda prompt="": (_ for _ in ()).throw(
                        PebbleError("input() is not available in background jobs")
                    ),
                )
                with self._jobs_lock:
                    if error is None:
                        job.status = "done"
                    elif error == "__interrupt__":
                        job.status = "interrupted"
                    else:
                        job.status = "error"
                        job.error = error
                    self._notify_sigchld_for_job(job)
            except Exception as exc:
                with self._jobs_lock:
                    job.status = "error"
                    job.error = str(exc)
                    self._notify_sigchld_for_job(job)

        thread = threading.Thread(target=worker, name=f"pebble-job-{job_id}", daemon=True)
        job.thread = thread
        thread.start()
        return job_id

    def _attach_foreground_job(self, job_id: int) -> bool:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise PebbleError(f"job {job_id} does not exist")
        self._foreground_pgid = job.pgid
        self._terminal_owner_thread_id = job.thread.ident if job.thread is not None else None
        while True:
            self._emit_new_job_output(job)
            if job.thread is not None and not job.thread.is_alive():
                self._terminal_owner_thread_id = None
                if self._foreground_pgid == job.pgid:
                    self._foreground_pgid = None
                self._emit_new_job_output(job)
                with self._jobs_lock:
                    self._jobs.pop(job_id, None)
                if job.error is not None:
                    if job.error == "__interrupt__":
                        print("^C")
                        print("[system] program interrupted", flush=True)
                        return False
                    raise PebbleError(job.error)
                if job.consumed_outputs == 0:
                    print("(no output)")
                return False
            action = self._poll_foreground_job_action()
            if action == "interrupt":
                self._emit_signal_event("SIGINT", job.job_id, job.pgid, "host-job", "foreground")
                if self._foreground_pgid == job.pgid:
                    self._foreground_pgid = None
                self._terminal_owner_thread_id = None
                return False
            if action == "detach":
                self._emit_signal_event("SIGTSTP", job.job_id, job.pgid, "host-job", "foreground")
                if self._foreground_pgid == job.pgid:
                    self._foreground_pgid = None
                self._terminal_owner_thread_id = None
                return True
            if job.thread is not None:
                if job.thread.ident is not None:
                    self._terminal_owner_thread_id = job.thread.ident
                job.thread.join(0.05)

    def _emit_new_job_output(self, job: BackgroundJob) -> None:
        while job.consumed_outputs < len(job.outputs):
            self._emit_runtime_output(job.outputs[job.consumed_outputs])
            job.consumed_outputs = job.consumed_outputs + 1

    def _poll_foreground_job_action(self) -> str | None:
        if self._detach_requested.is_set():
            self._detach_requested.clear()
            return "detach"
        if not sys.stdin.isatty():
            return None
        fd = sys.stdin.fileno()
        try:
            old_settings = termios.tcgetattr(fd)
        except termios.error:
            return None
        try:
            tty.setraw(fd)
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if not ready:
                return None
            first = sys.stdin.read(1)
            if first != "\x1b":
                return None
            ready, _, _ = select.select([sys.stdin], [], [], 0.03)
            if not ready:
                return None
            second = sys.stdin.read(1)
            return None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _list_background_jobs(self) -> list[str]:
        lines: list[str] = []
        for record in self._collect_process_records():
            lines.append(f"[{record.pid}] {record.state} {record.command}")
        return lines

    def _list_processes(self) -> list[str]:
        lines: list[str] = []
        for record in self._collect_process_records():
            lines.append(
                f"{record.pid} {record.kind} {record.state} cwd={record.cwd} argv={len(record.argv)} cmd={record.command}"
            )
        return lines

    def _collect_process_records(self) -> list[HostProcessRecord]:
        records: list[HostProcessRecord] = []
        with self._vm_lock:
            vm_tasks = [self._vm_tasks[key] for key in sorted(self._vm_tasks)]
        for task in vm_tasks:
            state = task.status
            if task.attached:
                state = "foreground"
            elif state == "ready":
                state = "running"
            records.append(
                HostProcessRecord(
                    pid=task.task_id,
                    kind="vm",
                    state=state,
                    command=task.command,
                    program=task.program,
                    argv=list(task.argv),
                    cwd=task.cwd,
                    ppid=task.ppid,
                    pgid=task.pgid,
                    sid=task.sid,
                    attached=task.attached,
                    exit_status=self._task_exit_status(task),
                    error=task.error,
                )
            )
        with self._jobs_lock:
            jobs = [self._jobs[key] for key in sorted(self._jobs)]
        for job in jobs:
            state = job.status
            if state == "running" and job.thread is not None and not job.thread.is_alive():
                state = "done"
                job.status = "done"
            records.append(
                HostProcessRecord(
                    pid=job.job_id,
                    kind="host-job",
                    state=state,
                    command=job.command,
                    program=job.program,
                    argv=list(job.argv),
                    cwd=job.cwd,
                    ppid=job.ppid,
                    pgid=job.pgid,
                    sid=job.sid,
                    exit_status=self._job_exit_status(job, state),
                    error=job.error,
                )
            )
        return records

    def _child_process_records(self, parent_pid: int) -> list[HostProcessRecord]:
        children: list[HostProcessRecord] = []
        for record in self._collect_process_records():
            if record.ppid == parent_pid:
                children.append(record)
        return children

    def _emit_signal_event(self, signal: str, pid: int, pgid: int, kind: str, state: str) -> None:
        self._pending_signal_events.append(
            {"signal": signal, "pid": pid, "pgid": pgid, "kind": kind, "state": state}
        )

    def _notify_sigchld_for_task(self, task: VMTask) -> None:
        if task.task_id in self._signal_notified_pids:
            return
        if task.status not in {"halted", "error"}:
            return
        self._signal_notified_pids.add(task.task_id)
        self._emit_signal_event("SIGCHLD", task.task_id, task.pgid, "vm", task.status)

    def _notify_sigchld_for_job(self, job: BackgroundJob) -> None:
        if job.job_id in self._signal_notified_pids:
            return
        if job.status not in {"done", "interrupted", "error"}:
            return
        self._signal_notified_pids.add(job.job_id)
        self._emit_signal_event("SIGCHLD", job.job_id, job.pgid, "host-job", job.status)

    def _task_exit_status(self, task: VMTask) -> int:
        if task.status == "error":
            return 1
        if task.status == "halted":
            return 0
        return -1

    def _job_exit_status(self, job: BackgroundJob, state: str | None = None) -> int:
        current_state = job.status if state is None else state
        if current_state == "done":
            return 0
        if current_state == "interrupted":
            return 130
        if current_state == "error":
            return 1
        return -1

    def _process_record_to_dict(self, record: HostProcessRecord) -> dict[str, object]:
        return {
            "pid": record.pid,
            "kind": record.kind,
            "state": record.state,
            "command": record.command,
            "program": record.program,
            "argv": list(record.argv),
            "cwd": record.cwd,
            "ppid": record.ppid,
            "pgid": record.pgid,
            "sid": record.sid,
            "attached": int(record.attached),
            "exit_status": record.exit_status,
            "error": "" if record.error is None else record.error,
        }

    def _wait_process(self, pid: int) -> dict[str, object]:
        with self._vm_lock:
            vm_task = self._vm_tasks.get(pid)
        if vm_task is not None:
            while vm_task.status not in {"halted", "error"}:
                time.sleep(0.02)
                with self._vm_lock:
                    vm_task = self._vm_tasks.get(pid)
                if vm_task is None:
                    raise PebbleError(f"process {pid} does not exist")
            record = HostProcessRecord(
                pid=vm_task.task_id,
                kind="vm",
                state="error" if vm_task.status == "error" else "done",
                command=vm_task.command,
                program=vm_task.program,
                argv=list(vm_task.argv),
                cwd=vm_task.cwd,
                attached=vm_task.attached,
                exit_status=self._task_exit_status(vm_task),
                error=vm_task.error,
            )
            with self._vm_lock:
                self._vm_tasks.pop(pid, None)
            self._signal_notified_pids.discard(pid)
            return self._process_record_to_dict(record)

        with self._jobs_lock:
            job = self._jobs.get(pid)
        if job is None:
            raise PebbleError(f"process {pid} does not exist")
        if job.thread is not None and job.thread.is_alive():
            job.thread.join()
        state = job.status
        if state == "running" and job.thread is not None and not job.thread.is_alive():
            state = "done"
            job.status = "done"
        record = HostProcessRecord(
            pid=job.job_id,
            kind="host-job",
            state=state,
            command=job.command,
            program=job.program,
            argv=list(job.argv),
            cwd=job.cwd,
            exit_status=self._job_exit_status(job, state),
            error=job.error,
        )
        with self._jobs_lock:
            self._jobs.pop(pid, None)
        self._signal_notified_pids.discard(pid)
        return self._process_record_to_dict(record)

    def _reap_process(self, pid: int) -> dict[str, object]:
        return self._wait_process(pid)

    def _wait_child_process(self, parent_pid: int) -> dict[str, object]:
        saw_child = 0
        while True:
            children = self._child_process_records(parent_pid)
            if len(children) > 0:
                saw_child = 1
            i = 0
            while i < len(children):
                child = children[i]
                if child.exit_status != -1:
                    return self._wait_process(child.pid)
                i = i + 1
            if saw_child == 0:
                raise PebbleError(f"process {parent_pid} has no child processes")
            time.sleep(0.02)

    def _foreground_job(self, job_id: int) -> list[str]:
        with self._vm_lock:
            vm_task = self._vm_tasks.get(job_id)
        if vm_task is not None:
            self._detach_requested.clear()
            detached = self._attach_foreground_vm_task(job_id)
            self._restore_shell_terminal()
            self._reset_to_prompt_line()
            if detached:
                return [f"[{job_id}] background"]
            return []
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise PebbleError(f"job {job_id} does not exist")
        if job.thread is not None and job.thread.is_alive():
            job.thread.join()
        with self._jobs_lock:
            self._jobs.pop(job_id, None)
        lines = list(job.outputs[job.consumed_outputs :])
        job.consumed_outputs = len(job.outputs)
        if job.error is not None:
            lines.append(job.error)
        if len(lines) == 0 and job.status == "done":
            lines.append("(no output)")
        lines.append(f"[{job.job_id}] {job.status}")
        return lines

    def _require_string_arg(
        self,
        name: str,
        args: list[object],
        line_number: int,
        expected: int,
    ) -> str:
        if len(args) != expected:
            raise PebbleError(f"line {line_number}: {name}() expected {expected} arguments but got {len(args)}")
        if not isinstance(args[0], str):
            raise PebbleError(f"line {line_number}: {name}() expects a string argument")
        return args[0]

    def _require_name_and_text(self, name: str, args: list[object], line_number: int) -> tuple[str, str]:
        if len(args) != 2:
            raise PebbleError(f"line {line_number}: {name}() expected 2 arguments but got {len(args)}")
        if not isinstance(args[0], str) or not isinstance(args[1], str):
            raise PebbleError(f"line {line_number}: {name}() expects string arguments")
        return args[0], args[1]

    def _resolve_user_path_to_host(self, name: str) -> Path:
        return self._logical_path_to_host(self._normalize_user_path(name))

    def _resolve_storage_path_to_host(self, name: str) -> Path:
        if name.startswith("/"):
            logical = name
        elif name:
            logical = "/" + name
        else:
            logical = "/"
        return self._logical_path_to_host(logical)

    def _to_fs_name(self, name: str) -> str:
        logical = self._normalize_user_path(name)
        if logical == "/":
            raise FileSystemError("root is a directory")
        return logical.lstrip("/")

    def _normalize_user_path(self, name: str, cwd: str | None = None) -> str:
        cleaned = name.strip()
        if not cleaned:
            raise FileSystemError("file name cannot be empty")
        if "\\" in cleaned:
            raise FileSystemError("invalid file name")
        absolute = cleaned.startswith("/")
        raw_parts = cleaned.split("/")
        if absolute:
            raw_parts = raw_parts[1:]
        elif raw_parts and raw_parts[0] in self.fs.mounts:
            absolute = True
        parts: list[str] = []
        cwd_value = self.cwd if cwd is None else cwd
        if not absolute and cwd_value != "/":
            parts = cwd_value.strip("/").split("/")
        for part in raw_parts:
            if part == "" or part == ".":
                if part == "":
                    raise FileSystemError("invalid file path")
                continue
            if part == "..":
                if not parts:
                    raise FileSystemError("file path escapes the Pebble OS root")
                parts.pop()
                continue
            parts.append(part)
        if not parts:
            return "/"
        return "/" + "/".join(parts)

    def _logical_path_to_host(self, logical_path: str) -> Path:
        if logical_path == "/":
            return self.fs.root
        parts = logical_path.strip("/").split("/")
        first = parts[0]
        if first in self.fs.mounts:
            host_root = self.fs.mounts[first]
            if len(parts) == 1:
                return host_root
            path = (host_root / "/".join(parts[1:])).resolve()
            try:
                path.relative_to(host_root)
            except ValueError as exc:
                raise FileSystemError("mounted file path escapes its mount root") from exc
            return path
        root = self.fs.root.resolve()
        path = (root / "/".join(parts)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise FileSystemError("file path escapes the Pebble OS root") from exc
        return path

    def onecmd(self, line: str) -> bool | None:
        try:
            return super().onecmd(line)
        except ValueError as exc:
            print(exc)
            return None


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "pebble_disk"
    PebbleShell(root).cmdloop()


def build_shell(fs_mode: str = "hostfs") -> PebbleShell:
    root = Path(__file__).resolve().parent.parent / "pebble_disk"
    return PebbleShell(root, fs_mode=fs_mode)

from __future__ import annotations

import cmd
import json
import os
import queue
import select
import shutil
import shlex
import socket
import ssl
import time
import sys
import termios
import threading
import tty
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import time as time_module

from pebble_bootloader.fs import FileSystemError, FlatFileSystem
from pebble_bootloader.lang import (
    FunctionValue,
    PebbleBytecodeInterpreter,
    PebbleError,
    PebbleInputBlocked,
    PebbleMutexBlocked,
    PebbleInterpreter,
    PebbleTTYBlocked,
)


VALID_FS_MODES = {"hostfs", "mfs", "mfs-import", "vfs-import", "vfs-persistent"}
BACKGROUND_VM_STEP_BUDGET = 10
FOREGROUND_VM_STEP_BUDGET = 5
FOREGROUND_VM_KEY_PRIORITY_STEP_BUDGET = 20
FOREGROUND_VM_IDLE_SLEEP_SECONDS = 0.02
TTY_ESCAPE_SEQUENCE_GRACE_SECONDS = 0.05


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
    input_prompt: str | None = None
    pending_input: str | None = None
    pending_keys: list[str] = field(default_factory=list)
    tty_timeout_seconds: float | None = None
    pending_tty_bytes: list[str] = field(default_factory=list)
    pending_escape_started_at: float | None = None
    blocked_mutex_id: int | None = None


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
        self._color_enabled = self._detect_color_support()
        self.env: dict[str, str] = {
            "PATH": "/system/bin:/system/sbin:/bin",
            "PEBBLE_COLOR": "1" if self._color_enabled else "0",
        }
        self._command_history: list[str] = []
        self._runtime_env_override: dict[str, str] | None = None
        self.mfs_blob: str | None = None
        self._jobs: dict[int, BackgroundJob] = {}
        self._jobs_lock = threading.Lock()
        self._next_job_id = 1
        self._vm_tasks: dict[int, VMTask] = {}
        self._vm_lock = threading.Lock()
        self._vm_execution_context = threading.local()
        self._mutexes: dict[int, dict[str, object]] = {}
        self._next_mutex_id = 1
        self._vm_snapshots: dict[int, dict[str, object]] = {}
        self._next_vm_snapshot_id = 1
        self._pending_signal_events: list[dict[str, object]] = []
        self._signal_notified_pids: set[int] = set()
        self._foreground_pgid: int | None = None
        self._redirect_output_target: Path | None = None
        self._redirect_output_mode: str = "w"
        self._redirect_error_target: Path | None = None
        self._redirect_error_mode: str = "w"
        self._redirect_error_to_stdout: bool = False
        self._active_stdin_fd: int | None = None
        self._active_stdout_fd: int | None = None
        self._active_stderr_fd: int | None = None
        self._fd_table: dict[int, dict[str, object]] = {}
        self._next_fd = 3
        self._pebble_repl: PebbleInterpreter | None = None
        self._detach_requested = threading.Event()
        self._main_thread_id = threading.get_ident()
        self._terminal_owner_thread_id: int | None = None
        self._foreground_terminal_raw = False
        self._shell_terminal_settings = self._capture_terminal_settings()
        self.fs.mount("system", Path(__file__).resolve().parent.parent / "pebble_system")
        self._ensure_phase4_layout()
        self._refresh_shell_state()
        self._load_shell_profile()
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
            "history",
            "ls",
            "cd",
            "export",
            "set",
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
            "bg",
            "nano",
            "source",
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
        if command in {"touch", "edit", "cat", "rm", "run", "runbg", "exec", "execbg", "nano", "source"}:
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
        if command == "bg":
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
        parts = self._split_args(text)
        background = 0
        if parts and parts[len(parts) - 1] == "&":
            background = 1
            parts = parts[:-1]
        assignments, argv = self._split_assignment_prefix(parts)
        if not argv:
            if assignments:
                for name, value in assignments.items():
                    self.env[name] = value
            return None
        env_override = assignments or {}
        if background:
            env_override["PEBBLE_SHELL_BG"] = "1"
        if self._dispatch_runtime_command(argv[0], argv[1:], env_override=env_override or None):
            return True
        return None

    def onecmd(self, line: str) -> bool | None:
        stripped_line = line.strip()
        if stripped_line:
            self._command_history.append(stripped_line)
        pipeline = self._parse_pipeline(line)
        if pipeline is not None:
            previous_stdin_fd = self._active_stdin_fd
            previous_stdout_fd = self._active_stdout_fd
            previous_stderr_fd = self._active_stderr_fd
            created_fds: list[int] = []
            try:
                stop: bool | None = None
                index = 0
                current_input_fd = previous_stdin_fd
                while index < len(pipeline):
                    write_fd: int | None = None
                    next_input_fd: int | None = None
                    if index + 1 < len(pipeline):
                        read_fd, write_fd = self._create_pipe_fds()
                        created_fds.append(read_fd)
                        created_fds.append(write_fd)
                        next_input_fd = read_fd
                    self._active_stdin_fd = current_input_fd
                    self._active_stdout_fd = write_fd if write_fd is not None else previous_stdout_fd
                    self._active_stderr_fd = previous_stderr_fd
                    stop = super().onecmd(pipeline[index])
                    if write_fd is not None:
                        self._close_fd_record(write_fd)
                    current_input_fd = next_input_fd
                    index = index + 1
                return stop
            finally:
                self._active_stdin_fd = previous_stdin_fd
                self._active_stdout_fd = previous_stdout_fd
                self._active_stderr_fd = previous_stderr_fd
                i = 0
                while i < len(created_fds):
                    self._close_fd_record(created_fds[i])
                    i = i + 1
        redirected = self._parse_redirections(line)
        if redirected is None:
            try:
                return super().onecmd(line)
            except ValueError as exc:
                self._emit_runtime_error_output(str(exc))
                return None
        cleaned_line, stdin_path, stdout_path, stdout_mode, stderr_path, stderr_mode, stderr_to_stdout = redirected
        previous_output_target = self._redirect_output_target
        previous_output_mode = self._redirect_output_mode
        previous_error_target = self._redirect_error_target
        previous_error_mode = self._redirect_error_mode
        previous_error_to_stdout = self._redirect_error_to_stdout
        previous_stdin_fd = self._active_stdin_fd
        previous_stdout_fd = self._active_stdout_fd
        previous_stderr_fd = self._active_stderr_fd
        opened_fds: list[int] = []
        try:
            self._redirect_output_target = None if stdout_path is None else self._logical_path_to_host(
                self._normalize_user_path(stdout_path)
            )
            self._redirect_output_mode = stdout_mode
            self._redirect_error_target = None if stderr_path is None else self._logical_path_to_host(
                self._normalize_user_path(stderr_path)
            )
            self._redirect_error_mode = stderr_mode
            self._redirect_error_to_stdout = stderr_to_stdout
            self._active_stdin_fd = None
            self._active_stdout_fd = None
            self._active_stderr_fd = None
            if stdout_path is not None:
                stdout_fd = self._open_fd_record(self._normalize_user_path(stdout_path), stdout_mode)
                opened_fds.append(stdout_fd)
                self._active_stdout_fd = stdout_fd
            if stderr_to_stdout:
                self._active_stderr_fd = self._active_stdout_fd
            elif stderr_path is not None:
                stderr_fd = self._open_fd_record(self._normalize_user_path(stderr_path), stderr_mode)
                opened_fds.append(stderr_fd)
                self._active_stderr_fd = stderr_fd
            if stdin_path is not None:
                stdin_fd = self._open_fd_record(self._normalize_user_path(stdin_path), "r")
                opened_fds.append(stdin_fd)
                self._active_stdin_fd = stdin_fd
            return super().onecmd(cleaned_line)
        except FileNotFoundError as exc:
            self._emit_runtime_error_output(str(exc))
            return None
        except FileSystemError as exc:
            self._emit_runtime_error_output(str(exc))
            return None
        except ValueError as exc:
            self._emit_runtime_error_output(str(exc))
            return None
        finally:
            self._redirect_output_target = previous_output_target
            self._redirect_output_mode = previous_output_mode
            self._redirect_error_target = previous_error_target
            self._redirect_error_mode = previous_error_mode
            self._redirect_error_to_stdout = previous_error_to_stdout
            self._active_stdin_fd = previous_stdin_fd
            self._active_stdout_fd = previous_stdout_fd
            self._active_stderr_fd = previous_stderr_fd
            i = 0
            while i < len(opened_fds):
                self._close_fd_record(opened_fds[i])
                i = i + 1

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
            if recursive_fuzzy and not child.is_dir() and child.suffix != ".peb":
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
            if recursive_fuzzy and not suggestion.endswith(".peb"):
                continue
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
            self.prompt = self._format_prompt(prompt)
        if isinstance(intro, str) and intro:
            self.intro = intro

    def _detect_color_support(self) -> bool:
        if os.environ.get("PEBBLE_COLOR_FORCE") == "1":
            return True
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return False
        if os.environ.get("NO_COLOR"):
            return False
        term = os.environ.get("TERM", "")
        if term == "" or term == "dumb":
            return False
        return True

    def _format_prompt(self, prompt: str) -> str:
        if not self._color_enabled:
            return prompt
        return f"\x1b[01;32mpebble@pebble-os\x1b[00m:\x1b[01;34m{self.cwd}\x1b[00m$ "

    def _dispatch_runtime_command(
        self, command: str, arg: str | list[str], env_override: dict[str, str] | None = None
    ) -> bool:
        try:
            argv = self._split_args(arg) if isinstance(arg, str) else list(arg)
            result = self._system_shell_call(
                "shell_dispatch",
                [command, argv],
                consume_output=True,
                env_override=env_override,
            )
            if isinstance(result, str) and result.startswith("__cwd__:"):
                self.cwd = result[8:]
                return False
            return result == "__exit__"
        except KeyboardInterrupt:
            self._emit_runtime_error_output("^C")
            return False
        except (FileSystemError, PebbleError, ValueError) as exc:
            self._emit_runtime_error_output(str(exc))
            return False

    def _system_shell_call(
        self,
        function_name: str,
        args: list[object] | None = None,
        consume_output: bool = False,
        env_override: dict[str, str] | None = None,
    ) -> object:
        runtime_source = self.fs.read_file("system/runtime.peb")
        shell_source = self.fs.read_file("system/shell.peb")
        source = runtime_source + "\n" + shell_source
        runtime = self._make_runtime(consume_output=consume_output)
        env_map = dict(self.env)
        if env_override:
            env_map.update(env_override)
        initial_globals: dict[str, object] = {
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "SYSTEM_SHELL_PATH": "system/shell.peb",
            "SYSTEM_SHELL_SOURCE": shell_source,
            "FS_MODE": self.fs_mode,
            "CWD": self.cwd,
            "ENV": env_map,
            "PATH": env_map.get("PATH", ""),
        }
        call_parts: list[str] = []
        if args:
            for index, value in enumerate(args):
                name = f"__arg_{index}"
                initial_globals[name] = value
                call_parts.append(name)
        call_expr = f"{function_name}(" + ", ".join(call_parts) + ")"
        previous_runtime_env = self._runtime_env_override
        self._runtime_env_override = env_map
        try:
            runtime.execute(source + f"\n__result = {call_expr}\n", initial_globals=initial_globals)
        finally:
            self._runtime_env_override = previous_runtime_env
        if env_override is None:
            updated_env = runtime.globals.get("ENV")
            if isinstance(updated_env, dict):
                merged = dict(self.env)
                for name, value in updated_env.items():
                    merged[str(name)] = str(value)
                self.env = merged
        return runtime.globals.get("__result")

    def _make_runtime(self, consume_output: bool) -> PebbleInterpreter:
        consumer = None
        if consume_output:
            consumer = self._emit_runtime_output
        return PebbleInterpreter(
            self.fs.root,
            input_provider=self._runtime_input,
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
                "pebble_repl_start": self._host_pebble_repl_start,
                "pebble_repl_step": self._host_pebble_repl_step,
                "pebble_repl_stop": self._host_pebble_repl_stop,
                "net_lookup_host": self._host_net_lookup_host,
                "net_tcp_probe": self._host_net_tcp_probe,
                "net_http_get": self._host_net_http_get,
                "ai_chat_complete": self._host_ai_chat_complete,
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
                "kill_process": self._host_kill_process,
                "background_job": self._host_background_job,
                "source_shell_script": self._host_source_shell_script,
                "fd_open": self._host_fd_open,
                "fd_read": self._host_fd_read,
                "fd_write": self._host_fd_write,
                "fd_close": self._host_fd_close,
                "fd_stat": self._host_fd_stat,
                "fd_readdir": self._host_fd_readdir,
                "vm_create_task": self._host_vm_create_task,
                "vm_step_task": self._host_vm_step_task,
                "vm_task_status": self._host_vm_task_status,
                "vm_take_task_output": self._host_vm_take_task_output,
                "vm_snapshot_task": self._host_vm_snapshot_task,
                "vm_restore_task": self._host_vm_restore_task,
                "vm_drop_task": self._host_vm_drop_task,
                "thread_spawn_source_host": self._host_thread_spawn_source,
                "thread_spawn_host": self._host_thread_spawn,
                "thread_join_host": self._host_thread_join,
                "thread_status_host": self._host_thread_status,
                "thread_self_host": self._host_thread_self,
                "thread_yield_host": self._host_thread_yield,
                "list_thread_records": self._host_list_thread_records,
                "mutex_create_host": self._host_mutex_create,
                "mutex_lock_host": self._host_mutex_lock,
                "mutex_try_lock_host": self._host_mutex_try_lock,
                "mutex_unlock_host": self._host_mutex_unlock,
                "list_mutex_records": self._host_list_mutex_records,
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
                "term_owner_pgid": self._host_term_owner_pgid,
                "term_mode": self._host_term_mode,
                "term_state": self._host_term_state,
                "current_time": self._host_current_time,
                "shell_history": self._host_shell_history,
                "sleep": self._host_sleep,
                "runtime_error": self._host_runtime_error,
            },
        )

    def _runtime_input(self, prompt: str = "") -> str:
        if self._active_stdin_fd is not None:
            return self._fd_readline(self._active_stdin_fd)
        return input(prompt)

    def _open_fd_record(self, logical_path: str, mode: str) -> int:
        device_kind = self._device_kind_for_path(logical_path)
        if device_kind != "":
            return self._open_device_fd(device_kind, logical_path, mode)
        path = self._logical_path_to_host(logical_path)
        if mode not in {"r", "w", "a"}:
            raise FileSystemError(f"unsupported fd mode '{mode}'")
        if mode == "r" and not path.exists():
            raise FileNotFoundError(f"[Errno 2] No such file or directory: '{logical_path}'")
        if mode in {"w", "a"}:
            path.parent.mkdir(parents=True, exist_ok=True)
        fd = self._next_fd
        self._next_fd = self._next_fd + 1
        record: dict[str, object] = {"kind": "file", "path": path, "mode": mode, "cursor": 0}
        self._fd_table[fd] = record
        if mode == "w":
            path.write_text("", encoding="utf-8")
        return fd

    def _device_kind_for_path(self, logical_path: str) -> str:
        if logical_path in {"/dev", "/dev/tty", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/null"}:
            return logical_path
        return ""

    def _open_device_fd(self, device_kind: str, logical_path: str, mode: str) -> int:
        allowed_modes: dict[str, set[str]] = {
            "/dev": {"r"},
            "/dev/tty": {"r", "w", "a"},
            "/dev/stdin": {"r"},
            "/dev/stdout": {"w", "a"},
            "/dev/stderr": {"w", "a"},
            "/dev/null": {"r", "w", "a"},
        }
        if mode not in allowed_modes.get(device_kind, set()):
            raise FileSystemError(f"unsupported mode '{mode}' for device '{logical_path}'")
        fd = self._next_fd
        self._next_fd = self._next_fd + 1
        self._fd_table[fd] = {"kind": "device", "device": device_kind, "path": logical_path, "mode": mode, "cursor": 0}
        return fd

    def _create_pipe_fds(self) -> tuple[int, int]:
        shared: dict[str, object] = {"kind": "pipe", "buffer": [], "read_cursor": 0, "write_closed": False}
        read_fd = self._next_fd
        self._next_fd = self._next_fd + 1
        write_fd = self._next_fd
        self._next_fd = self._next_fd + 1
        self._fd_table[read_fd] = {"kind": "pipe", "mode": "r", "pipe": shared}
        self._fd_table[write_fd] = {"kind": "pipe", "mode": "w", "pipe": shared}
        return read_fd, write_fd

    def _close_fd_record(self, fd: int) -> None:
        record = self._fd_table.pop(fd, None)
        if record is None:
            return
        if record.get("kind") == "pipe" and record.get("mode") == "w":
            pipe = record.get("pipe")
            if isinstance(pipe, dict):
                pipe["write_closed"] = True

    def _fd_readline(self, fd: int) -> str:
        record = self._fd_table.get(fd)
        if record is None:
            return ""
        kind = record.get("kind")
        if kind == "device":
            device_kind = record.get("device")
            if device_kind == "/dev":
                return ""
            if device_kind == "/dev/null":
                return ""
            if device_kind in {"/dev/stdin", "/dev/tty"}:
                return input("")
            return ""
        if kind == "pipe":
            pipe = record.get("pipe")
            if not isinstance(pipe, dict):
                return ""
            buffer = pipe.get("buffer")
            cursor = pipe.get("read_cursor")
            if not isinstance(buffer, list) or not isinstance(cursor, int):
                return ""
            if cursor >= len(buffer):
                return ""
            pipe["read_cursor"] = cursor + 1
            return str(buffer[cursor])
        path = record.get("path")
        cursor = record.get("cursor")
        if not isinstance(path, Path) or not isinstance(cursor, int):
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
        if cursor >= len(lines):
            return ""
        record["cursor"] = cursor + 1
        return lines[cursor]

    def _write_fd_text(self, fd: int, text: str) -> None:
        record = self._fd_table.get(fd)
        if record is None:
            return
        kind = record.get("kind")
        if kind == "device":
            device_kind = record.get("device")
            if device_kind == "/dev":
                return
            if device_kind == "/dev/null":
                return
            if device_kind == "/dev/stderr":
                sys.stderr.write(text)
                sys.stderr.flush()
                return
            if device_kind in {"/dev/stdout", "/dev/tty"}:
                sys.stdout.write(text)
                sys.stdout.flush()
                return
            return
        if kind == "pipe":
            pipe = record.get("pipe")
            if not isinstance(pipe, dict):
                return
            buffer = pipe.get("buffer")
            if not isinstance(buffer, list):
                return
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                buffer.append(lines[i])
                i = i + 1
            return
        path = record.get("path")
        mode = record.get("mode")
        if not isinstance(path, Path) or not isinstance(mode, str):
            return
        existing = ""
        if mode == "a" and path.exists():
            existing = path.read_text(encoding="utf-8")
        if mode == "a":
            path.write_text(existing + text, encoding="utf-8")
        else:
            current = ""
            if path.exists():
                current = path.read_text(encoding="utf-8")
            path.write_text(current + text, encoding="utf-8")

    def _split_args(self, arg: str) -> list[str]:
        if not arg.strip():
            return []
        try:
            return self._shell_split(arg)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    def _split_assignment_prefix(self, parts: list[str]) -> tuple[dict[str, str], list[str]]:
        assignments: dict[str, str] = {}
        index = 0
        while index < len(parts):
            token = parts[index]
            if "=" not in token:
                break
            name, value = token.split("=", 1)
            if not self._is_assignment_name(name):
                break
            assignments[name] = value
            index = index + 1
        return assignments, parts[index:]

    def _is_assignment_name(self, name: str) -> bool:
        if not name:
            return False
        if not (name[0].isalpha() or name[0] == "_"):
            return False
        i = 1
        while i < len(name):
            ch = name[i]
            if not (ch.isalnum() or ch == "_"):
                return False
            i = i + 1
        return True

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

    def _start_pebble_repl(self) -> None:
        env_map = dict(self._runtime_env_override or self.env)
        initial_globals: dict[str, object] = {
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "FS_MODE": self.fs_mode,
            "CWD": self.cwd,
            "ENV": env_map,
            "PATH": env_map.get("PATH", ""),
        }
        runtime = self._make_runtime(consume_output=False)
        interpreter = PebbleInterpreter(
            self.fs.root,
            input_provider=self._runtime_input,
            output_consumer=self._emit_runtime_output,
            path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, self.cwd)),
            host_functions=runtime.host_functions,
        )
        runtime_source = self.fs.read_file("system/runtime.peb")
        interpreter.start_repl_session(initial_globals=initial_globals)
        interpreter.execute_repl_line(runtime_source)
        self._pebble_repl = interpreter

    def _host_pebble_repl_start(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: pebble_repl_start() expected 0 arguments but got {len(args)}")
        try:
            self._start_pebble_repl()
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_pebble_repl_step(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], str):
            raise PebbleError(f"line {line_number}: pebble_repl_step() expects 1 string argument")
        try:
            if self._pebble_repl is None:
                self._start_pebble_repl()
            self._pebble_repl.execute_repl_line(args[0])
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_pebble_repl_stop(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: pebble_repl_stop() expected 0 arguments but got {len(args)}")
        self._pebble_repl = None
        return 0

    def _host_net_lookup_host(self, args: list[object], line_number: int) -> dict[str, object]:
        host = self._require_string_arg("net_lookup_host", args, line_number, 1)
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            return {"ok": 0, "host": host, "addresses": [], "error": str(exc)}
        addresses: list[str] = []
        seen: set[str] = set()
        for info in infos:
            sockaddr = info[4]
            if not isinstance(sockaddr, tuple) or not sockaddr:
                continue
            address = str(sockaddr[0])
            if address in seen:
                continue
            seen.add(address)
            addresses.append(address)
        return {"ok": 1, "host": host, "addresses": addresses, "error": ""}

    def _host_net_tcp_probe(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 3:
            raise PebbleError(f"line {line_number}: net_tcp_probe() expected 3 arguments but got {len(args)}")
        host = args[0]
        port = args[1]
        timeout_ms = args[2]
        if not isinstance(host, str) or not isinstance(port, int) or not isinstance(timeout_ms, int):
            raise PebbleError(f"line {line_number}: net_tcp_probe() expects (string, int, int)")
        if port < 1 or port > 65535:
            raise PebbleError(f"line {line_number}: net_tcp_probe() port must be between 1 and 65535")
        if timeout_ms < 1:
            raise PebbleError(f"line {line_number}: net_tcp_probe() timeout must be positive")
        started = time_module.monotonic()
        try:
            with socket.create_connection((host, port), timeout_ms / 1000.0) as conn:
                peer = conn.getpeername()
                address = ""
                if isinstance(peer, tuple) and peer:
                    address = str(peer[0])
        except OSError as exc:
            elapsed = int((time_module.monotonic() - started) * 1000)
            return {
                "ok": 0,
                "host": host,
                "address": "",
                "port": port,
                "latency_ms": elapsed,
                "error": str(exc),
            }
        elapsed = int((time_module.monotonic() - started) * 1000)
        return {"ok": 1, "host": host, "address": address, "port": port, "latency_ms": elapsed, "error": ""}

    def _host_net_http_get(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 2:
            raise PebbleError(f"line {line_number}: net_http_get() expected 2 arguments but got {len(args)}")
        url = args[0]
        timeout_ms = args[1]
        if not isinstance(url, str) or not isinstance(timeout_ms, int):
            raise PebbleError(f"line {line_number}: net_http_get() expects (string, int)")
        if timeout_ms < 1:
            raise PebbleError(f"line {line_number}: net_http_get() timeout must be positive")
        request = urllib.request.Request(url, headers={"User-Agent": "PebbleOS/0.1.2"})
        insecure_tls = 0
        try:
            try:
                response_context = urllib.request.urlopen(request, timeout=timeout_ms / 1000.0)
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                if not isinstance(reason, ssl.SSLCertVerificationError) and "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                    raise
                insecure_tls = 1
                response_context = urllib.request.urlopen(
                    request,
                    timeout=timeout_ms / 1000.0,
                    context=ssl._create_unverified_context(),
                )
            with response_context as response:
                body = response.read().decode("utf-8", errors="replace")
                headers = [f"{name}: {value}" for name, value in response.headers.items()]
                status = int(getattr(response, "status", 200))
                final_url = str(response.geturl())
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
            return {"ok": 0, "status": 0, "body": "", "headers": [], "url": url, "error": str(exc), "insecure_tls": 0}
        return {
            "ok": 1,
            "status": status,
            "body": body,
            "headers": headers,
            "url": final_url,
            "error": "",
            "insecure_tls": insecure_tls,
        }

    def _host_ai_chat_complete(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 7:
            raise PebbleError(f"line {line_number}: ai_chat_complete() expected 7 arguments but got {len(args)}")
        if not all(isinstance(item, str) for item in args):
            raise PebbleError(f"line {line_number}: ai_chat_complete() expects string arguments")
        base_url, api_key, model, system_prompt, history_text, prompt, timeout_text = args
        try:
            timeout_seconds = max(1.0, int(timeout_text) / 1000.0)
        except ValueError as exc:
            raise PebbleError(f"line {line_number}: ai_chat_complete() timeout must be an integer string") from exc
        endpoint = base_url.rstrip("/")
        if endpoint.endswith("/chat/completions"):
            chat_url = endpoint
        elif endpoint.endswith("/v1"):
            chat_url = endpoint + "/chat/completions"
        else:
            chat_url = endpoint + "/v1/chat/completions"
        messages: list[dict[str, str]] = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        if history_text.strip():
            for line in history_text.splitlines():
                if "|" not in line:
                    continue
                role, content = line.split("|", 1)
                if role not in {"user", "assistant"}:
                    continue
                decoded = bytes(content, "utf-8").decode("unicode_escape")
                messages.append({"role": role, "content": decoded})
        messages.append({"role": "user", "content": prompt})
        payload = json.dumps({"model": model, "messages": messages}).encode("utf-8")
        request = urllib.request.Request(
            chat_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "PebbleOS/0.1.2",
            },
            method="POST",
        )
        insecure_tls = 0
        try:
            try:
                response_context = urllib.request.urlopen(request, timeout=timeout_seconds)
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", None)
                if not isinstance(reason, ssl.SSLCertVerificationError) and "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                    raise
                insecure_tls = 1
                response_context = urllib.request.urlopen(
                    request,
                    timeout=timeout_seconds,
                    context=ssl._create_unverified_context(),
                )
            with response_context as response:
                body = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 200))
                final_url = str(response.geturl())
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
            return {
                "ok": 0,
                "status": 0,
                "content": "",
                "url": chat_url,
                "error": str(exc),
                "insecure_tls": 0,
            }
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            return {
                "ok": 0,
                "status": status,
                "content": "",
                "url": final_url,
                "error": f"invalid JSON response: {exc}",
                "insecure_tls": insecure_tls,
            }
        content = ""
        if isinstance(decoded, dict):
            choices = decoded.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get("message")
                    if isinstance(message, dict):
                        raw_content = message.get("content", "")
                        if isinstance(raw_content, str):
                            content = raw_content
                        elif isinstance(raw_content, list):
                            pieces: list[str] = []
                            for item in raw_content:
                                if isinstance(item, dict) and isinstance(item.get("text"), str):
                                    pieces.append(item["text"])
                            content = "".join(pieces)
        return {
            "ok": 1,
            "status": status,
            "content": content,
            "url": final_url,
            "error": "",
            "insecure_tls": insecure_tls,
        }

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

    def _host_kill_process(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: kill_process() expects 1 integer argument")
        try:
            return self._kill_process(args[0])
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_background_job(self, args: list[object], line_number: int) -> list[str]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: background_job() expects 1 integer argument")
        try:
            return self._background_job(args[0])
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_source_shell_script(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], str):
            raise PebbleError(f"line {line_number}: source_shell_script() expects 1 string argument")
        try:
            self._source_shell_file(args[0])
        except (FileSystemError, PebbleError, ValueError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_fd_open(self, args: list[object], line_number: int) -> int:
        if len(args) != 2 or not isinstance(args[0], str) or not isinstance(args[1], str):
            raise PebbleError(f"line {line_number}: fd_open() expects path and mode strings")
        try:
            return self._open_fd_record(self._normalize_user_path(args[0]), args[1])
        except (FileNotFoundError, FileSystemError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_fd_read(self, args: list[object], line_number: int) -> str:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: fd_read() expects an integer fd")
        record = self._fd_table.get(args[0])
        if record is None:
            raise PebbleError(f"line {line_number}: fd {args[0]} is not open")
        if record.get("kind") == "device":
            device_kind = record.get("device")
            if device_kind == "/dev":
                return ""
            if device_kind == "/dev/null":
                return ""
            if device_kind in {"/dev/stdin", "/dev/tty"}:
                return self._fd_readline(args[0])
            return ""
        if record.get("kind") == "pipe":
            pipe = record.get("pipe")
            if not isinstance(pipe, dict):
                return ""
            buffer = pipe.get("buffer")
            if not isinstance(buffer, list):
                return ""
            return "\n".join(str(item) for item in buffer)
        path = record.get("path")
        if not isinstance(path, Path):
            raise PebbleError(f"line {line_number}: fd {args[0]} is not readable")
        return path.read_text(encoding="utf-8")

    def _host_fd_write(self, args: list[object], line_number: int) -> int:
        if len(args) != 2 or not isinstance(args[0], int) or not isinstance(args[1], str):
            raise PebbleError(f"line {line_number}: fd_write() expects fd and text")
        record = self._fd_table.get(args[0])
        if record is None:
            raise PebbleError(f"line {line_number}: fd {args[0]} is not open")
        self._write_fd_text(args[0], args[1])
        return len(args[1])

    def _host_fd_close(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: fd_close() expects an integer fd")
        if args[0] not in self._fd_table:
            raise PebbleError(f"line {line_number}: fd {args[0]} is not open")
        self._close_fd_record(args[0])
        return 0

    def _host_fd_stat(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: fd_stat() expects an integer fd")
        record = self._fd_table.get(args[0])
        if record is None:
            raise PebbleError(f"line {line_number}: fd {args[0]} is not open")
        if record.get("kind") == "pipe":
            pipe = record.get("pipe")
            size = 0
            if isinstance(pipe, dict):
                buffer = pipe.get("buffer")
                if isinstance(buffer, list):
                    size = len(buffer)
            return {"size": size, "path": "<pipe>", "mode": record["mode"], "kind": "pipe"}
        if record.get("kind") == "device":
            path = str(record.get("path", ""))
            return {"size": 0, "path": path, "mode": record["mode"], "kind": "device"}
        path = record.get("path")
        if not isinstance(path, Path):
            raise PebbleError(f"line {line_number}: fd {args[0]} is not stat-able")
        return {"size": path.stat().st_size, "path": str(path), "mode": record["mode"], "kind": "file"}

    def _host_fd_readdir(self, args: list[object], line_number: int) -> list[str]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: fd_readdir() expects an integer fd")
        record = self._fd_table.get(args[0])
        if record is None:
            raise PebbleError(f"line {line_number}: fd {args[0]} is not open")
        if record.get("kind") == "device":
            if record.get("device") == "/dev":
                return ["null", "stderr", "stdin", "stdout", "tty"]
            raise PebbleError(f"line {line_number}: fd {args[0]} is not a directory")
        path = Path(record["path"])
        if not path.is_dir():
            raise PebbleError(f"line {line_number}: fd {args[0]} is not a directory")
        return sorted(child.name for child in path.iterdir())

    def _host_vm_create_task(self, args: list[object], line_number: int) -> int:
        if len(args) != 2:
            raise PebbleError(f"line {line_number}: vm_create_task() expected 2 arguments but got {len(args)}")
        if not isinstance(args[0], str):
            raise PebbleError(f"line {line_number}: vm_create_task() expects source text as the first argument")
        argv = args[1]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise PebbleError(f"line {line_number}: vm_create_task() expects a list of string arguments")
        runtime = self._make_runtime(consume_output=False)
        task_ref: dict[str, VMTask | None] = {"task": None}
        host_functions = dict(runtime.host_functions)
        host_functions["term_read_key"] = lambda inner_args, inner_line: self._vm_task_read_key_provider(task_ref["task"], None)
        host_functions["term_read_key_timeout"] = (
            lambda inner_args, inner_line: self._vm_task_read_key_provider(
                task_ref["task"],
                (inner_args[0] / 1000.0) if len(inner_args) == 1 and isinstance(inner_args[0], int) else None,
            )
        )
        host_functions["thread_self_host"] = lambda inner_args, inner_line: task_ref["task"].task_id if task_ref["task"] is not None else 0
        interpreter = PebbleBytecodeInterpreter(
            self.fs.root,
            input_provider=lambda prompt="": self._vm_task_input_provider(task_ref["task"], prompt),
            output_consumer=None,
            path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, self.cwd)),
            host_functions=host_functions,
        )
        initial_globals = {
            "ARGV": list(argv),
            "ARGC": len(argv),
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "FS_MODE": self.fs_mode,
            "CWD": self.cwd,
            "ENV": dict(self._runtime_env_override or self.env),
            "PATH": str((self._runtime_env_override or self.env).get("PATH", "")),
        }
        try:
            interpreter.prepare(args[0], initial_globals=initial_globals)
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        task_id = self._next_job_id
        self._next_job_id = self._next_job_id + 1
        task = VMTask(
            task_id=task_id,
            command="vm",
            program="<source>",
            argv=list(argv),
            cwd=self.cwd,
            interpreter=interpreter,
        )
        task_ref["task"] = task
        with self._vm_lock:
            self._vm_tasks[task_id] = task
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
        if task.status in {"halted", "error", "blocked-input", "blocked-tty", "blocked-mutex"}:
            return 0
        try:
            task.status = "running"
            count = self._run_vm_task_steps(task, args[1])
            task.status = "halted" if task.interpreter.vm_state.halted else "ready"
            return count
        except PebbleInputBlocked as exc:
            task.status = "blocked-input"
            task.input_prompt = exc.prompt
            return 0
        except PebbleTTYBlocked as exc:
            task.status = "blocked-tty"
            task.tty_timeout_seconds = exc.timeout_seconds
            return 0
        except PebbleMutexBlocked as exc:
            task.status = "blocked-mutex"
            task.blocked_mutex_id = exc.mutex_id
            return 0
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
        task_ref: dict[str, VMTask | None] = {"task": None}
        host_functions = dict(runtime.host_functions)
        host_functions["term_read_key"] = lambda inner_args, inner_line: self._vm_task_read_key_provider(task_ref["task"], None)
        host_functions["term_read_key_timeout"] = (
            lambda inner_args, inner_line: self._vm_task_read_key_provider(
                task_ref["task"],
                (inner_args[0] / 1000.0) if len(inner_args) == 1 and isinstance(inner_args[0], int) else None,
            )
        )
        host_functions["thread_self_host"] = lambda inner_args, inner_line: task_ref["task"].task_id if task_ref["task"] is not None else 0
        interpreter = PebbleBytecodeInterpreter(
            self.fs.root,
            input_provider=lambda prompt="": self._vm_task_input_provider(task_ref["task"], prompt),
            output_consumer=None,
            path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, self.cwd)),
            host_functions=host_functions,
        )
        interpreter.restore(snapshot)
        task_id = self._next_job_id
        self._next_job_id = self._next_job_id + 1
        task = VMTask(
            task_id=task_id,
            command="vm",
            program="<restored>",
            argv=[],
            cwd=self.cwd,
            interpreter=interpreter,
            outputs_consumed=len(interpreter.output),
            status="halted" if interpreter.vm_state.halted else "ready",
        )
        task_ref["task"] = task
        with self._vm_lock:
            self._vm_tasks[task_id] = task
        return task_id

    def _host_vm_drop_task(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: vm_drop_task() expects one integer task id")
        with self._vm_lock:
            if self._vm_tasks.pop(args[0], None) is None:
                raise PebbleError(f"line {line_number}: vm task {args[0]} does not exist")
        return 0

    def _thread_record(self, task: VMTask) -> dict[str, object]:
        status = task.status
        exit_status = 0
        blocked_on = ""
        if status == "error":
            exit_status = 1
        elif status == "blocked-input":
            blocked_on = "stdin"
        elif status == "blocked-tty":
            blocked_on = "tty"
        elif status == "blocked-mutex":
            blocked_on = "mutex" if task.blocked_mutex_id is None else f"mutex:{task.blocked_mutex_id}"
        return {
            "tid": task.task_id,
            "pid": task.ppid,
            "tgid": task.ppid,
            "state": status,
            "name": task.program,
            "argv": list(task.argv),
            "cwd": task.cwd,
            "exit_status": exit_status,
            "blocked_on": blocked_on,
            "attached": 1 if task.attached else 0,
            "outputs": list(task.interpreter.output),
        }

    def _serialize_pebble_value(self, value: object, line_number: int) -> str:
        if value is None:
            return "None"
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return repr(value)
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                parts.append(self._serialize_pebble_value(item, line_number))
            return "[" + ", ".join(parts) + "]"
        if isinstance(value, dict):
            parts: list[str] = []
            for key, item in value.items():
                parts.append(
                    self._serialize_pebble_value(key, line_number)
                    + ": "
                    + self._serialize_pebble_value(item, line_number)
                )
            return "{" + ", ".join(parts) + "}"
        raise PebbleError(f"line {line_number}: value is not serializable for thread spawn")

    def _create_vm_thread_from_callable(
        self,
        parent_task: VMTask,
        function_value: FunctionValue,
        arg_values: list[object],
        line_number: int,
    ) -> int:
        serialized_args: list[str] = []
        for value in arg_values:
            serialized_args.append(self._serialize_pebble_value(value, line_number))
        source = function_value.name + "(" + ", ".join(serialized_args) + ")"
        task_ref: dict[str, VMTask | None] = {"task": None}
        host_functions = dict(parent_task.interpreter.host_functions)
        host_functions["thread_self_host"] = lambda inner_args, inner_line: task_ref["task"].task_id if task_ref["task"] is not None else 0
        interpreter = PebbleBytecodeInterpreter(
            self.fs.root,
            input_provider=lambda prompt="": self._vm_task_input_provider(task_ref["task"], prompt),
            output_consumer=None,
            path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, parent_task.cwd)),
            host_functions=host_functions,
        )
        interpreter.prepare(source, initial_globals=parent_task.interpreter.globals)
        interpreter.functions = {function_value.name: function_value.function}
        interpreter.module_cache = dict(parent_task.interpreter.module_cache)
        interpreter.module_loading = set(parent_task.interpreter.module_loading)
        task_id = self._next_job_id
        self._next_job_id = self._next_job_id + 1
        task = VMTask(
            task_id=task_id,
            command="thread " + function_value.name,
            program=function_value.name,
            argv=[],
            cwd=parent_task.cwd,
            interpreter=interpreter,
            ppid=parent_task.ppid,
            pgid=parent_task.pgid,
            sid=parent_task.sid,
        )
        task_ref["task"] = task
        with self._vm_lock:
            self._vm_tasks[task_id] = task
        return task_id

    def _host_mutex_create(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: mutex_create_host() expected 0 arguments but got {len(args)}")
        with self._vm_lock:
            mutex_id = self._next_mutex_id
            self._next_mutex_id = self._next_mutex_id + 1
            self._mutexes[mutex_id] = {"id": mutex_id, "owner_tid": None, "waiters": []}
        return mutex_id

    def _current_thread_id(self) -> int:
        task = getattr(self._vm_execution_context, "task", None)
        return 0 if task is None else task.task_id

    def _run_vm_task_steps(self, task: VMTask, step_budget: int) -> int:
        self._vm_execution_context.task = task
        try:
            return task.interpreter.run_steps(step_budget)
        finally:
            self._vm_execution_context.task = None

    def _require_mutex(self, mutex_id: int, line_number: int) -> dict[str, object]:
        with self._vm_lock:
            mutex = self._mutexes.get(mutex_id)
        if mutex is None:
            raise PebbleError(f"line {line_number}: mutex {mutex_id} does not exist")
        return mutex

    def _host_mutex_lock(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: mutex_lock_host() expects one integer mutex id")
        mutex_id = args[0]
        owner_tid = self._current_thread_id()
        with self._vm_lock:
            mutex = self._mutexes.get(mutex_id)
            if mutex is None:
                raise PebbleError(f"line {line_number}: mutex {mutex_id} does not exist")
            current_owner = mutex["owner_tid"]
            if current_owner is None or current_owner == owner_tid:
                mutex["owner_tid"] = owner_tid
                task = self._vm_tasks.get(owner_tid)
                if task is not None:
                    task.blocked_mutex_id = None
                return 0
            waiters = mutex["waiters"]
            if owner_tid != 0 and owner_tid not in waiters:
                waiters.append(owner_tid)
            raise PebbleMutexBlocked(mutex_id)

    def _host_mutex_try_lock(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: mutex_try_lock_host() expects one integer mutex id")
        mutex_id = args[0]
        owner_tid = self._current_thread_id()
        with self._vm_lock:
            mutex = self._mutexes.get(mutex_id)
            if mutex is None:
                raise PebbleError(f"line {line_number}: mutex {mutex_id} does not exist")
            current_owner = mutex["owner_tid"]
            if current_owner is None or current_owner == owner_tid:
                mutex["owner_tid"] = owner_tid
                task = self._vm_tasks.get(owner_tid)
                if task is not None:
                    task.blocked_mutex_id = None
                return 1
        return 0

    def _host_mutex_unlock(self, args: list[object], line_number: int) -> int:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: mutex_unlock_host() expects one integer mutex id")
        mutex_id = args[0]
        owner_tid = self._current_thread_id()
        with self._vm_lock:
            mutex = self._mutexes.get(mutex_id)
            if mutex is None:
                raise PebbleError(f"line {line_number}: mutex {mutex_id} does not exist")
            if mutex["owner_tid"] != owner_tid:
                raise PebbleError(f"line {line_number}: mutex {mutex_id} is not owned by thread {owner_tid}")
            next_owner = None
            waiters = mutex["waiters"]
            while waiters:
                candidate = waiters.pop(0)
                task = self._vm_tasks.get(candidate)
                if task is None or task.status in {"halted", "error"}:
                    continue
                next_owner = candidate
                task.blocked_mutex_id = None
                if task.status == "blocked-mutex":
                    task.status = "ready"
                break
            mutex["owner_tid"] = next_owner
        return 0

    def _host_list_mutex_records(self, args: list[object], line_number: int) -> list[dict[str, object]]:
        if args:
            raise PebbleError(f"line {line_number}: list_mutex_records() expected 0 arguments but got {len(args)}")
        with self._vm_lock:
            mutexes = []
            for mutex_id in sorted(self._mutexes):
                record = self._mutexes[mutex_id]
                mutexes.append(
                    {
                        "id": mutex_id,
                        "owner_tid": record["owner_tid"],
                        "waiters": list(record["waiters"]),
                    }
                )
        return mutexes

    def _host_thread_spawn_source(self, args: list[object], line_number: int) -> int:
        if len(args) != 3 or not isinstance(args[0], str) or not isinstance(args[1], str):
            raise PebbleError(f"line {line_number}: thread_spawn_source_host() expects name, source, argv")
        argv = args[2]
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise PebbleError(f"line {line_number}: thread_spawn_source_host() expects argv as list[str]")
        return self._host_vm_create_task([args[1], argv], line_number)

    def _host_thread_spawn(self, args: list[object], line_number: int) -> int:
        if len(args) != 2 or not isinstance(args[0], FunctionValue) or not isinstance(args[1], list):
            raise PebbleError(f"line {line_number}: thread_spawn_host() expects function value and args list")
        task = getattr(self._vm_execution_context, "task", None)
        if task is None:
            raise PebbleError(f"line {line_number}: thread_spawn() is only available inside VM tasks")
        return self._create_vm_thread_from_callable(task, args[0], args[1], line_number)

    def _host_thread_join(self, args: list[object], line_number: int) -> dict[str, object]:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: thread_join_host() expects one integer tid")
        while True:
            with self._vm_lock:
                task = self._vm_tasks.get(args[0])
            if task is None:
                raise PebbleError(f"line {line_number}: thread {args[0]} does not exist")
            if task.status in {"halted", "error"}:
                return self._thread_record(task)
            self._host_vm_step_task([args[0], 50], line_number)

    def _host_thread_status(self, args: list[object], line_number: int) -> str:
        if len(args) != 1 or not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: thread_status_host() expects one integer tid")
        with self._vm_lock:
            task = self._vm_tasks.get(args[0])
        if task is None:
            raise PebbleError(f"line {line_number}: thread {args[0]} does not exist")
        return task.status

    def _host_thread_self(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: thread_self_host() expected 0 arguments but got {len(args)}")
        return 0

    def _host_thread_yield(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: thread_yield_host() expected 0 arguments but got {len(args)}")
        time.sleep(0)
        return 0

    def _host_list_thread_records(self, args: list[object], line_number: int) -> list[dict[str, object]]:
        if args:
            raise PebbleError(f"line {line_number}: list_thread_records() expected 0 arguments but got {len(args)}")
        with self._vm_lock:
            tasks = list(self._vm_tasks.values())
        return [self._thread_record(task) for task in tasks]

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

    def _read_terminal_key(
        self,
        line_number: int,
        timeout_seconds: float | None,
        escape_interrupts: bool = True,
    ) -> str:
        if not self._terminal_access_allowed():
            return ""
        if not sys.stdin.isatty():
            raise PebbleError(f"line {line_number}: term_read_key() requires an interactive terminal")
        fd = sys.stdin.fileno()
        interrupt_requested = False
        timed_out = False
        result = ""
        managed_raw = self._foreground_terminal_raw == 0
        old_settings = None
        if managed_raw:
            old_settings = termios.tcgetattr(fd)
        try:
            if managed_raw:
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
                        if escape_interrupts:
                            interrupt_requested = True
                        else:
                            ready, _, _ = select.select([sys.stdin], [], [], TTY_ESCAPE_SEQUENCE_GRACE_SECONDS)
                            if not ready:
                                result = "ESC"
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
                                    if third == "A":
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
                                    else:
                                        result = "ESC"
                                else:
                                    result = "ESC"
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
            if managed_raw and old_settings is not None:
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
        self._foreground_terminal_raw = False
        if self._shell_terminal_settings is None or not sys.stdin.isatty():
            return
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._shell_terminal_settings)
        except termios.error:
            return

    def _set_foreground_terminal_raw(self, enabled: bool) -> None:
        if not sys.stdin.isatty() or self._shell_terminal_settings is None:
            self._foreground_terminal_raw = enabled
            return
        fd = sys.stdin.fileno()
        try:
            if enabled:
                if not self._foreground_terminal_raw:
                    tty.setraw(fd)
            else:
                if self._foreground_terminal_raw:
                    termios.tcsetattr(fd, termios.TCSADRAIN, self._shell_terminal_settings)
        except termios.error:
            return
        self._foreground_terminal_raw = enabled

    def _emit_runtime_output(self, text: str) -> None:
        if self._active_stdout_fd is not None:
            self._write_fd_text(self._active_stdout_fd, text + "\n")
            return
        if self._redirect_output_target is not None:
            existing = ""
            if self._redirect_output_mode == "a" and self._redirect_output_target.exists():
                existing = self._redirect_output_target.read_text(encoding="utf-8")
            payload = text + "\n"
            self._redirect_output_target.write_text(existing + payload, encoding="utf-8")
            return
        if sys.stdout.isatty():
            sys.stdout.write(text + "\r\n")
            sys.stdout.flush()
            return
        print(text, flush=True)

    def _emit_runtime_error_output(self, text: str) -> None:
        if self._active_stderr_fd is not None:
            self._write_fd_text(self._active_stderr_fd, text + "\n")
            return
        if self._redirect_error_to_stdout:
            self._emit_runtime_output(text)
            return
        if self._redirect_error_target is not None:
            existing = ""
            if self._redirect_error_mode == "a" and self._redirect_error_target.exists():
                existing = self._redirect_error_target.read_text(encoding="utf-8")
            self._redirect_error_target.write_text(existing + text + "\n", encoding="utf-8")
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

    def _host_term_owner_pgid(self, args: list[object], line_number: int) -> int:
        if args:
            raise PebbleError(f"line {line_number}: term_owner_pgid() expected 0 arguments but got {len(args)}")
        return self._foreground_pgid or 0

    def _host_term_mode(self, args: list[object], line_number: int) -> str:
        if args:
            raise PebbleError(f"line {line_number}: term_mode() expected 0 arguments but got {len(args)}")
        if self._foreground_terminal_raw:
            return "raw"
        return "cooked"

    def _host_term_state(self, args: list[object], line_number: int) -> dict[str, object]:
        if args:
            raise PebbleError(f"line {line_number}: term_state() expected 0 arguments but got {len(args)}")
        return {
            "owner_pgid": self._foreground_pgid or 0,
            "mode": "raw" if self._foreground_terminal_raw else "cooked",
            "interactive": 1 if sys.stdin.isatty() else 0,
            "foreground_raw": 1 if self._foreground_terminal_raw else 0,
            "rows": shutil.get_terminal_size((80, 24)).lines,
            "cols": shutil.get_terminal_size((80, 24)).columns,
        }

    def _host_current_time(self, args: list[object], line_number: int) -> str:
        if args:
            raise PebbleError(f"line {line_number}: current_time() expected 0 arguments but got {len(args)}")
        return datetime.now().strftime("%Y-%m-%d, %H:%M:%S")

    def _host_shell_history(self, args: list[object], line_number: int) -> list[str]:
        if args:
            raise PebbleError(f"line {line_number}: shell_history() expected 0 arguments but got {len(args)}")
        return list(self._command_history)

    def _host_sleep(self, args: list[object], line_number: int) -> int:
        if len(args) != 1:
            raise PebbleError(f"line {line_number}: sleep() expected 1 argument but got {len(args)}")
        if not isinstance(args[0], int):
            raise PebbleError(f"line {line_number}: sleep() expects an integer (milliseconds)")
        time.sleep(args[0] / 1000.0)
        return 0

    def _host_runtime_error(self, args: list[object], line_number: int) -> int:
        message = self._require_string_arg("runtime_error", args, line_number, 1)
        raise PebbleError(f"line {line_number}: {message}")

    def _vm_task_input_provider(self, task: VMTask | None, prompt: str) -> str:
        if task is None:
            raise PebbleError("input() is not available in scheduler-driven bytecode tasks")
        if task.pending_input is not None:
            value = task.pending_input
            task.pending_input = None
            task.input_prompt = None
            return value
        raise PebbleInputBlocked(prompt)

    def _vm_task_read_key_provider(self, task: VMTask | None, timeout_seconds: float | None) -> str:
        if task is None:
            return ""
        if task.pending_keys:
            value = task.pending_keys.pop(0)
            task.tty_timeout_seconds = None
            return value
        raise PebbleTTYBlocked(timeout_seconds)

    def _collect_foreground_terminal_keys(self, task: VMTask, timeout_seconds: float | None) -> list[str]:
        first = self._read_terminal_byte(timeout_seconds)
        if first != "":
            task.pending_tty_bytes.append(first)
            if first == "\x1b" and len(task.pending_tty_bytes) == 1:
                task.pending_escape_started_at = time_module.monotonic()
            self._drain_ready_terminal_bytes(task)
        elif task.pending_tty_bytes and task.pending_tty_bytes[0] == "\x1b":
            if task.pending_escape_started_at is None:
                task.pending_escape_started_at = time_module.monotonic()
            elif time_module.monotonic() - task.pending_escape_started_at >= TTY_ESCAPE_SEQUENCE_GRACE_SECONDS:
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                return ["ESC"]
        keys = self._parse_pending_tty_events(task)
        return keys

    def _drain_ready_terminal_bytes(self, task: VMTask) -> None:
        while True:
            extra = self._read_terminal_byte(0.0)
            if extra == "":
                return
            task.pending_tty_bytes.append(extra)
            if task.pending_tty_bytes and task.pending_tty_bytes[0] == "\x1b":
                task.pending_escape_started_at = time_module.monotonic()

    def _parse_pending_tty_events(self, task: VMTask) -> list[str]:
        events: list[str] = []
        while task.pending_tty_bytes:
            first = task.pending_tty_bytes[0]
            if first == "\x03":
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                raise KeyboardInterrupt
            if first == "\x1a":
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                self._detach_requested.set()
                events.append("")
                continue
            if first in {"\r", "\n"}:
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                events.append("ENTER")
                continue
            if first == "\x7f":
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                events.append("BACKSPACE")
                continue
            if first == "\x18":
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                events.append("^X")
                continue
            if first == "\x0f":
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                events.append("^O")
                continue
            if first != "\x1b":
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                events.append(first)
                continue
            if len(task.pending_tty_bytes) == 1:
                return events
            second = task.pending_tty_bytes[1]
            if second == "O":
                if len(task.pending_tty_bytes) < 3:
                    return events
                third = task.pending_tty_bytes[2]
                del task.pending_tty_bytes[:3]
                task.pending_escape_started_at = None
                if third == "P":
                    self._detach_requested.set()
                    events.append("")
                else:
                    events.append("ESC")
                continue
            if second != "[":
                task.pending_tty_bytes.pop(0)
                task.pending_escape_started_at = None
                events.append("ESC")
                continue
            if len(task.pending_tty_bytes) < 3:
                return events
            third = task.pending_tty_bytes[2]
            if third == "A":
                del task.pending_tty_bytes[:3]
                task.pending_escape_started_at = None
                events.append("UP")
                continue
            if third == "B":
                del task.pending_tty_bytes[:3]
                task.pending_escape_started_at = None
                events.append("DOWN")
                continue
            if third == "C":
                del task.pending_tty_bytes[:3]
                task.pending_escape_started_at = None
                events.append("RIGHT")
                continue
            if third == "D":
                del task.pending_tty_bytes[:3]
                task.pending_escape_started_at = None
                events.append("LEFT")
                continue
            if third == "H":
                del task.pending_tty_bytes[:3]
                task.pending_escape_started_at = None
                events.append("HOME")
                continue
            if third == "F":
                del task.pending_tty_bytes[:3]
                task.pending_escape_started_at = None
                events.append("END")
                continue
            if third in {"1", "3", "4", "5", "6", "7", "8"}:
                if len(task.pending_tty_bytes) < 4:
                    return events
                fourth = task.pending_tty_bytes[3]
                del task.pending_tty_bytes[:4]
                task.pending_escape_started_at = None
                if fourth == "~":
                    if third in {"1", "7"}:
                        events.append("HOME")
                    elif third == "3":
                        events.append("DELETE")
                    elif third in {"4", "8"}:
                        events.append("END")
                    elif third == "5":
                        events.append("PAGEUP")
                    elif third == "6":
                        events.append("PAGEDOWN")
                else:
                    events.append("ESC")
                continue
            task.pending_tty_bytes.pop(0)
            task.pending_escape_started_at = None
            events.append("ESC")
        return events

    def _read_terminal_byte(self, timeout_seconds: float | None) -> str:
        if not self._terminal_access_allowed():
            return ""
        if not sys.stdin.isatty():
            return ""
        if timeout_seconds is not None:
            ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
            if not ready:
                return ""
        return sys.stdin.read(1)

    def _create_vm_task(self, name: str, extra_args: list[str], command_name: str, cwd: str | None = None) -> int:
        cwd_value = self.cwd if cwd is None else cwd
        program = self._normalize_user_path(name, cwd_value)
        runtime_source = self.fs.read_file("system/runtime.peb")
        source = runtime_source + "\n" + self.fs.read_file(program.lstrip("/"))
        runtime = self._make_runtime(consume_output=False)
        task_ref: dict[str, VMTask | None] = {"task": None}
        host_functions = dict(runtime.host_functions)
        host_functions["term_read_key"] = lambda inner_args, inner_line: self._vm_task_read_key_provider(task_ref["task"], None)
        host_functions["term_read_key_timeout"] = (
            lambda inner_args, inner_line: self._vm_task_read_key_provider(
                task_ref["task"],
                (inner_args[0] / 1000.0) if len(inner_args) == 1 and isinstance(inner_args[0], int) else None,
            )
        )
        interpreter = PebbleBytecodeInterpreter(
            self.fs.root,
            input_provider=lambda prompt="": self._vm_task_input_provider(task_ref["task"], prompt),
            output_consumer=None,
            path_resolver=lambda path: self._logical_path_to_host(self._normalize_user_path(path, cwd_value)),
            host_functions=host_functions,
        )
        initial_globals = {
            "ARGV": list(extra_args),
            "ARGC": len(extra_args),
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "FS_MODE": self.fs_mode,
            "CWD": cwd_value,
            "ENV": dict(self._runtime_env_override or self.env),
            "PATH": str((self._runtime_env_override or self.env).get("PATH", "")),
        }
        if program in {"/system/nano.peb", "/nano.peb", "system/nano.peb", "nano.peb"} and extra_args:
            target_file = extra_args[0]
            try:
                file_content = self.fs.read_file(target_file.lstrip("/"))
            except FileSystemError:
                file_content = ""
            initial_globals["TARGET_FILE"] = target_file
            initial_globals["FILE_CONTENT"] = file_content
        interpreter.prepare(source, initial_globals=initial_globals)
        task = VMTask(
            task_id=0,
            command=command_name + " " + program,
            program=program,
            argv=list(extra_args),
            cwd=cwd_value,
            interpreter=interpreter,
            ppid=1,
            pgid=0,
            sid=1,
        )
        task_ref["task"] = task
        with self._vm_lock:
            task_id = self._next_job_id
            self._next_job_id = self._next_job_id + 1
            task.task_id = task_id
            task.pgid = task_id
            self._vm_tasks[task_id] = task
        return task_id

    def _vm_scheduler_loop(self) -> None:
        while True:
            time.sleep(0.01)
            with self._vm_lock:
                tasks = [self._vm_tasks[key] for key in sorted(self._vm_tasks)]
            for task in tasks:
                if task.status in {"halted", "error", "blocked-input", "blocked-tty", "blocked-mutex"} or task.attached:
                    continue
                try:
                    task.status = "running"
                    self._run_vm_task_steps(task, BACKGROUND_VM_STEP_BUDGET)
                    task.status = "halted" if task.interpreter.vm_state.halted else "ready"
                    self._notify_sigchld_for_task(task)
                except PebbleInputBlocked as exc:
                    task.status = "blocked-input"
                    task.input_prompt = exc.prompt
                except PebbleTTYBlocked as exc:
                    task.status = "blocked-tty"
                    task.tty_timeout_seconds = exc.timeout_seconds
                except PebbleMutexBlocked as exc:
                    task.status = "blocked-mutex"
                    task.blocked_mutex_id = exc.mutex_id
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
            if task.status == "blocked-input":
                self._set_foreground_terminal_raw(False)
                try:
                    line = input(task.input_prompt or "")
                except EOFError:
                    line = ""
                except KeyboardInterrupt:
                    self._emit_signal_event("SIGINT", task.task_id, task.pgid, "vm", "foreground")
                    task.attached = False
                    task.status = "error"
                    task.error = "[system] program interrupted"
                    if self._foreground_pgid == task.pgid:
                        self._foreground_pgid = None
                    self._terminal_owner_thread_id = None
                    with self._vm_lock:
                        self._vm_tasks.pop(task_id, None)
                    print("^C")
                    print("[system] program interrupted", flush=True)
                    return False
                task.pending_input = line
                task.input_prompt = None
                task.status = "ready"
            elif task.status == "blocked-tty":
                self._set_foreground_terminal_raw(True)
                try:
                    keys = self._collect_foreground_terminal_keys(task, task.tty_timeout_seconds)
                except KeyboardInterrupt:
                    self._emit_signal_event("SIGINT", task.task_id, task.pgid, "vm", "foreground")
                    task.attached = False
                    task.status = "error"
                    task.error = "[system] program interrupted"
                    if self._foreground_pgid == task.pgid:
                        self._foreground_pgid = None
                    self._terminal_owner_thread_id = None
                    with self._vm_lock:
                        self._vm_tasks.pop(task_id, None)
                    print("^C")
                    print("[system] program interrupted", flush=True)
                    return False
                if len(keys) == 0:
                    if task.tty_timeout_seconds is not None and len(task.pending_tty_bytes) == 0:
                        task.pending_keys = [""]
                        task.tty_timeout_seconds = None
                        task.status = "ready"
                        continue
                    task.status = "blocked-tty"
                    continue
                task.pending_keys = list(keys)
                task.tty_timeout_seconds = None
                task.status = "ready"
            elif task.status not in {"halted", "error"}:
                try:
                    task.status = "running"
                    step_budget = FOREGROUND_VM_STEP_BUDGET
                    if task.pending_keys:
                        step_budget = FOREGROUND_VM_KEY_PRIORITY_STEP_BUDGET
                    self._run_vm_task_steps(task, step_budget)
                    task.status = "halted" if task.interpreter.vm_state.halted else "ready"
                    self._notify_sigchld_for_task(task)
                except PebbleInputBlocked as exc:
                    task.status = "blocked-input"
                    task.input_prompt = exc.prompt
                except PebbleTTYBlocked as exc:
                    task.status = "blocked-tty"
                    task.tty_timeout_seconds = exc.timeout_seconds
                except PebbleMutexBlocked as exc:
                    task.status = "blocked-mutex"
                    task.blocked_mutex_id = exc.mutex_id
                except PebbleError as exc:
                    task.status = "error"
                    task.error = str(exc)
                    self._notify_sigchld_for_task(task)
            self._emit_new_vm_output(task)
            if task.status in {"halted", "error"}:
                self._set_foreground_terminal_raw(False)
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
                self._set_foreground_terminal_raw(False)
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
                self._set_foreground_terminal_raw(False)
                self._emit_signal_event("SIGTSTP", task.task_id, task.pgid, "vm", "foreground")
                task.attached = False
                if self._foreground_pgid == task.pgid:
                    self._foreground_pgid = None
                self._terminal_owner_thread_id = None
                return True
            if task.pending_keys:
                continue
            time.sleep(FOREGROUND_VM_IDLE_SLEEP_SECONDS)

    def _emit_new_vm_output(self, task: VMTask) -> None:
        while task.outputs_consumed < len(task.interpreter.output):
            self._emit_runtime_output(task.interpreter.output[task.outputs_consumed])
            task.outputs_consumed = task.outputs_consumed + 1

    def _run_program(self, name: str, extra_args: list[str], exec_mode: str = "interp", cwd: str | None = None) -> None:
        if not sys.stdin.isatty():
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
            "ENV": dict(self._runtime_env_override or self.env),
            "PATH": str((self._runtime_env_override or self.env).get("PATH", "")),
        }
        provider = self._runtime_input if input_provider is None else input_provider
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
        interactive_program = 0
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
                    if job.status == "terminated":
                        self._notify_sigchld_for_job(job)
                        return
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

    def _kill_process(self, pid: int) -> dict[str, object]:
        with self._vm_lock:
            vm_task = self._vm_tasks.get(pid)
            if vm_task is not None:
                self._emit_signal_event("SIGTERM", vm_task.task_id, vm_task.pgid, "vm", "terminated")
                self._vm_tasks.pop(pid, None)
                return {"pid": pid, "state": "terminated", "kind": "vm", "exit_status": 143}
        with self._jobs_lock:
            job = self._jobs.get(pid)
            if job is None:
                raise PebbleError(f"process {pid} does not exist")
            job.status = "terminated"
            job.error = "terminated by SIGTERM"
            self._emit_signal_event("SIGTERM", job.job_id, job.pgid, "host-job", "terminated")
            return {"pid": pid, "state": "terminated", "kind": "host-job", "exit_status": 143}

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

    def _background_job(self, job_id: int) -> list[str]:
        with self._vm_lock:
            task = self._vm_tasks.get(job_id)
            if task is not None:
                task.attached = False
                return [f"[{job_id}] {task.program}"]
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return [f"[{job_id}] {job.program}"]
        raise PebbleError(f"job {job_id} does not exist")

    def _ensure_phase4_layout(self) -> None:
        defaults = {
            "etc/profile": "# Pebble OS login profile\nexport PATH=/system/bin:/system/sbin:/bin\n",
            "etc/passwd": "root:x:0:0:root:/root:/bin/sh\n",
            "etc/group": "root:x:0:\n",
            "etc/fstab": "# device mountpoint fstype options\n",
        }
        for name, content in defaults.items():
            path = self.fs.resolve_path(name)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

    def _load_shell_profile(self) -> None:
        try:
            self._source_shell_file("/etc/profile")
        except (FileSystemError, PebbleError, ValueError):
            pass
        try:
            self._source_shell_file("/etc/profile.local")
        except (FileSystemError, PebbleError, ValueError):
            return

    def _source_shell_file(self, name: str) -> None:
        logical = self._normalize_user_path(name)
        path = self._logical_path_to_host(logical)
        if not path.exists():
            raise FileSystemError(f"file '{logical}' does not exist")
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            self.onecmd(line)

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

    def _parse_redirections(
        self, line: str
    ) -> tuple[str, str | None, str | None, str, str | None, str, bool] | None:
        try:
            parts = self._shell_split(line)
        except ValueError:
            return None
        if ">" not in parts and ">>" not in parts and "<" not in parts and "2>" not in parts and "2>&1" not in parts:
            return None
        cleaned: list[str] = []
        stdin_path: str | None = None
        stdout_path: str | None = None
        stdout_mode = "w"
        stderr_path: str | None = None
        stderr_mode = "w"
        stderr_to_stdout = False
        i = 0
        while i < len(parts):
            token = parts[i]
            if token == "2>&1":
                stderr_to_stdout = True
                i = i + 1
                continue
            if token in {">", ">>", "<", "2>"}:
                if i + 1 >= len(parts):
                    raise ValueError(f"missing redirection target for {token}")
                target = parts[i + 1]
                if token == "<":
                    stdin_path = target
                elif token == "2>":
                    stderr_path = target
                    stderr_mode = "w"
                else:
                    stdout_path = target
                    stdout_mode = "a" if token == ">>" else "w"
                i = i + 2
                continue
            cleaned.append(token)
            i = i + 1
        return " ".join(cleaned), stdin_path, stdout_path, stdout_mode, stderr_path, stderr_mode, stderr_to_stdout

    def _parse_pipeline(self, line: str) -> list[str] | None:
        try:
            parts = self._shell_split(line)
        except ValueError:
            return None
        if "|" not in parts:
            return None
        segments: list[str] = []
        current: list[str] = []
        i = 0
        while i < len(parts):
            if parts[i] == "|":
                segment = " ".join(current).strip()
                if not segment:
                    raise ValueError("pipe requires commands on both sides")
                segments.append(segment)
                current = []
            else:
                current.append(parts[i])
            i = i + 1
        tail = " ".join(current).strip()
        if not tail:
            raise ValueError("pipe requires commands on both sides")
        segments.append(tail)
        return segments

    def _shell_split(self, line: str) -> list[str]:
        lexer = shlex.shlex(line, posix=True, punctuation_chars="|<>&")
        lexer.whitespace_split = True
        raw = list(lexer)
        tokens: list[str] = []
        i = 0
        while i < len(raw):
            token = raw[i]
            if token == ">>":
                tokens.append(">>")
                i = i + 1
                continue
            if token == "2" and i + 1 < len(raw) and raw[i + 1] == ">":
                if i + 2 < len(raw) and raw[i + 2] == "&1":
                    tokens.append("2>&1")
                    i = i + 3
                    continue
                tokens.append("2>")
                i = i + 2
                continue
            if token == "2" and i + 2 < len(raw) and raw[i + 1] == ">&" and raw[i + 2] == "1":
                tokens.append("2>&1")
                i = i + 3
                continue
            if token == "2>" and i + 1 < len(raw) and raw[i + 1] == "&1":
                tokens.append("2>&1")
                i = i + 2
                continue
            if token == ">" and i + 1 < len(raw) and raw[i + 1] == ">":
                tokens.append(">>")
                i = i + 2
                continue
            if token.endswith("2") and i + 1 < len(raw) and raw[i + 1] == ">" and token[:-1] != "":
                tokens.append(token[:-1])
                tokens.append("2>")
                i = i + 2
                continue
            if token.endswith(">") and token != ">":
                prefix = token[:-1]
                if prefix != "":
                    tokens.append(prefix)
                tokens.append(">")
                i = i + 1
                continue
            if token.endswith("<") and token != "<":
                prefix = token[:-1]
                if prefix != "":
                    tokens.append(prefix)
                tokens.append("<")
                i = i + 1
                continue
            if token.endswith("|") and token != "|":
                prefix = token[:-1]
                if prefix != "":
                    tokens.append(prefix)
                tokens.append("|")
                i = i + 1
                continue
            tokens.append(token)
            i = i + 1
        return tokens


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "pebble_disk"
    PebbleShell(root).cmdloop()


def build_shell(fs_mode: str = "hostfs") -> PebbleShell:
    root = Path(__file__).resolve().parent.parent / "pebble_disk"
    return PebbleShell(root, fs_mode=fs_mode)

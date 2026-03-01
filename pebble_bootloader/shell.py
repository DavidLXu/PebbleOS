from __future__ import annotations

import cmd
import select
import shutil
import shlex
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path

from pebble_bootloader.fs import FileSystemError, FlatFileSystem
from pebble_bootloader.lang import PebbleBytecodeInterpreter, PebbleError, PebbleInterpreter


VALID_FS_MODES = {"hostfs", "vfs-import", "vfs-persistent"}


class PebbleShell(cmd.Cmd):
    intro = (
        "Pebble OS shell\n"
        "Flat filesystem: one folder, no subdirectories\n"
        "Type 'help' for commands."
    )
    prompt = "pebble-os> "

    def __init__(self, root: Path, fs_mode: str = "hostfs") -> None:
        super().__init__()
        if fs_mode not in VALID_FS_MODES:
            raise ValueError(f"invalid fs mode '{fs_mode}'")
        self.fs = FlatFileSystem(root)
        self.fs_mode = fs_mode
        self.fs.mount("system", Path(__file__).resolve().parent.parent / "pebble_system")
        self._refresh_shell_state()

    def do_help(self, arg: str) -> None:
        self._dispatch_runtime_command("help", arg)

    def do_exit(self, arg: str) -> bool:
        return self._dispatch_runtime_command("exit", arg)

    def do_EOF(self, arg: str) -> bool:
        print()
        return self.do_exit(arg)

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
            return result == "__exit__"
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
        return PebbleInterpreter(
            self.fs.root,
            input_provider=input,
            output_consumer=print if consume_output else None,
            path_resolver=self.fs.resolve_path,
            host_functions={
                "raw_list_files": self._host_list_files,
                "raw_file_exists": self._host_file_exists,
                "raw_create_file": self._host_create_file,
                "raw_modify_file": self._host_modify_file,
                "raw_delete_file": self._host_delete_file,
                "raw_file_time": self._host_file_time,
                "raw_read_file": self._host_raw_read_file,
                "raw_write_file": self._host_raw_write_file,
                "list_files": self._host_list_files,
                "file_time": self._host_file_time,
                "file_exists": self._host_file_exists,
                "create_file": self._host_create_file,
                "modify_file": self._host_modify_file,
                "delete_file": self._host_delete_file,
                "capture_text": self._host_capture_text,
                "run_program": self._host_run_program,
                "exec_program": self._host_exec_program,
                "filesystem_file_count": self._host_filesystem_file_count,
                "filesystem_total_bytes": self._host_filesystem_total_bytes,
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
            return self.fs.file_time(name)
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_raw_read_file(self, args: list[object], line_number: int) -> str:
        name = self._require_string_arg("raw_read_file", args, line_number, 1)
        try:
            return self.fs.read_file(name)
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc

    def _host_raw_write_file(self, args: list[object], line_number: int) -> str:
        name, text = self._require_name_and_text("raw_write_file", args, line_number)
        try:
            path = self.fs.resolve_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return text

    def _host_file_exists(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("file_exists", args, line_number, 1)
        try:
            self.fs.read_file(name)
        except FileSystemError:
            return 0
        return 1

    def _host_create_file(self, args: list[object], line_number: int) -> int:
        name, text = self._require_name_and_text("create_file", args, line_number)
        try:
            self.fs.create_file(name, text)
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_modify_file(self, args: list[object], line_number: int) -> int:
        name, text = self._require_name_and_text("modify_file", args, line_number)
        try:
            self.fs.modify_file(name, text)
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

    def _host_delete_file(self, args: list[object], line_number: int) -> int:
        name = self._require_string_arg("delete_file", args, line_number, 1)
        try:
            self.fs.delete_file(name)
        except FileSystemError as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

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
            total = total + self.fs.resolve_path(name).stat().st_size
        return total

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
        if not sys.stdin.isatty():
            raise PebbleError(f"line {line_number}: term_read_key() requires an interactive terminal")
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            if timeout_seconds is not None:
                ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
                if not ready:
                    return ""
            first = sys.stdin.read(1)
            if first == "\x1b":
                second = sys.stdin.read(1)
                if second == "[":
                    third = sys.stdin.read(1)
                    if third == "A":
                        return "UP"
                    if third == "B":
                        return "DOWN"
                    if third == "C":
                        return "RIGHT"
                    if third == "D":
                        return "LEFT"
                    if third == "H":
                        return "HOME"
                    if third == "F":
                        return "END"
                    if third in {"1", "3", "4", "5", "6", "7", "8"}:
                        fourth = sys.stdin.read(1)
                        if fourth == "~":
                            if third in {"1", "7"}:
                                return "HOME"
                            if third == "3":
                                return "DELETE"
                            if third in {"4", "8"}:
                                return "END"
                            if third == "5":
                                return "PAGEUP"
                            if third == "6":
                                return "PAGEDOWN"
                return "ESC"
            if first in {"\r", "\n"}:
                return "ENTER"
            if first == "\x7f":
                return "BACKSPACE"
            if first == "\x18":
                return "^X"
            if first == "\x0f":
                return "^O"
            return first
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

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
        return datetime.now().strftime("%Y-%m-%d, %H:%M")

    def _host_runtime_error(self, args: list[object], line_number: int) -> int:
        message = self._require_string_arg("runtime_error", args, line_number, 1)
        raise PebbleError(f"line {line_number}: {message}")

    def _run_program(self, name: str, extra_args: list[str], exec_mode: str = "interp") -> None:
        runtime_source = self.fs.read_file("system/runtime.peb")
        source = runtime_source + "\n" + self.fs.read_file(name)
        initial_globals = {
            "ARGV": extra_args,
            "ARGC": len(extra_args),
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
            "FS_MODE": self.fs_mode,
        }
        if exec_mode == "bytecode":
            interpreter = PebbleBytecodeInterpreter(
                self.fs.root,
                input_provider=input,
                output_consumer=None,
                path_resolver=self.fs.resolve_path,
                host_functions=self._make_runtime(consume_output=False).host_functions,
            )
        else:
            interpreter = self._make_runtime(consume_output=False)
        interactive_program = name in {"nano.peb", "system/nano.peb"}
        if interactive_program and extra_args:
            target_file = extra_args[0]
            self.fs.resolve_path(target_file)
            try:
                file_content = self.fs.read_file(target_file)
            except FileSystemError:
                file_content = ""
            initial_globals["TARGET_FILE"] = target_file
            initial_globals["FILE_CONTENT"] = file_content

        output = interpreter.execute(source, initial_globals=initial_globals)
        if not output:
            if not interactive_program:
                print("(no output)")
            return
        for line in output:
            print(line)

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

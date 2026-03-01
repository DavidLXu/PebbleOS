from __future__ import annotations

import cmd
import shutil
import shlex
import sys
import termios
import tty
from pathlib import Path

from pebble_bootloader.fs import FileSystemError, FlatFileSystem
from pebble_bootloader.lang import PebbleError, PebbleInterpreter


class PebbleShell(cmd.Cmd):
    intro = (
        "Pebble OS shell\n"
        "Flat filesystem: one folder, no subdirectories\n"
        "Type 'help' for commands."
    )
    prompt = "pebble-os> "

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.fs = FlatFileSystem(root)
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
        source = self.fs.read_file("system/shell.peb")
        runtime = self._make_runtime(consume_output=consume_output)
        initial_globals: dict[str, object] = {}
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
                "list_files": self._host_list_files,
                "file_exists": self._host_file_exists,
                "create_file": self._host_create_file,
                "modify_file": self._host_modify_file,
                "delete_file": self._host_delete_file,
                "capture_text": self._host_capture_text,
                "run_program": self._host_run_program,
                "term_write": self._host_term_write,
                "term_flush": self._host_term_flush,
                "term_clear": self._host_term_clear,
                "term_move": self._host_term_move,
                "term_hide_cursor": self._host_term_hide_cursor,
                "term_show_cursor": self._host_term_show_cursor,
                "term_read_key": self._host_term_read_key,
                "term_rows": self._host_term_rows,
                "term_cols": self._host_term_cols,
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
            self._run_program(args[0], argv)
        except (FileSystemError, PebbleError) as exc:
            raise PebbleError(f"line {line_number}: {exc}") from exc
        return 0

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
        if not sys.stdin.isatty():
            raise PebbleError(f"line {line_number}: term_read_key() requires an interactive terminal")
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
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

    def _run_program(self, name: str, extra_args: list[str]) -> None:
        runtime_source = self.fs.read_file("system/runtime.peb")
        source = runtime_source + "\n" + self.fs.read_file(name)
        initial_globals = {
            "ARGV": extra_args,
            "ARGC": len(extra_args),
            "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
        }
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


def build_shell() -> PebbleShell:
    root = Path(__file__).resolve().parent.parent / "pebble_disk"
    return PebbleShell(root)

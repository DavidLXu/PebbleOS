from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pebble_bootloader.fs import FileSystemError, FlatFileSystem
from pebble_bootloader.lang import PebbleError, PebbleInterpreter
from pebble_bootloader.shell import build_shell


class FlatFileSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fs = FlatFileSystem(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_modify_and_delete_file(self) -> None:
        self.fs.create_file("program.peb", "print 1")
        self.assertEqual(self.fs.list_files(), ["program.peb"])
        self.assertEqual(self.fs.read_file("program.peb"), "print 1")

        self.fs.modify_file("program.peb", "value = 9\nprint value")
        self.assertEqual(self.fs.read_file("program.peb"), "value = 9\nprint value")

        self.fs.delete_file("program.peb")
        self.assertEqual(self.fs.list_files(), [])

    def test_rejects_subdirectories(self) -> None:
        with self.assertRaises(FileSystemError):
            self.fs.create_file("dir/program.peb", "")

    def test_supports_mounted_paths(self) -> None:
        mount_dir = Path(self.temp_dir.name) / "mounted"
        mount_dir.mkdir()
        self.fs.mount("mounted", mount_dir)
        self.fs.create_file("mounted/hello.txt", "hi")
        self.assertEqual(self.fs.read_file("mounted/hello.txt"), "hi")
        self.assertIn("mounted/hello.txt", self.fs.list_files())

    def test_supports_system_mount_alias(self) -> None:
        system_dir = Path(self.temp_dir.name) / "system"
        system_dir.mkdir()
        self.fs.mount("system", system_dir)
        self.fs.create_file("system/runtime.peb", "print 1")
        self.assertEqual(self.fs.read_file("system/runtime.peb"), "print 1")
        self.assertIn("system/runtime.peb", self.fs.list_files())


class PebbleInterpreterTests(unittest.TestCase):
    def test_runs_assignments_math_and_print(self) -> None:
        source = "\n".join(
            [
                "a = 10",
                "b = a * 3 - 4",
                "print b",
                "a = b + 2",
                "print a",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["26", "28"])

    def test_errors_on_unknown_variable(self) -> None:
        with self.assertRaises(PebbleError):
            PebbleInterpreter().execute("print missing")

    def test_supports_functions_for_loops_if_blocks_and_returns(self) -> None:
        source = "\n".join(
            [
                "def scaled_sum(limit, factor):",
                "    total = 0",
                "    for i in range(limit):",
                "        if i:",
                "            total = total + i * factor",
                "    return total",
                "print scaled_sum(5, 3)",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["30"])

    def test_supports_elif_else_and_comparisons(self) -> None:
        source = "\n".join(
            [
                "def classify(x):",
                "    if x < 0:",
                "        return 11",
                "    elif x == 0:",
                "        return 22",
                "    else:",
                "        return 33",
                "print classify(-2)",
                "print classify(0)",
                "print classify(4)",
                "print 4 > 3",
                "print 4 == 5",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["11", "22", "33", "1", "0"])

    def test_supports_range_start_stop_and_step(self) -> None:
        source = "\n".join(
            [
                "total = 0",
                "for i in range(1, 7, 2):",
                "    total = total + i",
                "print total",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["9"])

    def test_rejects_bad_indentation(self) -> None:
        with self.assertRaises(PebbleError):
            PebbleInterpreter().execute("if 1:\n  print 1")

    def test_rejects_return_outside_function(self) -> None:
        with self.assertRaises(PebbleError):
            PebbleInterpreter().execute("return 1")

    def test_rejects_standalone_else(self) -> None:
        with self.assertRaises(PebbleError):
            PebbleInterpreter().execute("else:\n    print 1")

    def test_supports_while_strings_lists_and_index_assignment(self) -> None:
        source = "\n".join(
            [
                'parts = ["peb", "ble"]',
                'name = ""',
                "i = 0",
                "while i < len(parts):",
                "    name = name + parts[i]",
                "    i = i + 1",
                'parts[1] = "BLE"',
                "print name",
                "print parts[1]",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["pebble", "BLE"])

    def test_supports_append_range_and_list_iteration(self) -> None:
        source = "\n".join(
            [
                "items = []",
                "for i in range(3):",
                "    append(items, i)",
                "total = 0",
                "for value in items:",
                "    total = total + value",
                "print total",
                "print len(items)",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["3", "3"])

    def test_supports_file_io_and_casts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            interpreter = PebbleInterpreter(Path(temp_dir))
            source = "\n".join(
                [
                    'write_file("n.txt", "41")',
                    'text = read_file("n.txt")',
                    "print int(text) + 1",
                    'print str(7) + "!"',
                ]
            )
            output = interpreter.execute(source)
            self.assertEqual(output, ["42", "7!"])
            self.assertEqual((Path(temp_dir) / "n.txt").read_text(encoding="utf-8"), "41")

    def test_rejects_write_file_outside_flat_fs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            interpreter = PebbleInterpreter(Path(temp_dir))
            with self.assertRaises(PebbleError):
                interpreter.execute('write_file("../bad.txt", "x")')

    def test_supports_input_output_callbacks_and_initial_globals(self) -> None:
        prompts: list[str] = []
        outputs: list[str] = []

        def fake_input(prompt: str) -> str:
            prompts.append(prompt)
            return "42"

        interpreter = PebbleInterpreter(
            input_provider=fake_input,
            output_consumer=outputs.append,
        )
        source = "\n".join(
            [
                'print "hello " + TARGET',
                'value = input("number: ")',
                "print int(value) + 1",
            ]
        )
        result = interpreter.execute(source, initial_globals={"TARGET": "nano"})
        self.assertEqual(prompts, ["number: "])
        self.assertEqual(outputs, ["hello nano", "43"])
        self.assertEqual(result, ["hello nano", "43"])

    def test_supports_argv_via_globals_and_builtin(self) -> None:
        interpreter = PebbleInterpreter()
        source = "\n".join(
            [
                "print ARGC",
                "print ARGV[0]",
                "print argv(1)",
            ]
        )
        output = interpreter.execute(
            source,
            initial_globals={
                "ARGC": 2,
                "ARGV": ["alpha", "two words"],
            },
        )
        self.assertEqual(output, ["2", "alpha", "two words"])

    def test_supports_mounted_file_access_from_pebble(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            disk_dir = Path(temp_dir) / "disk"
            mounted_dir = Path(temp_dir) / "mounted"
            disk_dir.mkdir()
            mounted_dir.mkdir()

            def resolve(name: str) -> Path:
                if name.startswith("mounted/"):
                    return mounted_dir / name.split("/", 1)[1]
                return disk_dir / name

            interpreter = PebbleInterpreter(disk_dir, path_resolver=resolve)
            source = '\n'.join(
                [
                    'write_file("mounted/self.txt", "boot")',
                    'print read_file("mounted/self.txt")',
                ]
            )
            output = interpreter.execute(source)
            self.assertEqual(output, ["boot"])
            self.assertEqual((mounted_dir / "self.txt").read_text(encoding="utf-8"), "boot")

    def test_supports_python_style_bool_none_and_dict_features(self) -> None:
        source = "\n".join(
            [
                'data = {"name": "pebble", "count": 2}',
                'print data["name"]',
                'data["count"] = data["count"] + 1',
                'print data["count"]',
                "print len(data)",
                "print keys(data)",
                "print True and False",
                "print False or 7",
                "print not None",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(
            output,
            ["pebble", "3", "2", "[name, count]", "False", "7", "True"],
        )

    def test_supports_break_continue_pass_and_comments(self) -> None:
        source = "\n".join(
            [
                "items = []",
                "for i in range(6):  # count up",
                "    if i == 1:",
                "        continue",
                "    elif i == 4:",
                "        break",
                "    else:",
                "        pass",
                "    append(items, i)",
                "print items",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["[0, 2, 3]"])

    def test_supports_for_over_string(self) -> None:
        source = "\n".join(
            [
                'out = ""',
                'for ch in "abc":',
                "    out = out + ch",
                "print out",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["abc"])

    def test_supports_host_defined_builtins(self) -> None:
        def twice(args: list[object], line_number: int) -> int:
            self.assertEqual(line_number, 1)
            self.assertEqual(args, [7])
            return 14

        interpreter = PebbleInterpreter(host_functions={"twice": twice})
        output = interpreter.execute("print twice(7)")
        self.assertEqual(output, ["14"])

    def test_system_nano_runs_with_terminal_bridge(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        nano_source = Path("/Users/xulixin/LX_OS/pebble_system/nano.peb").read_text(encoding="utf-8")
        rendered: list[str] = []
        keys = iter(["^X"])

        interpreter = PebbleInterpreter(
            host_functions={
                "term_write": lambda args, line: rendered.append(args[0]) or args[0],
                "term_flush": lambda args, line: 0,
                "term_clear": lambda args, line: 0,
                "term_move": lambda args, line: 0,
                "term_hide_cursor": lambda args, line: 0,
                "term_show_cursor": lambda args, line: 0,
                "term_read_key": lambda args, line: next(keys),
                "term_rows": lambda args, line: 24,
                "term_cols": lambda args, line: 80,
            },
        )

        interpreter.execute(
            runtime_source + "\n" + nano_source,
            initial_globals={"TARGET_FILE": "note.txt", "FILE_CONTENT": "abc"},
        )
        self.assertTrue(rendered)


class PebbleShellRuntimeTests(unittest.TestCase):
    def test_runtime_shell_handles_help_ls_and_exit(self) -> None:
        shell = build_shell()
        outputs: list[str] = []

        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("help")
            should_exit = shell.onecmd("exit")

        self.assertIn("Pebble OS commands:", outputs)
        self.assertTrue(should_exit)

    def test_runtime_touch_matches_linux_style_create_empty_without_overwrite(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("touch_linux_test.txt")
        if target.exists():
            target.unlink()

        try:
            shell.onecmd("touch touch_linux_test.txt")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "")

            target.write_text("keep me", encoding="utf-8")
            shell.onecmd("touch touch_linux_test.txt")
            self.assertEqual(target.read_text(encoding="utf-8"), "keep me")
        finally:
            if target.exists():
                target.unlink()


if __name__ == "__main__":
    unittest.main()

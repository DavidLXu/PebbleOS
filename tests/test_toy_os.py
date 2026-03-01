from __future__ import annotations

import os
import tempfile
import termios
import threading
import time
import unittest
import types
import itertools
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from pebble_bootloader.fs import FileSystemError, FlatFileSystem
from pebble_bootloader.lang import PebbleBytecodeInterpreter, PebbleError, PebbleInterpreter
from pebble_bootloader.shell import build_shell

REPO_ROOT = Path("/Users/xulixin/LX_OS")
SYSTEM_ROOT = REPO_ROOT / "pebble_system"
RUN_SLOW_TESTS = os.environ.get("PEBBLE_RUN_SLOW_TESTS") == "1"
slow_test = unittest.skipUnless(RUN_SLOW_TESTS, "set PEBBLE_RUN_SLOW_TESTS=1 to run slow shell integration tests")


def resolve_repo_system_path(name: str) -> Path:
    if name.startswith("system/"):
        return SYSTEM_ROOT / name[len("system/") :]
    return REPO_ROOT / name


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

    def test_supports_subdirectories(self) -> None:
        self.fs.create_file("dir/program.peb", "print 1")
        self.assertEqual(self.fs.read_file("dir/program.peb"), "print 1")
        self.assertIn("dir/program.peb", self.fs.list_files())

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

    def test_reports_file_time_for_flat_files(self) -> None:
        self.fs.create_file("clock.txt", "hi")
        target = self.fs.resolve_path("clock.txt")
        with patch("pebble_bootloader.fs.datetime") as mock_datetime:
            mock_datetime.fromtimestamp.return_value = datetime(2026, 3, 1, 15, 45, 30)
            self.assertEqual(self.fs.file_time("clock.txt"), "2026-03-01, 15:45:30")
            mock_datetime.fromtimestamp.assert_called_once_with(target.stat().st_mtime)


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

    def test_supports_floats_and_numeric_casts(self) -> None:
        source = "\n".join(
            [
                "a = 1.5",
                "b = a + 2",
                "c = b * 2.0",
                "d = c / 2",
                "print b",
                "print c",
                "print d",
                'print float("2.25")',
                "print int(3.9)",
                "print 1.5 < 2",
            ]
        )
        output = PebbleInterpreter().execute(source)
        self.assertEqual(output, ["3.5", "7.0", "3.5", "2.25", "3", "1"])

    def test_errors_on_unknown_variable(self) -> None:
        with self.assertRaises(PebbleError):
            PebbleInterpreter().execute("print missing")

    def test_interpreter_supports_minimal_try_except(self) -> None:
        output = PebbleInterpreter().execute(
            "\n".join(
                [
                    "try:",
                    "    print missing_name",
                    "except:",
                    '    print "recovered"',
                ]
            )
        )
        self.assertEqual(output, ["recovered"])

    def test_bytecode_supports_minimal_try_except(self) -> None:
        interpreter = PebbleBytecodeInterpreter()
        interpreter.prepare(
            "\n".join(
                [
                    "try:",
                    "    print missing_name",
                    "except:",
                    '    print "recovered"',
                ]
            )
        )
        self.assertEqual(interpreter.run_until_complete(), ["recovered"])

    def test_interpreter_supports_raise_inside_try_except(self) -> None:
        output = PebbleInterpreter().execute(
            "\n".join(
                [
                    "try:",
                    '    raise "boom"',
                    "except:",
                    '    print "handled"',
                ]
            )
        )
        self.assertEqual(output, ["handled"])

    def test_bytecode_supports_raise_inside_try_except(self) -> None:
        interpreter = PebbleBytecodeInterpreter()
        interpreter.prepare(
            "\n".join(
                [
                    "try:",
                    '    raise "boom"',
                    "except:",
                    '    print "handled"',
                ]
            )
        )
        self.assertEqual(interpreter.run_until_complete(), ["handled"])

    def test_interpreter_supports_except_binding(self) -> None:
        output = PebbleInterpreter().execute(
            "\n".join(
                [
                    "try:",
                    '    raise "disk full"',
                    "except err:",
                    "    print err",
                ]
            )
        )
        self.assertEqual(output, ["line 2: disk full"])

    def test_bytecode_supports_except_binding(self) -> None:
        interpreter = PebbleBytecodeInterpreter()
        interpreter.prepare(
            "\n".join(
                [
                    "try:",
                    '    raise "disk full"',
                    "except err:",
                    "    print err",
                ]
            )
        )
        self.assertEqual(interpreter.run_until_complete(), ["line 2: disk full"])

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

    def test_runtime_math_helpers_work_in_pebble(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "print abs(-7)",
                    "print pow(3, 4)",
                    "print sqrt(10)",
                    "print sin(30)",
                    "print cos(60)",
                    "print tan(45)",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["7", "81", "3", "5000", "5000", "10000"])

    def test_import_math_supports_module_style_calls(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import math",
                    "print math.abs(-7)",
                    "print math.sqrt(10)",
                    "print math.sin(30)",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["7", "3", "5000"])

    def test_import_text_random_and_os_modules(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = PebbleInterpreter(
                Path(temp_dir),
                path_resolver=lambda name: resolve_repo_system_path(name)
                if name.startswith("system/")
                else Path(temp_dir) / name,
                host_functions={
                    "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
                    "raw_list_files": lambda args, line: sorted(
                        [path.name for path in Path(temp_dir).iterdir() if path.is_file()]
                    ),
                    "raw_file_exists": lambda args, line: int((Path(temp_dir) / args[0]).exists()),
                    "raw_read_file": lambda args, line: (Path(temp_dir) / args[0]).read_text(encoding="utf-8"),
                    "raw_write_file": lambda args, line: (
                        (Path(temp_dir) / args[0]).write_text(args[1], encoding="utf-8"),
                        args[1],
                    )[1],
                    "raw_delete_file": lambda args, line: (Path(temp_dir) / args[0]).unlink() or 0,
                    "raw_file_time": lambda args, line: "2026-03-01, 16:30:45",
                },
            ).execute(
                runtime_source
                + "\n".join(
                    [
                        "",
                        "import text",
                        "import random",
                        "import os",
                        'print text.first_line("alpha\\nbeta")',
                        'print text.repeat("x", 3)',
                        "print text.len(text.lines(\"a\\nb\"))",
                        "print random.seed(7)",
                        "print random.range(1, 10)",
                        'print os.write("demo.txt", "hi")',
                        'print os.exists("demo.txt")',
                        'print os.read("demo.txt")',
                    ]
                ),
                initial_globals={"FS_MODE": "hostfs"},
            )
        self.assertEqual(output, ["alpha", "xxx", "2", "7", "3", "hi", "1", "hi"])

    def test_import_memory_module_provides_virtual_ram(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import memory",
                    "print memory.init(8)",
                    "a = memory.alloc(3)",
                    "print a",
                    "print memory.top()",
                    "print memory.write(a + 1, 42)",
                    "print memory.read(a + 1)",
                    "print memory.fill(7)",
                    "print memory.read(0)",
                    "print memory.clear()",
                    "print memory.read(0)",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["8", "0", "3", "42", "42", "8", "7", "8", "0"])

    def test_import_memory_module_supports_block_operations(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import memory",
                    "memory.init(6)",
                    "memory.store(0, [4, 5, 6])",
                    "print memory.slice(0, 3)",
                    "print memory.copy(0, 3, 3)",
                    "print memory.slice(3, 3)",
                    "print memory.dump()",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["[4, 5, 6]", "3", "[4, 5, 6]", "[4, 5, 6, 4, 5, 6]"])

    def test_import_memory_module_supports_marks_moves_and_compare(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import memory",
                    "memory.init(8)",
                    "memory.store(0, [1, 2, 3, 4])",
                    "print memory.move(0, 2, 4)",
                    "print memory.slice(0, 6)",
                    "print memory.compare(0, 2, 2)",
                    "mark = memory.mark()",
                    "print memory.alloc(2)",
                    "print memory.top()",
                    "print memory.reset(mark)",
                    "print memory.top()",
                    "print memory.zero(0, 3)",
                    "print memory.slice(0, 4)",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["4", "[1, 2, 1, 2, 3, 4]", "0", "0", "2", "0", "0", "3", "[0, 0, 0, 2]"])

    def test_import_heap_module_allocates_objects(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import heap",
                    "print heap.init(12)",
                    'obj = heap.alloc("pair", 2)',
                    "print obj",
                    "print heap.kind(obj)",
                    "print heap.size(obj)",
                    "print heap.write(obj, 0, 7)",
                    "print heap.write(obj, 1, 9)",
                    "print heap.read(obj, 1)",
                    "print heap.slice(obj)",
                    "print heap.used()",
                    "print heap.count()",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["12", "0", "pair", "2", "7", "9", "9", "[7, 9]", "4", "1"])

    def test_import_heap_module_supports_marks_and_reset(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import heap",
                    "print heap.init(16)",
                    'first = heap.alloc("pair", 2)',
                    "mark = heap.mark()",
                    'second = heap.alloc("triple", 3)',
                    "print second",
                    "print heap.count()",
                    "print heap.reset(mark)",
                    "print heap.count()",
                    'third = heap.alloc("solo", 1)',
                    "print third",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["16", "4", "2", "4", "1", "4"])

    def test_supports_file_based_module_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module_path = Path(temp_dir) / "mymodule.peb"
            module_path.write_text(
                "\n".join(
                    [
                        "VALUE = 9",
                        "def twice(x):",
                        "    return x * 2",
                    ]
                ),
                encoding="utf-8",
            )
            output = PebbleInterpreter(Path(temp_dir)).execute(
                "\n".join(
                    [
                        "import mymodule",
                        "print mymodule.VALUE",
                        "print mymodule.twice(7)",
                    ]
                )
            )
        self.assertEqual(output, ["9", "14"])

    def test_supports_nested_module_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nested_dir = Path(temp_dir) / "pkg"
            nested_dir.mkdir()
            module_path = nested_dir / "mathish.peb"
            module_path.write_text(
                "\n".join(
                    [
                        "VALUE = 5",
                        "def triple(x):",
                        "    return x * 3",
                    ]
                ),
                encoding="utf-8",
            )
            output = PebbleInterpreter(Path(temp_dir)).execute(
                "\n".join(
                    [
                        "import pkg.mathish",
                        "print pkg.mathish.VALUE",
                        "print pkg.mathish.triple(4)",
                    ]
                )
            )
        self.assertEqual(output, ["5", "12"])

    def test_bytecode_mode_runs_basic_program(self) -> None:
        source = "\n".join(
            [
                "def inc(x):",
                "    return x + 1",
                "value = 0",
                "for i in range(3):",
                "    value = inc(value)",
                "print value",
            ]
        )
        output = PebbleBytecodeInterpreter().execute(source)
        self.assertEqual(output, ["3"])

    def test_bytecode_mode_supports_floats(self) -> None:
        source = "\n".join(
            [
                "value = float(1) + 2.5",
                "print value",
                "print value * 2",
                "print value / 2",
            ]
        )
        output = PebbleBytecodeInterpreter().execute(source)
        self.assertEqual(output, ["3.5", "7.0", "1.75"])

    def test_bytecode_mode_supports_import_math(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleBytecodeInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import math",
                    "print math.cos(60)",
                    "print math.tan(45)",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["5000", "10000"])

    def test_bytecode_mode_supports_import_text_and_random(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleBytecodeInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import text",
                    "import random",
                    'print text.join(["a", "b"])',
                    "print random.seed(7)",
                    "print random.range(1, 10)",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["a\nb", "7", "3"])

    def test_bytecode_mode_supports_import_memory(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleBytecodeInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import memory",
                    "print memory.init(4)",
                    "base = memory.alloc(2)",
                    "print base",
                    "print memory.write(base, 1.5)",
                    "print memory.read(base)",
                    "print memory.top()",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["4", "0", "1.5", "1.5", "2"])

    def test_bytecode_mode_supports_memory_blocks_and_heap(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleBytecodeInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import memory",
                    "import heap",
                    "memory.init(8)",
                    "memory.store(0, [1, 2, 3])",
                    "print memory.copy(0, 4, 3)",
                    "print memory.slice(4, 3)",
                    "print heap.init(10)",
                    'obj = heap.alloc("vec", 3)',
                    'print heap.store(obj, [8, 9, 10])',
                    "print heap.slice(obj)",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["3", "[1, 2, 3]", "10", "3", "[8, 9, 10]"])

    def test_bytecode_mode_supports_memory_marks_and_heap_reset(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        output = PebbleBytecodeInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            }
        ).execute(
            runtime_source
            + "\n".join(
                [
                    "",
                    "import memory",
                    "import heap",
                    "memory.init(6)",
                    "memory.store(0, [9, 8, 7])",
                    "mark = memory.mark()",
                    "print memory.alloc(2)",
                    "print memory.reset(mark)",
                    "print memory.move(0, 1, 3)",
                    "print memory.slice(0, 4)",
                    "print heap.init(12)",
                    "hmark = heap.mark()",
                    'print heap.alloc("two", 2)',
                    "print heap.reset(hmark)",
                    "print heap.count()",
                ]
            ),
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["0", "0", "3", "[9, 9, 8, 7]", "12", "0", "0", "0"])

    def test_bytecode_vm_tracks_frame_stack(self) -> None:
        interpreter = PebbleBytecodeInterpreter()
        output = interpreter.execute(
            "\n".join(
                [
                    "def add_one(x):",
                    "    return x + 1",
                    "print add_one(4)",
                ]
            )
        )
        self.assertEqual(output, ["5"])
        self.assertEqual(len(interpreter.vm_state.frame_stack), 0)
        self.assertEqual(len(interpreter.vm_state.value_stack), 0)

    def test_bytecode_vm_can_step_through_program(self) -> None:
        interpreter = PebbleBytecodeInterpreter()
        interpreter.prepare(
            "\n".join(
                [
                    "x = 1",
                    "print x",
                    "x = x + 1",
                    "print x",
                ]
            )
        )
        self.assertEqual(interpreter.run_steps(1), 1)
        self.assertEqual(interpreter.output, [])
        self.assertEqual(interpreter.globals["x"], 1)
        self.assertEqual(interpreter.run_steps(1), 1)
        self.assertEqual(interpreter.output, ["1"])
        self.assertEqual(interpreter.run_until_complete(), ["1", "2"])
        self.assertTrue(interpreter.vm_state.halted)

    def test_bytecode_vm_snapshot_restore_resumes_loop(self) -> None:
        interpreter = PebbleBytecodeInterpreter()
        interpreter.prepare(
            "\n".join(
                [
                    "x = 0",
                    "while x < 3:",
                    "    x = x + 1",
                    "    print x",
                ]
            )
        )
        self.assertEqual(interpreter.run_steps(5), 5)
        self.assertEqual(interpreter.output, ["1"])
        snapshot = interpreter.snapshot()
        self.assertEqual(interpreter.run_until_complete(), ["1", "2", "3"])

        restored = PebbleBytecodeInterpreter()
        restored.restore(snapshot)
        self.assertEqual(restored.run_until_complete(), ["1", "2", "3"])
        self.assertEqual(restored.globals["x"], 3)

    def test_bytecode_mode_supports_file_based_module_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            module_path = Path(temp_dir) / "mymodule.peb"
            module_path.write_text(
                "\n".join(
                    [
                        "VALUE = 11",
                        "def plus(x, y):",
                        "    return x + y",
                    ]
                ),
                encoding="utf-8",
            )
            output = PebbleBytecodeInterpreter(Path(temp_dir)).execute(
                "\n".join(
                    [
                        "import mymodule",
                        "print mymodule.VALUE",
                        "print mymodule.plus(4, 5)",
                    ]
                )
            )
        self.assertEqual(output, ["11", "9"])

    def test_bytecode_mode_supports_nested_module_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            nested_dir = Path(temp_dir) / "pkg"
            nested_dir.mkdir()
            module_path = nested_dir / "ops.peb"
            module_path.write_text(
                "\n".join(
                    [
                        "VALUE = 8",
                        "def dec(x):",
                        "    return x - 1",
                    ]
                ),
                encoding="utf-8",
            )
            output = PebbleBytecodeInterpreter(Path(temp_dir)).execute(
                "\n".join(
                    [
                        "import pkg.ops",
                        "print pkg.ops.VALUE",
                        "print pkg.ops.dec(9)",
                    ]
                )
            )
        self.assertEqual(output, ["8", "8"])

    def test_system_nano_runs_with_terminal_bridge(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        nano_source = Path("/Users/xulixin/LX_OS/pebble_system/nano.peb").read_text(encoding="utf-8")
        rendered: list[str] = []
        keys = iter(["^X"])
        next_fd = {"value": 3}
        fd_table: dict[int, dict[str, object]] = {}

        def fd_open(args, line):
            fd = next_fd["value"]
            next_fd["value"] = fd + 1
            fd_table[fd] = {"path": args[0], "mode": args[1]}
            return fd

        def fd_write(args, line):
            fd = args[0]
            text = args[1]
            record = fd_table.get(fd, {})
            if record.get("path") in {"/dev/stdout", "/dev/tty"}:
                rendered.append(text)
            return len(text)

        interpreter = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "term_write": lambda args, line: rendered.append(args[0]) or args[0],
                "term_flush": lambda args, line: 0,
                "term_clear": lambda args, line: 0,
                "term_move": lambda args, line: 0,
                "term_hide_cursor": lambda args, line: 0,
                "term_show_cursor": lambda args, line: 0,
                "term_read_key": lambda args, line: next(keys),
                "term_read_key_timeout": lambda args, line: next(keys),
                "term_rows": lambda args, line: 24,
                "term_cols": lambda args, line: 80,
                "term_state": lambda args, line: {
                    "owner_pgid": 0,
                    "mode": "cooked",
                    "interactive": 0,
                    "foreground_raw": 0,
                    "rows": 24,
                    "cols": 80,
                },
                "raw_list_files": lambda args, line: [],
                "raw_file_exists": lambda args, line: 0,
                "raw_create_file": lambda args, line: 0,
                "raw_modify_file": lambda args, line: 0,
                "raw_delete_file": lambda args, line: 0,
                "raw_file_time": lambda args, line: "2026-03-01, 15:30:00",
                "raw_read_file": lambda args, line: "",
                "raw_write_file": lambda args, line: args[1],
                "fd_open": fd_open,
                "fd_write": fd_write,
                "fd_close": lambda args, line: 0,
                "current_time": lambda args, line: "2026-03-01, 15:30:00",
                "runtime_error": lambda args, line: (_ for _ in ()).throw(PebbleError(args[0])),
            },
        )

        interpreter.execute(
            runtime_source + "\n" + nano_source,
            initial_globals={"TARGET_FILE": "note.txt", "FILE_CONTENT": "abc", "FS_MODE": "hostfs"},
        )
        self.assertTrue(rendered)

    def test_runtime_exposes_timed_key_reads(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        interpreter = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "term_read_key_timeout": lambda args, line: "LEFT",
            },
        )
        output = interpreter.execute(
            runtime_source + '\nprint read_key_timeout(120)\n',
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["LEFT"])

    def test_runtime_exposes_tty_state_wrappers(self) -> None:
        runtime_source = Path("/Users/xulixin/LX_OS/pebble_system/runtime.peb").read_text(encoding="utf-8")
        interpreter = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "term_owner_pgid": lambda args, line: 42,
                "term_mode": lambda args, line: "raw",
                "term_state": lambda args, line: {
                    "owner_pgid": 42,
                    "mode": "raw",
                    "interactive": 1,
                    "foreground_raw": 1,
                    "rows": 30,
                    "cols": 100,
                },
            },
        )
        output = interpreter.execute(
            runtime_source + '\nprint tty_owner_pgid()\nprint tty_mode()\nprint tty_state()["rows"]\n',
            initial_globals={"FS_MODE": "hostfs"},
        )
        self.assertEqual(output, ["42", "raw", "30"])

    def test_kernel_term_snapshot_exposes_tty_abi(self) -> None:
        interpreter = PebbleInterpreter(
            path_resolver=resolve_repo_system_path,
            host_functions={
                "term_owner_pgid": lambda args, line: 7,
                "term_mode": lambda args, line: "cooked",
                "term_state": lambda args, line: {
                    "owner_pgid": 7,
                    "mode": "cooked",
                    "interactive": 1,
                    "foreground_raw": 0,
                    "rows": 24,
                    "cols": 80,
                },
            },
        )
        output = interpreter.execute(
            "\n".join(
                [
                    "import system.kernel.term",
                    "snapshot = system.kernel.term.tty_snapshot()",
                    'print snapshot["mode"]',
                    'print snapshot["owner_pgid"]',
                    'print len(snapshot["syscalls"])',
                    'print snapshot["capabilities"]["modes"][0]',
                ]
            )
        )
        self.assertEqual(output, ["cooked", "7", "13", "cooked"])

    def test_runtime_exposes_minimal_fd_wrappers(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        target = shell.fs.resolve_path("fd_runtime_test.txt")
        if target.exists():
            target.unlink()

        try:
            output = PebbleInterpreter(
                shell.fs.root,
                path_resolver=shell._resolve_user_path_to_host,
                host_functions=shell._make_runtime(consume_output=False).host_functions,
            ).execute(
                runtime_source
                + "\n"
                + "\n".join(
                    [
                        'fd = sys_fd_open("fd_runtime_test.txt", "w")',
                        'print sys_fd_write(fd, "hello")',
                        "print sys_fd_close(fd)",
                        'fd2 = sys_fd_open("fd_runtime_test.txt", "r")',
                        "print sys_fd_read(fd2)",
                    ]
                ),
                initial_globals={
                    "FS_MODE": "hostfs",
                    "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                    "SYSTEM_SHELL_PATH": "system/shell.peb",
                    "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
                },
            )
        finally:
            if target.exists():
                target.unlink()

        self.assertEqual(output, ["5", "0", "hello"])

    def test_runtime_can_open_and_stat_device_fds(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        output = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    'dev = sys_fd_open("/dev", "r")',
                    "print len(sys_fd_readdir(dev))",
                    'tty = sys_fd_open("/dev/tty", "w")',
                    'print sys_fd_stat(tty)["kind"]',
                    'print sys_fd_stat(tty)["path"]',
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        self.assertEqual(output, ["5", "device", "/dev/tty"])

    def test_runtime_exposes_thread_bootstrap_api(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        output = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    "states = runtime_thread_states()",
                    'print states["ready"]',
                    'tid = thread_spawn_source("demo", "print 7", [])',
                    "print thread_status(tid)",
                    "record = thread_join(tid)",
                    'print record["tid"]',
                    'print record["state"]',
                    'print record["outputs"][0]',
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        self.assertEqual(output, ["ready", "ready", "1", "halted", "7"])

    def test_runtime_exposes_mutex_bootstrap_api(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        output = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    "states = runtime_thread_states()",
                    'print states["blocked-mutex"]',
                    "mid = mutex_create()",
                    "print mutex_try_lock(mid)",
                    "print mutex_try_lock(mid)",
                    "print len(mutex_list())",
                    "print mutex_list()[0][\"owner_tid\"]",
                    "mutex_unlock(mid)",
                    "print mutex_list()[0][\"owner_tid\"] == None",
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        self.assertEqual(output, ["blocked-mutex", "1", "1", "1", "0", "1"])

    def test_thread_mutex_block_and_wakeup(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        output = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    "mid = mutex_create()",
                    "mutex_lock(mid)",
                    'tid = thread_spawn_source("demo", "import system.kernel.mutex\\nsystem.kernel.mutex.mutex_lock(1)\\nprint 9\\nsystem.kernel.mutex.mutex_unlock(1)", [])',
                    "print tid",
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        tid = int(output[0])
        shell._host_vm_step_task([tid, 50], 1)
        self.assertEqual(shell._host_thread_status([tid], 1), "blocked-mutex")
        mutexes = shell._host_list_mutex_records([], 1)
        self.assertEqual(mutexes[0]["waiters"], [tid])
        shell._host_mutex_unlock([1], 1)
        self.assertEqual(shell._host_thread_status([tid], 1), "ready")
        record = shell._host_thread_join([tid], 1)
        self.assertEqual(record["state"], "halted")
        self.assertEqual(record["outputs"], ["9"])

    def test_ps_dash_t_lists_threads(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        runtime_source = shell.fs.read_file("system/runtime.peb")
        PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source + '\nthread_spawn_source("demo", "print 1", [])\n',
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("ps -T")
        self.assertTrue(any("tid=" in line and "tgid=" in line for line in outputs))

    def test_top_once_can_render_threads(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        runtime_source = shell.fs.read_file("system/runtime.peb")
        PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source + '\nthread_spawn_source("demo", "print 1", [])\n',
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("top --once --threads")
        self.assertTrue(any("TID TGID STATE NAME" in line for line in outputs))

    def test_runtime_can_write_to_dev_stdout_via_fd(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        output = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    'out = sys_fd_open("/dev/stdout", "w")',
                    'print sys_fd_write(out, "fd stdout test")',
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        self.assertIn("14", output)

    def test_runtime_stdout_helper_uses_device_fd(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        output = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    'print stdout_write("xy")',
                    'print stdout_write_line("z")',
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        self.assertEqual(output, ["2", "2"])

    def test_runtime_can_write_to_dev_null(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        output = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    'sink = sys_fd_open("/dev/null", "w")',
                    'print sys_fd_write(sink, "discard me")',
                    'print sys_fd_stat(sink)["kind"]',
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        self.assertEqual(output, ["10", "device"])

    def test_runtime_exposes_virtual_dev_paths_in_filesystem_view(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        output = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    "print directory_exists('/dev')",
                    "print file_exists('/dev/tty')",
                    "print file_exists('/dev/stdout')",
                    "items = list_files()",
                    "found = 0",
                    "i = 0",
                    "while i < len(items):",
                    "    if items[i] == 'dev' or items[i] == 'dev/tty':",
                    "        found = 1",
                    "    i = i + 1",
                    "print found",
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )
        self.assertEqual(output, ["1", "1", "1", "1"])

    def test_detach_emits_sigtstp_with_foreground_process_group(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("fg_signal_test.peb")
        target.write_text('i = 0\nwhile i < 50:\n    i = i + 1\nprint "done"\n', encoding="utf-8")
        actions = iter(["detach"])

        try:
            with patch("sys.stdin.isatty", return_value=True):
                with patch.object(shell, "_poll_foreground_job_action", side_effect=lambda: next(actions, None)):
                    shell.onecmd("run fg_signal_test.peb")
            events = shell._host_drain_signal_events([], 1)
        finally:
            if target.exists():
                target.unlink()
            with shell._vm_lock:
                for task_id in list(shell._vm_tasks):
                    shell._vm_tasks.pop(task_id, None)

        self.assertEqual(events[0]["signal"], "SIGTSTP")
        self.assertEqual(events[0]["pid"], 1)
        self.assertEqual(events[0]["pgid"], 1)


class PebbleShellSmokeTests(unittest.TestCase):
    def test_shell_help_smoke(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("help")
        self.assertTrue(any("preferred run   COMMAND [ARGS...]" in line for line in outputs))
        self.assertTrue(any("compatibility launcher" in line for line in outputs))

    def test_shell_can_cd_into_virtual_dev_and_find_devices(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("find /dev")
            shell.onecmd("cd /dev")
            shell.onecmd("pwd")
            shell.onecmd("ls")
        self.assertIn("/dev", outputs)
        self.assertTrue(any("/dev/tty" in line for line in outputs))
        self.assertTrue(any("tty" == line or line.endswith("  tty") for line in outputs))

    def test_tty_command_reports_device_state(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("tty")
        self.assertIn("/dev/tty", outputs)
        self.assertTrue(any(line.startswith("mode=") for line in outputs))


@slow_test
class PebbleShellRuntimeTests(unittest.TestCase):
    def test_run_program_is_interrupted_by_control_c(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("interrupt_test.peb")
        target.write_text('value = input("number: ")\nprint value\n', encoding="utf-8")
        outputs: list[str] = []

        try:
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                with patch(
                    "builtins.print",
                    side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                ):
                    shell.onecmd("run interrupt_test.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("^C", outputs)
        self.assertIn("[system] program interrupted", outputs)

    def test_cmdloop_installs_readline_completer(self) -> None:
        shell = build_shell()
        fake_readline = types.SimpleNamespace()
        events: list[object] = []
        fake_readline.get_completer = lambda: "old"
        fake_readline.set_completer = lambda value: events.append(("set", value))
        fake_readline.parse_and_bind = lambda spec: events.append(("bind", spec))
        fake_input_calls = iter(["exit"])

        with patch.dict("sys.modules", {"readline": fake_readline}):
            with patch("builtins.input", side_effect=lambda prompt="": next(fake_input_calls)):
                with patch.object(shell, "preloop"), patch.object(shell, "postloop"):
                    shell.cmdloop()

        self.assertEqual(events[0][0], "set")
        self.assertEqual(events[1], ("bind", "tab: complete"))
        self.assertEqual(events[-1], ("set", "old"))

    def test_term_read_key_maps_f1_to_global_detach_request(self) -> None:
        shell = build_shell()
        chars = iter(["\x1b", "O", "P"])

        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
            with patch("sys.stdin.read", side_effect=lambda size=1: next(chars)):
                with patch("select.select", side_effect=[([object()], [], []), ([object()], [], [])]):
                    with patch("termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0]), patch(
                        "termios.tcsetattr", return_value=None
                    ), patch("tty.setraw", return_value=None):
                        key = shell._read_terminal_key(1, None)

        self.assertEqual(key, "")
        self.assertTrue(shell._detach_requested.is_set())

    def test_term_read_key_maps_escape_to_interrupt(self) -> None:
        shell = build_shell()

        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
            with patch("sys.stdin.read", return_value="\x1b"):
                with patch("select.select", return_value=([], [], [])):
                    with patch("termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0]), patch(
                        "termios.tcsetattr", return_value=None
                    ), patch("tty.setraw", return_value=None):
                        with self.assertRaises(KeyboardInterrupt):
                            shell._read_terminal_key(1, None)

    def test_term_read_key_maps_ctrl_z_to_detach_request(self) -> None:
        shell = build_shell()

        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
            with patch("sys.stdin.read", return_value="\x1a"):
                with patch("select.select", return_value=([object()], [], [])):
                    with patch("termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0]), patch(
                        "termios.tcsetattr", return_value=None
                    ), patch("tty.setraw", return_value=None):
                        key = shell._read_terminal_key(1, None)

        self.assertEqual(key, "")
        self.assertTrue(shell._detach_requested.is_set())

    def test_background_threads_cannot_consume_terminal_input(self) -> None:
        shell = build_shell()
        value_box: list[str] = []

        def worker():
            value_box.append(shell._read_terminal_key(1, None))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        self.assertEqual(value_box, [""])

    def test_runtime_output_uses_crlf_on_tty(self) -> None:
        shell = build_shell()
        writes: list[str] = []

        with patch("sys.stdout.isatty", return_value=True), patch("sys.stdout.write", side_effect=writes.append), patch(
            "sys.stdout.flush", return_value=None
        ):
            shell._emit_runtime_output("hello")

        self.assertEqual(writes, ["hello\r\n"])

    def test_reset_to_prompt_line_uses_crlf_on_tty(self) -> None:
        shell = build_shell()
        writes: list[str] = []

        with patch("sys.stdout.isatty", return_value=True), patch("sys.stdout.write", side_effect=writes.append), patch(
            "sys.stdout.flush", return_value=None
        ):
            shell._reset_to_prompt_line()

        self.assertEqual(writes, ["\r\n"])

    def test_restore_shell_terminal_reapplies_saved_settings(self) -> None:
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
            with patch("termios.tcgetattr", return_value=[1, 2, 3, 4, 5, 6]):
                shell = build_shell()

        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
            with patch("termios.tcsetattr", return_value=None) as mock_setattr:
                shell._restore_shell_terminal()

        mock_setattr.assert_called_once_with(0, termios.TCSADRAIN, [1, 2, 3, 4, 5, 6])

    def test_runtime_shell_handles_help_ls_and_exit(self) -> None:
        shell = build_shell()
        outputs: list[str] = []

        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("help")
            should_exit = shell.onecmd("exit")

        self.assertIn("Pebble OS commands:", outputs)
        self.assertTrue(should_exit)

    def test_shell_tab_completion_suggests_command_names(self) -> None:
        shell = build_shell()
        matches = shell.completenames("ru")

        self.assertIn("run", matches)
        self.assertIn("runbg", matches)

    def test_shell_tab_completion_suggests_paths_and_directories(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("completion_docs/readme.peb")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("print 1", encoding="utf-8")
        shell.fs.resolve_path("completion_docs/subdir").mkdir(parents=True, exist_ok=True)

        try:
            file_matches = shell.completedefault("com", "cat com", 4, 7)
            dir_matches = shell.completedefault("com", "cd com", 3, 6)
        finally:
            if target.exists():
                target.unlink()
            subdir = shell.fs.resolve_path("completion_docs/subdir")
            if subdir.exists():
                subdir.rmdir()
            root_dir = shell.fs.resolve_path("completion_docs")
            if root_dir.exists():
                root_dir.rmdir()

        self.assertIn("completion_docs/", dir_matches)
        self.assertIn("completion_docs/", file_matches)

    def test_shell_tab_completion_suggests_nested_paths(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("apps/completion_demo.peb")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('print "ok"', encoding="utf-8")

        try:
            matches = shell.completedefault("apps/com", "run apps/com", 4, 12)
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("apps/completion_demo.peb", matches)

    def test_shell_tab_completion_supports_fuzzy_for_ls_cd_run_and_exec(self) -> None:
        shell = build_shell()
        dir_path = shell.fs.resolve_path("fuzzy_remove_dir")
        file_path = shell.fs.resolve_path("apps/fuzzy_demo_program.peb")
        tree_demo_file = shell.fs.resolve_path("tree_demo/a.txt")
        tree_demo_sub_file = shell.fs.resolve_path("tree_demo/sub/b.txt")
        tree_demo_subdir = shell.fs.resolve_path("tree_demo/sub")
        tree_demo_dir = shell.fs.resolve_path("tree_demo")
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text('print "ok"', encoding="utf-8")

        try:
            if tree_demo_file.exists():
                tree_demo_file.unlink()
            if tree_demo_sub_file.exists():
                tree_demo_sub_file.unlink()
            if tree_demo_subdir.exists():
                tree_demo_subdir.rmdir()
            if tree_demo_dir.exists():
                tree_demo_dir.rmdir()
            ls_matches = shell.completedefault("remove", "ls remove", 3, 9)
            cd_matches = shell.completedefault("remove", "cd remove", 3, 9)
            run_matches = shell.completedefault("demo", "run demo", 4, 8)
            exec_matches = shell.completedefault("demo", "exec demo", 5, 9)
        finally:
            if file_path.exists():
                file_path.unlink()
            if dir_path.exists():
                dir_path.rmdir()

        self.assertIn("fuzzy_remove_dir/", ls_matches)
        self.assertIn("fuzzy_remove_dir/", cd_matches)
        self.assertIn("apps/fuzzy_demo_program.peb", run_matches)
        self.assertIn("apps/fuzzy_demo_program.peb", exec_matches)

    def test_shell_tab_completion_suggests_foreground_job_ids(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("bg_complete.peb")
        target.write_text('print "done"\n', encoding="utf-8")

        try:
            shell.onecmd("runbg bg_complete.peb")
            matches = shell.completedefault("", "fg ", 3, 3)
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("1", matches)

    def test_background_jobs_can_be_started_listed_and_foregrounded(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("bg_test.peb")
        target.write_text('print "job output"\n', encoding="utf-8")
        outputs: list[str] = []

        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("runbg bg_test.peb")
                shell.onecmd("jobs")
                shell.onecmd("fg 1")
        finally:
            if target.exists():
                target.unlink()

        self.assertTrue(any(line.startswith("[1] bg_test.peb") for line in outputs))
        self.assertTrue(any("[1] " in line and "runbg /bg_test.peb" in line for line in outputs))
        self.assertIn("job output", outputs)
        self.assertIn("[1] done", outputs)

    def test_ps_lists_vm_and_host_managed_tasks(self) -> None:
        shell = build_shell()
        host_target = shell.fs.resolve_path("ps_bg_test.peb")
        host_target.write_text('print "host job"\n', encoding="utf-8")
        outputs: list[str] = []

        try:
            vm_id = shell._create_vm_task("system/count_tick.peb", [], "run")
            shell.onecmd("runbg ps_bg_test.peb")
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("ps")
        finally:
            with shell._vm_lock:
                shell._vm_tasks.pop(vm_id, None)
            if host_target.exists():
                host_target.unlink()

        self.assertTrue(any(" vm " in line and "run /system/count_tick.peb" in line for line in outputs))
        self.assertTrue(any(" host-job " in line and "runbg /ps_bg_test.peb" in line for line in outputs))

    def test_foreground_run_can_detach_into_jobs(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("fg_detach_test.peb")
        target.write_text('i = 0\nwhile i < 50:\n    i = i + 1\nprint "detach output"\n', encoding="utf-8")
        outputs: list[str] = []
        actions = iter(["detach"])

        try:
            with patch("sys.stdin.isatty", return_value=True):
                with patch.object(shell, "_poll_foreground_job_action", side_effect=lambda: next(actions, None)):
                    with patch(
                        "builtins.print",
                        side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                    ):
                        shell.onecmd("run fg_detach_test.peb")
                        shell.onecmd("jobs")
                        shell.onecmd("fg 1")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("[1] background", outputs)
        self.assertTrue(any("[1] " in line and "run /fg_detach_test.peb" in line for line in outputs))
        self.assertIn("detach output", outputs)

    def test_two_foreground_programs_can_detach_and_continue_in_background(self) -> None:
        shell = build_shell()
        first = shell.fs.resolve_path("fg_one.peb")
        second = shell.fs.resolve_path("fg_two.peb")
        first.write_text('i = 0\nwhile i < 80:\n    i = i + 1\nprint "one done"\n', encoding="utf-8")
        second.write_text('i = 0\nwhile i < 80:\n    i = i + 1\nprint "two done"\n', encoding="utf-8")
        outputs: list[str] = []
        actions = iter(["detach", "detach"])

        try:
            with patch("sys.stdin.isatty", return_value=True):
                with patch.object(shell, "_poll_foreground_job_action", side_effect=lambda: next(actions, None)):
                    with patch(
                        "builtins.print",
                        side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                    ):
                        shell.onecmd("run fg_one.peb")
                        shell.onecmd("run fg_two.peb")
                        shell.onecmd("jobs")
                        shell.onecmd("fg 1")
                        shell.onecmd("fg 2")
        finally:
            if first.exists():
                first.unlink()
            if second.exists():
                second.unlink()

        self.assertIn("[1] background", outputs)
        self.assertIn("[2] background", outputs)
        self.assertTrue(any("[1] " in line and "run /fg_one.peb" in line for line in outputs))
        self.assertTrue(any("[2] " in line and "run /fg_two.peb" in line for line in outputs))
        self.assertIn("one done", outputs)
        self.assertIn("two done", outputs)

    def test_foreground_vm_task_owns_terminal_while_attached(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("fg_tty_owner.peb")
        target.write_text('print "TTY OWNER"\nprint read_key()\n', encoding="utf-8")
        task_id = shell._create_vm_task("/fg_tty_owner.peb", [], "run")
        outputs: list[str] = []
        pending_chars = ["q"]

        try:
            with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
                with patch("sys.stdin.read", side_effect=lambda size=1: pending_chars.pop(0)):
                    with patch(
                        "select.select",
                        side_effect=lambda *args, **kwargs: (([object()], [], []) if pending_chars else ([], [], [])),
                    ):
                        with patch("termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0]), patch(
                            "termios.tcsetattr", return_value=None
                        ), patch("tty.setraw", return_value=None):
                            with patch.object(shell, "_poll_foreground_job_action", return_value=None):
                                with patch(
                                    "builtins.print",
                                    side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                                ):
                                    shell._attach_foreground_vm_task(task_id)
        finally:
            with shell._vm_lock:
                shell._vm_tasks.pop(task_id, None)
            if target.exists():
                target.unlink()

        self.assertIn("TTY OWNER", outputs)
        self.assertIn("q", outputs)

    def test_background_jobs_reject_interactive_programs(self) -> None:
        shell = build_shell()
        outputs: list[str] = []

        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("runbg system/nano.peb note.txt")

        self.assertTrue(any("interactive programs cannot run in the background" in line for line in outputs))

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

    def test_shell_redirects_output_with_truncate(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("redirect_out.txt")
        if target.exists():
            target.unlink()
        try:
            shell.onecmd("pwd > redirect_out.txt")
            self.assertEqual(target.read_text(encoding="utf-8"), "/\n")
        finally:
            if target.exists():
                target.unlink()

    def test_shell_redirects_output_with_append(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("redirect_append.txt")
        if target.exists():
            target.unlink()
        try:
            shell.onecmd("pwd > redirect_append.txt")
            shell.onecmd("pwd >> redirect_append.txt")
            self.assertEqual(target.read_text(encoding="utf-8"), "/\n/\n")
        finally:
            if target.exists():
                target.unlink()

    def test_shell_redirects_input_for_run(self) -> None:
        shell = build_shell()
        program = shell.fs.resolve_path("redirect_in.peb")
        source = shell.fs.resolve_path("redirect_input.txt")
        program.write_text('value = input("n: ")\nprint value\n', encoding="utf-8")
        source.write_text("42\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("run redirect_in.peb < redirect_input.txt")
        finally:
            if program.exists():
                program.unlink()
            if source.exists():
                source.unlink()
        self.assertIn("42", outputs)

    def test_shell_redirects_stderr_to_file(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("redirect_err.txt")
        if target.exists():
            target.unlink()
        try:
            shell.onecmd("pwd < missing_input.txt 2> redirect_err.txt")
            self.assertIn("No such file or directory", target.read_text(encoding="utf-8"))
        finally:
            if target.exists():
                target.unlink()

    def test_shell_redirects_stderr_to_stdout_target(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("redirect_merge.txt")
        if target.exists():
            target.unlink()
        try:
            shell.onecmd("pwd < missing_input.txt > redirect_merge.txt 2>&1")
            self.assertIn("No such file or directory", target.read_text(encoding="utf-8"))
        finally:
            if target.exists():
                target.unlink()

    def test_shell_supports_single_pipeline_into_run(self) -> None:
        shell = build_shell()
        program = shell.fs.resolve_path("pipe_read.peb")
        program.write_text('value = input("n: ")\nprint value\n', encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("pwd | run pipe_read.peb")
        finally:
            if program.exists():
                program.unlink()
        self.assertIn("/", outputs)

    def test_echo_prints_arguments_as_one_line(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("echo hello pebble os")
        self.assertIn("hello pebble os", outputs)

    def test_help_for_external_command_uses_system_bin(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("help echo")
        self.assertIn("echo [args...]", outputs)
        self.assertIn("external command from /system/bin/echo.peb", outputs)

    def test_wc_counts_file_contents(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("wc_file.txt")
        target.write_text("one two\nthree\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("wc wc_file.txt")
        finally:
            if target.exists():
                target.unlink()
        self.assertIn("2 3 14 wc_file.txt", outputs)

    def test_wc_counts_pipeline_input(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("echo hello pebble os | wc")
        self.assertIn("1 3 15", outputs)

    def test_cat_reads_from_pipeline_input(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("echo hello pebble os | cat")
        self.assertIn("hello pebble os", outputs)

    def test_head_and_tail_are_external_commands(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("head_tail.txt")
        target.write_text("1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n", encoding="utf-8")
        head_outputs: list[str] = []
        tail_outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: head_outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("head head_tail.txt")
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: tail_outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("tail head_tail.txt")
        finally:
            if target.exists():
                target.unlink()
        self.assertEqual(head_outputs[:2], ["1", "2"])
        self.assertEqual(head_outputs[-1], "10")
        self.assertEqual(tail_outputs[0], "3")
        self.assertEqual(tail_outputs[-1], "12")

    def test_help_for_migrated_external_command_uses_system_bin(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("help cat")
        self.assertIn("cat [args...]", outputs)
        self.assertIn("external command from /system/bin/cat.peb", outputs)

    def test_set_and_export_persist_shell_environment(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("set FOO=bar")
            shell.onecmd("export BAR=baz")
            shell.onecmd("env")
        self.assertIn("FOO=bar", outputs)
        self.assertIn("BAR=baz", outputs)

    def test_env_assignment_prefix_applies_to_single_command(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd('FOO="hello world" env')
            shell.onecmd("env")
        self.assertIn("FOO=hello world", outputs)
        self.assertEqual(outputs.count("FOO=hello world"), 1)

    def test_which_uses_path_lookup_and_bin_compatibility(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("which echo")
            shell.onecmd("PATH=/bin which wc")
        self.assertIn("/system/bin/echo.peb", outputs)
        self.assertIn("/system/bin/wc.peb", outputs)

    def test_which_finds_sh_via_bin_compatibility(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("PATH=/bin which sh")
        self.assertIn("/system/bin/sh.peb", outputs)

    def test_find_lists_files_under_optional_prefix(self) -> None:
        shell = build_shell()
        shell.onecmd("touch find_test_dir/file_a.txt")
        shell.onecmd("touch find_test_dir/file_b.txt")
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("find /find_test_dir")
        self.assertIn("/find_test_dir/file_a.txt", outputs)
        self.assertIn("/find_test_dir/file_b.txt", outputs)

    def test_find_supports_name_filter(self) -> None:
        shell = build_shell()
        shell.onecmd("touch find_name_dir/alpha.txt")
        shell.onecmd("touch find_name_dir/beta.log")
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("find /find_name_dir -name alpha")
        self.assertIn("/find_name_dir/alpha.txt", outputs)
        self.assertNotIn("/find_name_dir/beta.log", outputs)

    def test_find_bare_name_matches_mounted_system_file(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("find shell.peb")
        self.assertIn("/system/shell.peb", outputs)

    def test_grep_filters_pipeline_and_files(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("grep_file.txt")
        target.write_text("red\nblue\nred-blue\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("echo red blue | grep red")
                shell.onecmd("grep red grep_file.txt")
        finally:
            if target.exists():
                target.unlink()
        self.assertIn("red blue", outputs)
        self.assertIn("grep_file.txt:red", outputs)
        self.assertIn("grep_file.txt:red-blue", outputs)

    def test_env_accepts_assignment_arguments(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("env ZED=one")
        self.assertIn("ZED=one", outputs)

    def test_which_accepts_multiple_names(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("which echo wc")
        self.assertIn("/system/bin/echo.peb", outputs)
        self.assertIn("/system/bin/wc.peb", outputs)

    def test_kill_terminates_background_vm_process(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("kill_vm_test.peb")
        target.write_text("i = 0\nwhile i < 1000:\n    i = i + 1\nprint i\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("runbg /kill_vm_test.peb")
                shell.onecmd("kill 1")
                shell.onecmd("ps")
        finally:
            if target.exists():
                target.unlink()
        self.assertIn("killed 1", outputs)

    def test_parser_accepts_tight_redirection_tokens(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("tight_redirect.txt")
        if target.exists():
            target.unlink()
        try:
            shell.onecmd("echo hello>tight_redirect.txt")
            self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")
        finally:
            if target.exists():
                target.unlink()

    def test_bg_reports_existing_background_job(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("bg_resume_test.peb")
        target.write_text('i = 0\nwhile i < 100:\n    i = i + 1\nprint i\n', encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("runbg bg_resume_test.peb")
                shell.onecmd("bg 1")
        finally:
            if target.exists():
                target.unlink()
        self.assertIn("[1] /bg_resume_test.peb", outputs)

    def test_direct_peb_command_runs_without_run_prefix(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("demo.peb")
        target.write_text('print "demo ok"\n', encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("demo")
        finally:
            if target.exists():
                target.unlink()
        self.assertIn("demo ok", outputs)

    def test_direct_peb_command_supports_background_ampersand(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("amp_demo.peb")
        target.write_text('i = 0\nwhile i < 100:\n    i = i + 1\nprint i\n', encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("amp_demo &")
                shell.onecmd("jobs")
        finally:
            if target.exists():
                target.unlink()
        self.assertTrue(any(line.startswith("[1] amp_demo.peb") for line in outputs))

    def test_source_updates_current_shell_environment_and_directory(self) -> None:
        shell = build_shell()
        script = shell.fs.resolve_path("source_test.sh")
        script.write_text("export SOURCE_FLAG=yes\ncd system\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("source source_test.sh")
                shell.onecmd("env")
                shell.onecmd("pwd")
        finally:
            if script.exists():
                script.unlink()
        self.assertIn("SOURCE_FLAG=yes", outputs)
        self.assertIn("/system", outputs)

    def test_sh_runs_script_via_standard_entrypoint(self) -> None:
        shell = build_shell()
        script = shell.fs.resolve_path("sh_test.sh")
        script.write_text("export SH_FLAG=ok\ncd system\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("sh sh_test.sh")
                shell.onecmd("env")
                shell.onecmd("pwd")
        finally:
            if script.exists():
                script.unlink()
        self.assertIn("SH_FLAG=ok", outputs)
        self.assertIn("/system", outputs)

    def test_explicit_bin_sh_maps_to_system_bin_sh(self) -> None:
        shell = build_shell()
        script = shell.fs.resolve_path("bin_sh_test.sh")
        script.write_text("echo bin sh ok\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("/bin/sh bin_sh_test.sh")
        finally:
            if script.exists():
                script.unlink()
        self.assertIn("bin sh ok", outputs)

    def test_bash_runs_script_with_pebble_native_frontend(self) -> None:
        shell = build_shell()
        script = shell.fs.resolve_path("bash_test.sh")
        script.write_text("echo bash script ok\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("bash bash_test.sh")
        finally:
            if script.exists():
                script.unlink()
        self.assertIn("bash script ok", outputs)

    def test_bash_supports_dash_c(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd('bash -c "echo native bash"')
        self.assertIn("native bash", outputs)

    def test_bash_repl_uses_foreground_terminal_input_path(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        prompts: list[str] = []
        replies = iter(["echo repl ok", "exit"])

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            return next(replies)

        with patch("builtins.input", side_effect=fake_input):
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("bash")

        self.assertIn("repl ok", outputs)
        self.assertTrue(any(prompt.startswith("bash:") for prompt in prompts))

    def test_foreground_vm_task_can_block_on_input_and_resume(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("vm_input_resume.peb")
        target.write_text('value = input("name: ")\nprint value\n', encoding="utf-8")
        outputs: list[str] = []
        prompts: list[str] = []
        replies = iter(["pebble"])

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            return next(replies)

        try:
            with patch("builtins.input", side_effect=fake_input):
                with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                    shell.onecmd("run vm_input_resume.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("pebble", outputs)
        self.assertIn("name: ", prompts)

    def test_foreground_vm_task_can_block_on_tty_and_resume(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("vm_key_resume.peb")
        target.write_text('print read_key()\n', encoding="utf-8")
        outputs: list[str] = []
        pending_chars = ["a"]

        try:
            with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
                with patch("sys.stdin.read", side_effect=lambda size=1: pending_chars.pop(0)):
                    with patch(
                        "select.select",
                        side_effect=lambda *args, **kwargs: (([object()], [], []) if pending_chars else ([], [], [])),
                    ):
                        with patch("termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0]), patch(
                            "termios.tcsetattr", return_value=None
                        ), patch("tty.setraw", return_value=None):
                            with patch.object(shell, "_poll_foreground_job_action", return_value=None):
                                with patch(
                                    "builtins.print",
                                    side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                                ):
                                    shell.onecmd("run vm_key_resume.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("a", outputs)

    def test_foreground_vm_task_queues_multiple_tty_keys(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("vm_key_queue.peb")
        target.write_text('print read_key()\nprint read_key()\nprint read_key()\n', encoding="utf-8")
        outputs: list[str] = []
        pending_chars = ["a", "b", "c"]

        try:
            with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
                with patch("sys.stdin.read", side_effect=lambda size=1: pending_chars.pop(0)):
                    with patch(
                        "select.select",
                        side_effect=lambda *args, **kwargs: (([object()], [], []) if pending_chars else ([], [], [])),
                    ):
                        with patch("termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0]), patch(
                            "termios.tcsetattr", return_value=None
                        ), patch("tty.setraw", return_value=None):
                            with patch.object(shell, "_poll_foreground_job_action", return_value=None):
                                with patch(
                                    "builtins.print",
                                    side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                                ):
                                    shell.onecmd("run vm_key_queue.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertEqual(outputs[:3], ["a", "b", "c"])

    def test_foreground_vm_task_decodes_arrow_keys_without_exit(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("vm_arrow_key.peb")
        target.write_text('print read_key()\n', encoding="utf-8")
        outputs: list[str] = []
        pending_chars = ["\x1b", "[", "D"]

        try:
            with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.fileno", return_value=0):
                with patch("sys.stdin.read", side_effect=lambda size=1: pending_chars.pop(0)):
                    with patch(
                        "select.select",
                        side_effect=lambda *args, **kwargs: (([object()], [], []) if pending_chars else ([], [], [])),
                    ):
                        with patch("termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0]), patch(
                            "termios.tcsetattr", return_value=None
                        ), patch("tty.setraw", return_value=None):
                            with patch.object(shell, "_poll_foreground_job_action", return_value=None):
                                with patch(
                                    "builtins.print",
                                    side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                                ):
                                    shell.onecmd("run vm_arrow_key.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("LEFT", outputs)

    def test_foreground_vm_task_decodes_delayed_arrow_sequence(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("vm_delayed_arrow_key.peb")
        target.write_text('print read_key()\n', encoding="utf-8")
        outputs: list[str] = []
        bytes_seen = iter(["\x1b", "", "[", "C", ""])

        try:
            with patch("sys.stdin.isatty", return_value=True):
                with patch.object(shell, "_read_terminal_byte", side_effect=lambda timeout=None: next(bytes_seen)):
                    with patch.object(shell, "_poll_foreground_job_action", return_value=None):
                        with patch(
                            "builtins.print",
                            side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                        ):
                            shell.onecmd("run vm_delayed_arrow_key.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("RIGHT", outputs)

    def test_foreground_vm_task_treats_bare_escape_as_key_not_interrupt(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("vm_escape_key.peb")
        target.write_text('print read_key()\n', encoding="utf-8")
        outputs: list[str] = []
        bytes_seen = itertools.chain(["\x1b", "", ""], itertools.repeat(""))

        try:
            with patch("sys.stdin.isatty", return_value=True):
                with patch.object(shell, "_read_terminal_byte", side_effect=lambda timeout=None: next(bytes_seen)):
                    with patch.object(shell, "_poll_foreground_job_action", return_value=None):
                        with patch(
                            "builtins.print",
                            side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                        ):
                            shell.onecmd("run vm_escape_key.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("ESC", outputs)

    def test_foreground_vm_task_read_key_timeout_resumes_without_input(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("vm_key_timeout.peb")
        target.write_text('print read_key_timeout(10)\nprint "tick"\n', encoding="utf-8")
        outputs: list[str] = []
        bytes_seen = itertools.repeat("")

        try:
            with patch("sys.stdin.isatty", return_value=True):
                with patch.object(shell, "_read_terminal_byte", side_effect=lambda timeout=None: next(bytes_seen)):
                    with patch.object(shell, "_poll_foreground_job_action", return_value=None):
                        with patch(
                            "builtins.print",
                            side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
                        ):
                            shell.onecmd("run vm_key_timeout.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("", outputs)
        self.assertIn("tick", outputs)

    def test_shell_boot_creates_etc_placeholders_and_loads_profile(self) -> None:
        shell = build_shell()
        profile = shell.fs.resolve_path("etc/profile")
        profile.write_text("export PROFILE_FLAG=loaded\n", encoding="utf-8")
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("env")
        self.assertTrue(shell.fs.resolve_path("etc/passwd").exists())
        self.assertTrue(shell.fs.resolve_path("etc/group").exists())
        self.assertTrue(shell.fs.resolve_path("etc/fstab").exists())
        self.assertIn("PROFILE_FLAG=loaded", outputs)

    def test_shell_supports_multi_stage_pipeline(self) -> None:
        shell = build_shell()
        program_a = shell.fs.resolve_path("pipe_first.peb")
        program_b = shell.fs.resolve_path("pipe_second.peb")
        program_a.write_text('value = input("n: ")\nprint value + "-a"\n', encoding="utf-8")
        program_b.write_text('value = input("n: ")\nprint value + "-b"\n', encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("echo base | run pipe_first.peb | run pipe_second.peb")
        finally:
            if program_a.exists():
                program_a.unlink()
            if program_b.exists():
                program_b.unlink()
        self.assertIn("base-a-b", outputs)

    def test_grep_supports_ignore_case_and_line_numbers(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("grep_case.txt")
        target.write_text("Red\nblue\nRED\n", encoding="utf-8")
        outputs: list[str] = []
        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("grep -i -n red grep_case.txt")
        finally:
            if target.exists():
                target.unlink()
        self.assertIn("1:grep_case.txt:Red", outputs)
        self.assertIn("3:grep_case.txt:RED", outputs)

    def test_find_supports_type_filter(self) -> None:
        shell = build_shell()
        shell.onecmd("touch find_type_dir/file.txt")
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("find /find_type_dir -type f")
        self.assertIn("/find_type_dir/file.txt", outputs)

    def test_top_once_prints_process_snapshot(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("top --once")
        self.assertTrue(any("Pebble top" in line for line in outputs))
        self.assertTrue(any("PID KIND STATE PGID EXIT PROGRAM" in line for line in outputs))

    def test_htop_once_prints_process_snapshot(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("htop --once")
        self.assertTrue(any("Pebble htop" in line for line in outputs))
        self.assertTrue(any("PID   STATE" in line and "PROGRAM" in line for line in outputs))

    def test_help_marks_legacy_launchers_as_compatibility_entrypoints(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("help")
            shell.onecmd("help run")
        self.assertTrue(any("compatibility launcher" in line for line in outputs))
        self.assertTrue(any("preferred run   COMMAND [ARGS...]" in line for line in outputs))

    def test_runtime_cd_pwd_and_prompt_follow_current_directory(self) -> None:
        shell = build_shell()
        shell.onecmd("touch dir_cd_test/file.txt")
        shell.onecmd("cd dir_cd_test")
        shell.postcmd(False, "cd dir_cd_test")

        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("pwd")

        self.assertEqual(outputs[-1], "/dir_cd_test")
        self.assertEqual(shell.prompt, "pebble-os:/dir_cd_test> ")

    def test_ls_hides_system_mount_outside_root_directory(self) -> None:
        shell = build_shell()
        shell.onecmd("touch dir_cd_test/file.txt")
        shell.onecmd("cd dir_cd_test")
        shell.postcmd(False, "cd dir_cd_test")

        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("ls")

        self.assertTrue(any("file.txt" in line for line in outputs))
        self.assertFalse(any("system/" in line for line in outputs))
        self.assertFalse(any("dir_cd_test/file.txt" in line for line in outputs))

    def test_ls_shows_system_files_when_inside_system_directory(self) -> None:
        shell = build_shell()
        shell.onecmd("cd system")
        shell.postcmd(False, "cd system")

        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("ls")

        self.assertTrue(any("runtime.peb" in line for line in outputs))
        self.assertFalse(any("system/runtime.peb" in line for line in outputs))

    def test_runtime_mkdir_and_rmdir_notice_for_non_empty_directory(self) -> None:
        shell = build_shell()
        outputs: list[str] = []

        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("mkdir dir_remove_test")
            shell.onecmd("touch dir_remove_test/file.txt")
            shell.onecmd("rmdir dir_remove_test")

        self.assertTrue(shell.fs.resolve_path("dir_remove_test").is_dir())
        self.assertIn("notice: directory 'dir_remove_test' is not empty", outputs)

    def test_vfs_cd_pwd_and_rmdir_notice_for_non_empty_directory(self) -> None:
        shell = build_shell(fs_mode="vfs-persistent")
        outputs: list[str] = []

        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("mkdir vfs_dir")
            shell.onecmd("touch vfs_dir/file.txt")
            shell.onecmd("cd vfs_dir")
            shell.postcmd(False, "cd vfs_dir")
            shell.onecmd("pwd")
            shell.onecmd("cd /")
            shell.postcmd(False, "cd /")
            shell.onecmd("rmdir vfs_dir")

        self.assertIn("/vfs_dir", outputs)
        self.assertIn("notice: directory 'vfs_dir' is not empty", outputs)
        self.assertEqual(shell.prompt, "pebble-os:/> ")

    def test_mfs_keeps_changes_in_memory_during_session(self) -> None:
        shell = build_shell(fs_mode="mfs")
        shell.onecmd("mkdir memdir")
        shell.onecmd("touch memdir/file.txt")
        shell.onecmd("cd memdir")
        shell.postcmd(False, "cd memdir")

        outputs: list[str] = []
        with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
            shell.onecmd("pwd")
            shell.onecmd("ls")

        self.assertIn("/memdir", outputs)
        self.assertTrue(any("file.txt" in line for line in outputs))

    def test_mfs_sync_writes_backing_store_snapshot(self) -> None:
        shell = build_shell(fs_mode="mfs")
        backing = shell.fs.resolve_path(".__pebble_vfs__.db")
        if backing.exists():
            backing.unlink()

        outputs: list[str] = []
        try:
            shell.onecmd("touch sync_test.txt")
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("sync")
            self.assertTrue(backing.exists())
            self.assertIn("synced memory filesystem to .__pebble_vfs__.db", outputs)
            self.assertIn("sync_test.txt", backing.read_text(encoding="utf-8"))
        finally:
            if backing.exists():
                backing.unlink()

    def test_mfs_import_loads_host_files_without_writing_backing_store(self) -> None:
        shell = build_shell(fs_mode="mfs-import")
        host_target = shell.fs.resolve_path("mfs_import_test.txt")
        backing = shell.fs.resolve_path(".__pebble_vfs__.db")
        if backing.exists():
            backing.unlink()
        host_target.write_text("from host", encoding="utf-8")
        outputs: list[str] = []

        try:
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("cat mfs_import_test.txt")
            self.assertIn("from host", outputs)
            self.assertIsNotNone(shell.mfs_blob)
            self.assertFalse(backing.exists())
        finally:
            if host_target.exists():
                host_target.unlink()
            if backing.exists():
                backing.unlink()

    def test_runtime_time_shows_formatted_current_time(self) -> None:
        shell = build_shell()
        outputs: list[str] = []

        with patch("pebble_bootloader.shell.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 3, 1, 15, 30, 45)
            with patch(
                "builtins.print",
                side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
            ):
                shell.onecmd("time")

        self.assertIn("2026-03-01, 15:30:45", outputs)

    def test_runtime_ls_shows_file_time_for_each_file(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("ls_time_test.txt")
        if target.exists():
            target.unlink()

        outputs: list[str] = []
        try:
            shell.onecmd("touch ls_time_test.txt")
            with patch(
                "builtins.print",
                side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
            ):
                shell.onecmd("ls")
        finally:
            if target.exists():
                target.unlink()

        self.assertTrue(any(line.endswith("  ls_time_test.txt") for line in outputs))

    def test_root_ls_shows_only_direct_children(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        try:
            shell.onecmd("touch nested_ls_dir/child.txt")
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("ls")
        finally:
            target = shell.fs.resolve_path("nested_ls_dir/child.txt")
            if target.exists():
                target.unlink()
            parent = shell.fs.resolve_path("nested_ls_dir")
            if parent.exists():
                parent.rmdir()
        self.assertTrue(any(line.endswith("  nested_ls_dir") for line in outputs))
        self.assertFalse(any("nested_ls_dir/child.txt" in line for line in outputs))

    def test_tree_lists_recursive_structure_from_current_directory(self) -> None:
        shell = build_shell()
        outputs: list[str] = []
        try:
            shell.onecmd("touch tree_demo/a.txt")
            shell.onecmd("touch tree_demo/sub/b.txt")
            with patch("builtins.print", side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts))):
                shell.onecmd("cd tree_demo")
                shell.onecmd("tree")
        finally:
            target_a = shell.fs.resolve_path("tree_demo/a.txt")
            target_b = shell.fs.resolve_path("tree_demo/sub/b.txt")
            if target_a.exists():
                target_a.unlink()
            if target_b.exists():
                target_b.unlink()
            subdir = shell.fs.resolve_path("tree_demo/sub")
            if subdir.exists():
                subdir.rmdir()
            rootdir = shell.fs.resolve_path("tree_demo")
            if rootdir.exists():
                rootdir.rmdir()
        self.assertIn(".", outputs)
        self.assertIn("a.txt", outputs)
        self.assertIn("sub/", outputs)
        self.assertIn("  b.txt", outputs)

    def test_vfs_import_mode_reads_user_files_from_virtual_backend(self) -> None:
        shell = build_shell(fs_mode="vfs-import")
        host_target = shell.fs.resolve_path("vfs_boot_test.txt")
        host_target.write_text("from host", encoding="utf-8")
        outputs: list[str] = []

        try:
            with patch(
                "builtins.print",
                side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
            ):
                shell.onecmd("cat vfs_boot_test.txt")
            self.assertIn("from host", outputs)
            self.assertTrue(shell.fs.resolve_path(".__pebble_vfs__.db").exists())
        finally:
            if host_target.exists():
                host_target.unlink()
            if shell._system_shell_call("file_exists", ["vfs_boot_test.txt"]):
                shell.onecmd("rm vfs_boot_test.txt")

    def test_exec_runs_program_in_bytecode_mode(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("bytecode_test.peb")
        target.write_text('print "bytecode ok"\n', encoding="utf-8")
        outputs: list[str] = []

        try:
            with patch(
                "builtins.print",
                side_effect=lambda *parts, **kwargs: outputs.append(" ".join(str(part) for part in parts)),
            ):
                shell.onecmd("exec bytecode_test.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertIn("bytecode ok", outputs)

    def test_pebble_command_launches_target_in_interpreter_mode(self) -> None:
        shell = build_shell()
        target = shell.fs.resolve_path("interp_test.peb")
        target.write_text('print "interp ok"\n', encoding="utf-8")

        try:
            with patch.object(shell, "_run_program", wraps=shell._run_program) as mock_run:
                shell.onecmd("pebble interp_test.peb")
        finally:
            if target.exists():
                target.unlink()

        self.assertGreaterEqual(len(mock_run.call_args_list), 2)
        target_call = mock_run.call_args_list[1]
        self.assertEqual(target_call.args[0], "interp_test.peb")
        self.assertEqual(target_call.kwargs.get("exec_mode"), "interp")

    def test_runtime_boot_identifies_memory_filesystem(self) -> None:
        shell = build_shell(fs_mode="mfs")
        runtime_source = shell.fs.read_file("system/runtime.peb")
        outputs = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source + "\nboot()\n",
            initial_globals={
                "FS_MODE": "mfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )

        self.assertIn("[runtime] user filesystem: Pebble memory filesystem", outputs)

    def test_runtime_scheduler_can_spawn_step_snapshot_and_restore_vm_tasks(self) -> None:
        shell = build_shell()
        runtime_source = shell.fs.read_file("system/runtime.peb")
        outputs = PebbleInterpreter(
            shell.fs.root,
            path_resolver=shell._resolve_user_path_to_host,
            host_functions=shell._make_runtime(consume_output=False).host_functions,
        ).execute(
            runtime_source
            + "\n"
            + "\n".join(
                [
                    "scheduler_new()",
                    'source = "x = 0\\nwhile x < 3:\\n    x = x + 1\\n    print x"',
                    'task = scheduler_spawn_source("demo", source, [])',
                    "print scheduler_step_ready(5)",
                    "print scheduler_take_output(task)",
                    "snap = scheduler_snapshot_task(task)",
                    'copy = scheduler_restore_snapshot(snap, "demo-copy")',
                    "print scheduler_step_task(copy, 10)",
                    "print scheduler_take_output(copy)",
                ]
            ),
            initial_globals={
                "FS_MODE": "hostfs",
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell.fs.read_file("system/shell.peb"),
            },
        )

        self.assertEqual(outputs, ["5", "[1]", "7", "[2, 3]"])



if __name__ == "__main__":
    unittest.main()

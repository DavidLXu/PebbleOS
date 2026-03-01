from pathlib import Path

from pebble_bootloader.lang import PebbleError, PebbleInterpreter
from pebble_bootloader.shell import build_shell


if __name__ == "__main__":
    shell = build_shell()
    runtime_path = shell.fs.resolve_path("system/runtime.peb")
    shell_path = shell.fs.resolve_path("system/shell.peb")
    runtime_source = runtime_path.read_text(encoding="utf-8")
    shell_source = shell_path.read_text(encoding="utf-8")

    boot = PebbleInterpreter(
        shell.fs.root,
        input_provider=input,
        output_consumer=print,
        path_resolver=shell.fs.resolve_path,
    )
    try:
        boot.execute(
            runtime_source + "\nboot()\n",
            initial_globals={
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell_source,
            },
        )
    except PebbleError as exc:
        print(f"[boot error] {exc}")

    shell.cmdloop()

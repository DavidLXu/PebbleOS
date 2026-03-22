import argparse
import sys
from pathlib import Path

from pebble_bootloader.lang import PebbleError
from pebble_bootloader.shell import VALID_FS_MODES, build_shell


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fs-mode",
        default="hostfs",
        choices=sorted(VALID_FS_MODES),
        help="filesystem backend mode",
    )
    parser.add_argument(
        "--allow-system-writes",
        action="store_true",
        help="allow modifications to mounted system files",
    )
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="retry HTTPS requests without certificate verification after a TLS validation failure",
    )
    args = parser.parse_args()

    shell = build_shell(
        fs_mode=args.fs_mode,
        allow_system_writes=args.allow_system_writes,
        allow_insecure_tls=args.insecure_tls,
    )
    runtime_path = shell.fs.resolve_path("system/runtime.peb")
    shell_path = shell.fs.resolve_path("system/shell.peb")
    runtime_source = runtime_path.read_text(encoding="utf-8")
    shell_source = shell_path.read_text(encoding="utf-8")

    boot = shell._make_runtime(consume_output=True)
    try:
        boot.execute(
            runtime_source + "\nboot()\n",
            initial_globals={
                "SYSTEM_RUNTIME_PATH": "system/runtime.peb",
                "SYSTEM_SHELL_PATH": "system/shell.peb",
                "SYSTEM_SHELL_SOURCE": shell_source,
                "FS_MODE": shell.fs_mode,
            },
        )
    except PebbleError as exc:
        print(f"[boot error] {exc}")
        sys.exit(1)

    shell.cmdloop()

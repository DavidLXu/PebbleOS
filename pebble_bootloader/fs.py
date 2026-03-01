from __future__ import annotations

from datetime import datetime
from pathlib import Path


class FileSystemError(Exception):
    """Raised when a filesystem operation cannot be completed."""


class FlatFileSystem:
    """A filesystem rooted at one folder, with optional mounted subtrees."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.mounts: dict[str, Path] = {}

    def mount(self, alias: str, host_path: Path) -> None:
        cleaned = alias.strip().strip("/")
        if not cleaned or "/" in cleaned or "\\" in cleaned:
            raise FileSystemError("mount alias must be a single path segment")
        if not host_path.exists() or not host_path.is_dir():
            raise FileSystemError(f"mount source '{host_path}' is not a directory")
        self.mounts[cleaned] = host_path.resolve()

    def list_files(self) -> list[str]:
        names = [path.relative_to(self.root).as_posix() for path in sorted(self.root.rglob("*")) if path.is_file()]
        for alias, host_root in self.mounts.items():
            for path in sorted(host_root.rglob("*")):
                if path.is_file():
                    relative = path.relative_to(host_root).as_posix()
                    names.append(f"{alias}/{relative}")
        return sorted(names)

    def create_file(self, name: str, content: str = "") -> None:
        path = self.resolve_path(name)
        if path.exists():
            raise FileSystemError(f"file '{name}' already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def modify_file(self, name: str, content: str) -> None:
        path = self.resolve_path(name)
        if not path.exists():
            raise FileSystemError(f"file '{name}' does not exist")
        path.write_text(content, encoding="utf-8")

    def delete_file(self, name: str) -> None:
        path = self.resolve_path(name)
        if not path.exists():
            raise FileSystemError(f"file '{name}' does not exist")
        path.unlink()

    def read_file(self, name: str) -> str:
        path = self.resolve_path(name)
        if not path.exists():
            raise FileSystemError(f"file '{name}' does not exist")
        return path.read_text(encoding="utf-8")

    def file_time(self, name: str) -> str:
        path = self.resolve_path(name)
        if not path.exists():
            raise FileSystemError(f"file '{name}' does not exist")
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d, %H:%M:%S")

    def resolve_path(self, name: str) -> Path:
        cleaned = name.strip()
        if not cleaned:
            raise FileSystemError("file name cannot be empty")
        if "\\" in cleaned or cleaned in {".", ".."}:
            raise FileSystemError("invalid file name")

        if "/" in cleaned:
            mount_name, remainder = cleaned.split("/", 1)
            if mount_name in self.mounts:
                if not remainder or remainder.startswith("/") or any(part in {"", ".", ".."} for part in remainder.split("/")):
                    raise FileSystemError("invalid mounted file path")
                host_root = self.mounts[mount_name]
                path = (host_root / remainder).resolve()
                try:
                    path.relative_to(host_root)
                except ValueError as exc:
                    raise FileSystemError("mounted file path escapes its mount root") from exc
                return path
            if cleaned.startswith("/") or any(part in {"", ".", ".."} for part in cleaned.split("/")):
                raise FileSystemError("invalid file path")
            path = (self.root / cleaned).resolve()
            try:
                path.relative_to(self.root.resolve())
            except ValueError as exc:
                raise FileSystemError("file path escapes the Pebble OS root") from exc
            return path

        return self.root / cleaned

    def _resolve(self, name: str) -> Path:
        return self.resolve_path(name)

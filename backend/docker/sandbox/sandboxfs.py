#!/usr/bin/env python3
"""File primitives inside a sandbox container.

Invoked by the host runtime through `docker exec` with plain argv: no shell,
no host environment, no network. Paths are confined to /workspace twice —
lexically (no absolute paths, no parent hops) and by resolving symlinks —
so nothing outside the workspace can be named or reached.
"""

import sys
from pathlib import Path

WORKSPACE = Path("/workspace")
MAX_LIST = 200            # entries surfaced to the agent
MAX_READ_BYTES = 1_000_000  # hard byte cap on a file pulled out of the container

EXIT_OK = 0
EXIT_MISSING = 1
EXIT_NOT_FILE = 2
EXIT_ERROR = 3
EXIT_USAGE = 64


def confined(name: str) -> Path:
    if not name or "\x00" in name:
        raise ValueError(f"invalid sandbox path: {name!r}")
    if name.startswith(("/", "\\")):
        raise ValueError(f"path escapes the sandbox: {name}")
    if ".." in name.split("/"):
        raise ValueError(f"path escapes the sandbox: {name}")
    path = WORKSPACE.joinpath(*name.split("/")).resolve()
    if path != WORKSPACE and WORKSPACE not in path.parents:
        raise ValueError(f"path escapes the sandbox: {name}")
    return path


def list_files() -> int:
    rels = []
    for path in WORKSPACE.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            rel = path.relative_to(WORKSPACE)
        except ValueError:
            continue
        rels.append(str(rel))
    rels = sorted(rels)[:MAX_LIST]
    sys.stdout.write("\n".join(rels) + ("\n" if rels else ""))
    return EXIT_OK


def read(name: str) -> int:
    path = confined(name)
    try:
        data = path.read_bytes()[:MAX_READ_BYTES]
    except FileNotFoundError:
        sys.stderr.write(f"no such sandbox file: {name}")
        return EXIT_MISSING
    except IsADirectoryError:
        sys.stderr.write(f"not a file: {name}")
        return EXIT_NOT_FILE
    except OSError as error:
        sys.stderr.write(f"read failed: {error}")
        return EXIT_ERROR
    sys.stdout.buffer.write(data)
    return EXIT_OK


def write(name: str) -> int:
    path = confined(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(sys.stdin.buffer.read())
    except OSError as error:
        sys.stderr.write(f"write failed: {error}")
        return EXIT_ERROR
    return EXIT_OK


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "list":
        return list_files()
    if command == "read":
        if len(argv) < 3:
            return EXIT_USAGE
        return read(argv[2])
    if command == "write":
        if len(argv) < 3:
            return EXIT_USAGE
        return write(argv[2])
    sys.stderr.write(f"unknown subcommand: {command!r}")
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv))

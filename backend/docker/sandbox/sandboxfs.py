#!/usr/bin/env python3
"""File primitives inside a sandbox container.

Invoked by the host runtime through `docker exec` with plain argv: no shell,
no host environment, no network. Paths are confined to /workspace twice —
lexically (no absolute paths, no parent hops) and by resolving symlinks —
so nothing outside the workspace can be named or reached.
"""

import fnmatch
import json
import os
import re
import signal
import sys
from pathlib import Path

WORKSPACE = Path("/workspace")
MAX_LIST = 200            # entries surfaced to the agent
MAX_READ_BYTES = 1_000_000  # hard byte cap on a file pulled out of the container
MAX_SCAN_ENTRIES = 2000
MAX_SEARCH_FILES = 200
MAX_SEARCH_BYTES = 2_000_000
MAX_FILE_BYTES = 256_000
MAX_RANGE = 20_000
MAX_MATCHES = 100
MAX_SEARCH_OUTPUT = 16000

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
    # Legacy callers still get plain file names, scoped to the current directory.
    rels = [entry["path"] for entry in list_entries(".", 0, MAX_LIST)["entries"]
            if entry["kind"] == "file"]
    sys.stdout.write("\n".join(rels) + ("\n" if rels else ""))
    return EXIT_OK


def read(name: str) -> int:
    path = confined(name)
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_READ_BYTES)
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


def bounded_int(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def list_entries(name=".", offset=0, limit=100):
    bounded_int(offset, "offset", 0, MAX_SCAN_ENTRIES)
    bounded_int(limit, "limit", 1, MAX_LIST)
    root = confined(name)
    entries = []
    scan_truncated = False
    with os.scandir(root) as iterator:
        for index, entry in enumerate(iterator):
            if index >= MAX_SCAN_ENTRIES:
                scan_truncated = True
                break
            if entry.is_symlink():
                continue
            kind = "directory" if entry.is_dir(follow_symlinks=False) else "file"
            if kind == "file" and not entry.is_file(follow_symlinks=False):
                continue
            entries.append({"path": str(Path(entry.path).relative_to(WORKSPACE)), "kind": kind})
    entries.sort(key=lambda entry: entry["path"])
    page = entries[offset:offset + limit]
    more = offset + len(page) < len(entries)
    return {"entries": page, "path": name, "offset": offset, "limit": limit,
            "truncated": more or scan_truncated, "scan_truncated": scan_truncated,
            "next_offset": offset + len(page) if more else None}


def read_range(name, offset=0, limit=200):
    bounded_int(offset, "offset", 0, 100_000)
    bounded_int(limit, "limit", 1, 1000)
    path = confined(name)
    lines, scanned, line_number, chars = [], 0, 0, 0
    truncated = False
    line_truncated = False
    with path.open("rb") as handle:
        while len(lines) < limit and scanned < MAX_READ_BYTES:
            data = handle.readline(MAX_READ_BYTES - scanned)
            if not data:
                break
            scanned += len(data)
            if line_number >= offset:
                text = data.decode("utf-8", errors="replace")
                excerpt = text[:MAX_RANGE - chars]
                lines.append(excerpt)
                chars += len(excerpt)
                if len(excerpt) < len(text):
                    truncated = True
                    line_truncated = True
                    break
            line_number += 1
        truncated |= bool(handle.read(1))
    return {"path": name, "offset": offset, "limit": limit, "unit": "lines",
            "content": "".join(lines), "returned_lines": len(lines),
            "scanned_bytes": scanned, "truncated": truncated,
            "line_truncated": line_truncated,
            "next_offset": offset + len(lines) if truncated and lines and not line_truncated else None}


class SearchTimeout(Exception):
    pass


def search_files(name=".", pattern="", glob="*", limit=20):
    """Bound traversal, bytes, matches, snippets and regex CPU independently."""
    bounded_int(limit, "limit", 1, MAX_MATCHES)
    if not isinstance(pattern, str) or not 1 <= len(pattern) <= 256:
        raise ValueError("pattern must contain 1..256 characters")
    if not isinstance(glob, str) or not 1 <= len(glob) <= 256:
        raise ValueError("glob must contain 1..256 characters")
    try:
        regex = re.compile(pattern)
    except re.error as error:
        return {"status": "error", "error": "invalid_regex", "reason": str(error),
                "matches": [], "truncated": False}
    root = confined(name)
    matches, pending = [], [root]
    entries = files = scanned_bytes = 0
    output_chars = 0
    truncated = False
    skipped = 0

    def deadline(_signum, _frame):
        raise SearchTimeout()

    previous = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        while pending:
            path = pending.pop()
            if path.is_symlink():
                skipped += 1
                continue
            if path.is_dir():
                with os.scandir(path) as iterator:
                    for entry in iterator:
                        entries += 1
                        if entries > MAX_SCAN_ENTRIES:
                            truncated = True
                            break
                        if not entry.is_symlink():
                            pending.append(Path(entry.path))
                if entries > MAX_SCAN_ENTRIES:
                    break
                continue
            if not path.is_file():
                continue
            rel = str(path.relative_to(WORKSPACE))
            scope_rel = str(path.relative_to(root)) if root.is_dir() else path.name
            if not (fnmatch.fnmatch(scope_rel, glob) or fnmatch.fnmatch(path.name, glob)):
                continue
            if files >= MAX_SEARCH_FILES or scanned_bytes >= MAX_SEARCH_BYTES:
                truncated = True
                break
            files += 1
            cap = min(MAX_FILE_BYTES, MAX_SEARCH_BYTES - scanned_bytes)
            try:
                with path.open("rb") as handle:
                    data = handle.read(cap)
                    file_truncated = bool(handle.read(1))
            except OSError:
                skipped += 1
                truncated = True
                continue
            scanned_bytes += len(data)
            truncated |= file_truncated
            if b"\x00" in data:
                skipped += 1
                continue
            for line_number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                match = regex.search(line)
                if match is None:
                    continue
                start = max(0, match.start() - 80)
                snippet = line[start:start + 240]
                output_chars += len(rel) + len(snippet) + 100
                if output_chars > MAX_SEARCH_OUTPUT:
                    truncated = True
                    break
                matches.append({"path": rel, "line": line_number,
                                "snippet": snippet,
                                "snippet_truncated": len(line) > 240})
                if len(matches) >= limit:
                    truncated = True
                    break
            if len(matches) >= limit or output_chars > MAX_SEARCH_OUTPUT:
                break
    except SearchTimeout:
        return {"status": "error", "error": "search_timeout", "reason": "search exceeded 2 seconds",
                "matches": matches, "truncated": True, "scanned_files": files,
                "scanned_bytes": scanned_bytes}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    return {"status": "ready", "matches": matches, "truncated": truncated,
            "scanned_files": files, "scanned_bytes": scanned_bytes, "skipped_files": skipped}


def structured(command, payload):
    try:
        args = json.loads(payload)
        if not isinstance(args, dict):
            raise TypeError("arguments must be an object")
        result = {"list-page": list_entries, "read-range": read_range,
                  "search": search_files}[command](**args)
    except (OSError, ValueError, TypeError) as error:
        result = {"status": "error", "error": type(error).__name__,
                  "reason": str(error), "truncated": False}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command in {"list-page", "read-range", "search"}:
        return structured(command, argv[2] if len(argv) > 2 else "{}")
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

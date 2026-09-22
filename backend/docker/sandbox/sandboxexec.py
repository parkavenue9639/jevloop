#!/usr/bin/env python3
"""Run one stdin-provided Bash script in its own process group with a deadline."""

import argparse
import os
import signal
import subprocess
import sys
import time


def _stop_group(pid: int, grace: float) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(prog="sandboxexec")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--kill-after", type=float, default=5.0)
    args = parser.parse_args()
    script = sys.stdin.buffer.read()
    if not script.strip():
        print("empty command", file=sys.stderr)
        return 2

    process = subprocess.Popen(
        ["/bin/bash", "-s"],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdin is not None
    try:
        process.stdin.write(script)
        process.stdin.close()
    except BrokenPipeError:
        pass

    try:
        return process.wait(timeout=max(0.1, args.timeout))
    except subprocess.TimeoutExpired:
        _stop_group(process.pid, max(0.1, args.kill_after))
        try:
            process.wait(timeout=max(0.1, args.kill_after))
        except subprocess.TimeoutExpired:
            pass
        print(f"command timed out after {args.timeout:g}s", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())

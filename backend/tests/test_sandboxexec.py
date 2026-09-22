"""The lightweight command helper scopes source and timeout to one process group."""

import subprocess
import sys
import time
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "docker" / "sandbox" / "sandboxexec.py"


def run_helper(script: str, *args: str):
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        input=script,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def test_script_source_is_absent_from_shell_and_wrapper_cmdlines():
    sentinel = "JEVLOOP_SELF_MATCH_SENTINEL_7f39"
    result = run_helper(
        f"if grep -azq {sentinel} /proc/$$/cmdline /proc/$PPID/cmdline; "
        "then exit 9; fi; printf safe"
    )

    assert result.returncode == 0
    assert result.stdout == "safe"


def test_timeout_reaps_the_whole_command_process_group(tmp_path):
    marker = tmp_path / "marker"
    result = run_helper(
        "trap '' TERM; "
        f"while :; do printf x >> {marker}; sleep 0.02; done",
        "--timeout", "0.2",
        "--kill-after", "0.1",
    )

    assert result.returncode == 124
    assert "command timed out after 0.2s" in result.stderr
    size = marker.stat().st_size
    time.sleep(0.15)
    assert marker.stat().st_size == size

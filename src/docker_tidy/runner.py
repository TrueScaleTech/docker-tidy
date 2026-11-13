"""The only module that starts a process.

Every command is a list of arguments. Nothing is ever built as a string and handed to
a shell, so a container or image name can never turn into a command.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from typing import Protocol


class DockerError(Exception):
    """Docker could not be run, or refused a command. The message is fit to show."""


class Runner(Protocol):
    def run(self, args: Sequence[str]) -> str:
        """Run `docker <args>` and return what it printed on standard output."""
        ...


class SubprocessRunner:
    """Runs the real `docker` command."""

    def __init__(self, executable: str = "docker", timeout: float = 120.0) -> None:
        self._executable = executable
        self._timeout = timeout

    def run(self, args: Sequence[str]) -> str:
        path = shutil.which(self._executable)
        if path is None:
            msg = f"Could not find the '{self._executable}' command. Is Docker installed?"
            raise DockerError(msg)
        try:
            done = subprocess.run(
                [path, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"'docker {args[0]}' did not answer within {self._timeout:.0f} seconds."
            raise DockerError(msg) from exc
        except OSError as exc:
            msg = f"Could not start '{path}': {exc}"
            raise DockerError(msg) from exc
        if done.returncode != 0:
            raise DockerError(_clean_error(done.stderr) or f"'docker {args[0]}' failed.")
        return done.stdout


def _clean_error(stderr: str) -> str:
    """Keep the useful part of docker's complaint, without its "Error response" noise."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""
    text = " ".join(lines)
    prefix = "Error response from daemon: "
    return text.removeprefix(prefix)

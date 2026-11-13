import sys

import pytest

from docker_tidy.runner import DockerError, SubprocessRunner, _clean_error


def test_missing_command_is_a_clear_error() -> None:
    runner = SubprocessRunner(executable="docker-tidy-no-such-command")
    with pytest.raises(DockerError, match="Could not find"):
        runner.run(["version"])


def test_output_of_a_successful_command_is_returned() -> None:
    # The Python interpreter stands in for docker: any program that prints will do.
    runner = SubprocessRunner(executable=sys.executable)
    assert runner.run(["-c", "print('hello')"]).strip() == "hello"


def test_failure_carries_the_error_text() -> None:
    runner = SubprocessRunner(executable=sys.executable)
    with pytest.raises(DockerError, match="boom"):
        runner.run(["-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])


def test_failure_without_text_still_has_a_message() -> None:
    runner = SubprocessRunner(executable=sys.executable)
    with pytest.raises(DockerError, match="failed"):
        runner.run(["-c", "import sys; sys.exit(3)"])


def test_timeout_is_reported() -> None:
    runner = SubprocessRunner(executable=sys.executable, timeout=0.2)
    with pytest.raises(DockerError, match="did not answer"):
        runner.run(["-c", "import time; time.sleep(5)"])


def test_arguments_are_never_interpreted_by_a_shell() -> None:
    runner = SubprocessRunner(executable=sys.executable)
    out = runner.run(["-c", "import sys; print(sys.argv[1])", "a b; echo hacked && $(x)"])
    assert out.strip() == "a b; echo hacked && $(x)"


def test_daemon_noise_is_stripped_from_errors() -> None:
    raw = "Error response from daemon: conflict: unable to remove repository reference\n"
    assert _clean_error(raw) == "conflict: unable to remove repository reference"
    assert _clean_error("") == ""

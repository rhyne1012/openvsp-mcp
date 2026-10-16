"""SSH transport quoting and stdio forwarding, without network access."""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/launch_ssh.py"
spec = importlib.util.spec_from_file_location("launch_ssh", SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_remote_command_is_one_quoted_shell_command():
    path = "/home/user/a directory/launch'$(touch BAD).sh"
    args = ["--health", "value with spaces", "$(touch BAD); echo bad"]
    argv = launcher.command("user@host", path, arguments=args)
    assert shlex.split(argv[-1]) == ["exec", path, *args]
    assert argv[-3:-1] == ["--", "user@host"]
    assert "StrictHostKeyChecking=yes" in argv and "BatchMode=yes" in argv
    assert "-T" in argv


@pytest.mark.parametrize(
    "host,path", [("-oProxyCommand=bad", "/ok"), ("x y", "/ok"), ("", "/ok"), ("host", "relative")]
)
def test_invalid_destination_is_rejected(host, path):
    with pytest.raises(ValueError):
        launcher.command(host, path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX launcher")
def test_launcher_forwards_stdio_without_banners(tmp_path):
    fake = tmp_path / "ssh"
    fake.write_text(
        f"#!{sys.executable}\nimport sys,json\n"
        "print(json.dumps(sys.argv[1:]), file=sys.stderr)\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
    )
    fake.chmod(0o755)
    payload = '{"jsonrpc":"2.0","method":"ping","id":1}\n'
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--host",
            "user@host",
            "--launcher",
            "/remote/launch",
            "--identity",
            str(tmp_path / "key with spaces"),
            "--",
            "--health",
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
        env=dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"]),
    )
    assert result.stdout == payload
    argv = json.loads(result.stderr)
    assert argv[argv.index("-i") + 1] == str(tmp_path / "key with spaces")
    assert shlex.split(argv[-1]) == ["exec", "/remote/launch", "--health"]

"""Forward MCP stdio through a verified, noninteractive OpenSSH connection."""

import argparse
import os
import shlex


def command(host, launcher, identity=None, arguments=()):
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        raise ValueError("host must be a hostname, SSH alias or user@host")
    if not launcher.startswith("/"):
        raise ValueError("remote launcher must be an absolute POSIX path")
    result = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]
    if identity:
        result += ["-i", os.path.expanduser(identity), "-o", "IdentitiesOnly=yes"]
    # OpenSSH sends a command string to the remote login shell, not an argv array.
    result += ["--", host, "exec " + shlex.join([launcher, *arguments])]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--launcher", required=True)
    parser.add_argument(
        "--identity", help="Optional local private-key path; key contents are not read"
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    try:
        argv = command(args.host, args.launcher, args.identity, arguments)
    except ValueError as exc:
        parser.error(str(exc))
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()

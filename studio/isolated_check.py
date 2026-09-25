"""Record a check child's wait status inside its namespace, before bwrap maps signals."""
import json
import os
import subprocess
import sys


def main():
    status_path, arguments = sys.argv[1:]
    child = subprocess.run(json.loads(arguments), close_fds=True)
    fd = os.open(status_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    with os.fdopen(fd, "w") as stream:
        json.dump({"exit_code": child.returncode}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return child.returncode if child.returncode >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

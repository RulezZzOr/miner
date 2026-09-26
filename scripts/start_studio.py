"""Shared setup check and launcher. Never replace an existing user configuration."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure(root: Path) -> bool:
    """Create a first-run config exclusively; concurrent setup cannot overwrite it."""
    example = (root / "agent.example.toml").read_bytes()
    try:
        with (root / "agent.toml").open("xb") as stream:
            stream.write(example)
    except FileExistsError:
        return False
    return True


def main() -> None:
    os.umask(0o077)
    if sys.platform not in {"darwin", "linux"}:
        raise SystemExit("Windows requires WSL2. Use Switch Studio Windows.cmd.")
    sys.path.insert(0, str(ROOT))
    try:
        # Check real runtime imports before creating any configuration.
        import apodex.switch_cli  # noqa: F401

        import studio.server
    except ImportError as exc:
        raise SystemExit(f"Runtime is incomplete: {exc}. Run ./setup-studio.") from exc
    arguments = sys.argv[1:]
    # Help has no side effects: it never creates agent.toml.
    if not {"-h", "--help"} & set(arguments) and configure(ROOT):
        print("Created agent.toml. In Models, enter your server URL and model name.", flush=True)
    if arguments == ["--check"]:
        print(f"Runtime OK: {sys.platform}, Python {sys.version.split()[0]}")
        return
    # Run the package module in this process. Executing studio/server.py as a script would
    # import it twice, and errors raised through the second copy would bypass the handler.
    sys.argv = ["switch-studio", *arguments]
    raise SystemExit(studio.server.main())


if __name__ == "__main__":
    main()

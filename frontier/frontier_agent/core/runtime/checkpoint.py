"""Durable checkpoints; failure is fatal to work that depends on resuming them."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path


class CheckpointError(RuntimeError):
    """The last completed turn could not be durably recorded."""


def write_checkpoint(path: str | Path, data: dict) -> None:
    target = Path(path)
    temporary = None
    try:
        # Serialize before opening anything, preserving the previous checkpoint.
        payload = json.dumps(data, ensure_ascii=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        if os.name != "nt":
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except Exception as exc:
        raise CheckpointError(f"Checkpoint could not be saved to {target}: {exc}") from exc
    finally:
        if temporary is not None:
            with suppress(OSError):
                os.unlink(temporary)

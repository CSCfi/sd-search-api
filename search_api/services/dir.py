"""Directory functions."""

import re
from pathlib import Path
import fsspec  # type: ignore


def list_directories(
    root: str = "/",
    fs: fsspec.AbstractFileSystem | None = None,
    pattern: str | None = None,
) -> list[str]:
    """
    List directories under a root path using fsspec. Optionally filter by regex.

    :param root: Root directory or bucket path.
    :param fs: Optional fsspec filesystem. If None, a local filesystem is used.
    :param pattern: Optional regex pattern to filter directory names.
    :return: List of directory paths matching the pattern.
    """
    if fs is None:
        # Use local filesystem.
        fs = fsspec.filesystem("file")

    # Ensure root exists
    if not fs.exists(root):
        raise FileNotFoundError(f"Root path '{root}' does not exist")

    regex = re.compile(pattern) if pattern else None

    dirs = []
    for path in fs.ls(root, detail=True):
        if path["type"] == "directory":
            dir_name = Path(path["name"]).name
            if regex and not regex.search(dir_name):
                continue
            dirs.append(path["name"])

    return dirs

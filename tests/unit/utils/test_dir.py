import pytest
import fsspec
from pathlib import Path

from search_api.utils.dir import list_directories


@pytest.fixture
def test_local_dirs(tmp_path: Path):
    """
    Create local test directories:
    /A/
    /A/C/
    /B/
    /file.txt
    """
    (tmp_path / "A").mkdir()
    (tmp_path / "A" / "C").mkdir()
    (tmp_path / "B").mkdir()
    (tmp_path / "file.txt").write_text("test")

    fs = fsspec.filesystem("file")
    return tmp_path, fs


def test_list_directories(test_local_dirs):
    root, fs = test_local_dirs
    dirs = list_directories(str(root), fs=fs)
    assert sorted(dirs) == sorted([str(root / "A"), str(root / "B")])


def test_list_directories_with_regex(test_local_dirs):
    root, fs = test_local_dirs
    dirs = list_directories(str(root), fs=fs, pattern="^A")
    assert dirs == [str(root / "A")]


def test_list_directories_no_match(test_local_dirs):
    root, fs = test_local_dirs
    assert list_directories(str(root), fs=fs, pattern="^Z") == []

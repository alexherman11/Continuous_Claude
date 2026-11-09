import os

from path_safety import is_within
from file_manager import FileManager


def test_inside_and_equal(tmp_path):
    base = tmp_path / "scratchpad"
    base.mkdir()
    assert is_within(base, base)
    assert is_within(base, base / "notes" / "a.md")


def test_sibling_prefix_is_not_inside(tmp_path):
    base = tmp_path / "scratchpad"
    evil = tmp_path / "scratchpad_evil"
    base.mkdir()
    evil.mkdir()
    # The old startswith() check accepted this.
    assert str(evil / "x").startswith(str(base))
    assert not is_within(base, evil / "x")


def test_parent_traversal_is_not_inside(tmp_path):
    base = tmp_path / "scratchpad"
    base.mkdir()
    assert not is_within(base, base / ".." / "other")


def test_symlink_escape_is_not_inside(tmp_path):
    base = tmp_path / "scratchpad"
    evil = tmp_path / "scratchpad_evil"
    base.mkdir()
    evil.mkdir()
    os.symlink(evil, base / "link")
    assert not is_within(base, base / "link" / "x.md")


def test_file_manager_rejects_symlink_to_sibling(tmp_path):
    fm = FileManager(base_path=str(tmp_path))
    evil = tmp_path / "scratchpad_evil"
    evil.mkdir()
    os.symlink(evil, os.path.join(fm.scratchpad_dir, "link"))

    result = fm.write_scratchpad("link/pwned.md", "x")
    assert "error" in result
    assert not (evil / "pwned.md").exists()

    ok = fm.write_scratchpad("notes/fine.md", "hello")
    assert ok["status"] == "ok"
    assert fm.read_file("scratchpad", "notes/fine.md")["content"] == "hello"

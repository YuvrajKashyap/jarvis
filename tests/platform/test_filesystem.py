from pathlib import Path

import pytest

from jarvis.platform.filesystem import LocalFileStore


def test_file_store_reads_bounded_chunks_only_inside_allowed_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    document = allowed / "notes.txt"
    document.write_text("0123456789", encoding="utf-8")
    files = LocalFileStore(roots=(allowed,), undo_directory=tmp_path / "undo")

    chunk = files.read_text(document, offset=3, limit=4)

    assert chunk.content == "3456"
    assert chunk.next_offset == 7
    assert chunk.truncated is True
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    with pytest.raises(PermissionError, match="allowed roots"):
        files.read_text(outside, offset=0, limit=100)


def test_write_is_atomic_reversible_and_undo_itself_is_reversible(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    document = allowed / "notes.txt"
    document.write_text("before", encoding="utf-8")
    files = LocalFileStore(roots=(allowed,), undo_directory=tmp_path / "undo")

    changed = files.write_text(document, "after")
    restored = files.undo(changed.undo_reference)

    assert changed.created is False
    assert document.read_text(encoding="utf-8") == "before"
    assert restored.path == str(document.resolve())
    files.undo(restored.undo_reference)
    assert document.read_text(encoding="utf-8") == "after"


def test_new_file_can_be_undone_without_leaving_temporary_files(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "new.txt"
    files = LocalFileStore(roots=(allowed,), undo_directory=tmp_path / "undo")

    changed = files.write_text(target, "new content")
    files.undo(changed.undo_reference)

    assert changed.created is True
    assert not target.exists()
    assert list(allowed.glob(".jarvis-*.tmp")) == []

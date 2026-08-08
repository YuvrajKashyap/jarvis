import base64
import json
import os
import threading
from pathlib import Path
from uuid import UUID, uuid4

from jarvis.agency.files import FileMutation, FileText


class LocalFileStore:
    """Constrained, UTF-8-only local file access with durable inverse mutations."""

    def __init__(
        self,
        *,
        roots: tuple[Path, ...],
        undo_directory: Path,
        maximum_file_bytes: int = 1_000_000,
    ) -> None:
        if not roots:
            raise ValueError("at least one allowed file root is required")
        if maximum_file_bytes < 1_024 or maximum_file_bytes > 100_000_000:
            raise ValueError("maximum file size must be between 1024 and 100000000 bytes")
        self._roots = tuple(root.resolve(strict=True) for root in roots)
        if any(not root.is_dir() for root in self._roots):
            raise ValueError("allowed file roots must be directories")
        self._undo_directory = undo_directory.resolve()
        self._undo_directory.mkdir(parents=True, exist_ok=True)
        self._maximum_file_bytes = maximum_file_bytes
        self._lock = threading.RLock()

    def read_text(self, path: Path, *, offset: int, limit: int) -> FileText:
        if offset < 0:
            raise ValueError("file offset cannot be negative")
        if limit < 1 or limit > 32_000:
            raise ValueError("file read limit must be between 1 and 32000 characters")
        target = self._resolve_existing_file(path)
        if target.stat().st_size > self._maximum_file_bytes:
            raise ValueError("file exceeds the configured read limit")
        content = target.read_text(encoding="utf-8")
        chunk = content[offset : offset + limit]
        next_offset = offset + len(chunk)
        return FileText(
            path=str(target),
            content=chunk,
            next_offset=next_offset,
            truncated=next_offset < len(content),
        )

    def write_text(self, path: Path, content: str) -> FileMutation:
        encoded = content.encode("utf-8")
        if len(encoded) > self._maximum_file_bytes:
            raise ValueError("file content exceeds the configured write limit")
        with self._lock:
            target = self._resolve_write_target(path)
            return self._mutate(target, encoded)

    def undo(self, undo_reference: str) -> FileMutation:
        try:
            reference = UUID(undo_reference)
        except ValueError as error:
            raise ValueError("undo reference is invalid") from error
        record_path = self._undo_directory / f"{reference}.json"
        with self._lock:
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                raw_path = record["path"]
                prior_exists = record["prior_exists"]
                prior_content = record["prior_content"]
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
                raise LookupError("undo reference was not found") from error
            if (
                not isinstance(raw_path, str)
                or not isinstance(prior_exists, bool)
                or not isinstance(prior_content, str)
            ):
                raise LookupError("undo record is malformed")
            target = self._resolve_write_target(Path(raw_path))
            try:
                content = base64.b64decode(prior_content, validate=True) if prior_exists else None
            except ValueError as error:
                raise LookupError("undo record is malformed") from error
            inverse = self._mutate(target, content)
            record_path.unlink()
            return inverse

    def _mutate(self, target: Path, content: bytes | None) -> FileMutation:
        existed = target.exists()
        if existed:
            if not target.is_file() or target.is_symlink():
                raise PermissionError("only regular files can be changed")
            if target.stat().st_size > self._maximum_file_bytes:
                raise ValueError("existing file exceeds the reversible write limit")
            prior = target.read_bytes()
        else:
            prior = b""

        undo_reference = uuid4()
        undo_record = self._write_undo_record(
            undo_reference,
            target=target,
            prior_exists=existed,
            prior=prior,
        )
        try:
            if content is None:
                if target.exists():
                    target.unlink()
                bytes_written = 0
            else:
                temporary = target.parent / f".jarvis-{uuid4().hex}.tmp"
                try:
                    with temporary.open("xb") as output:
                        output.write(content)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                finally:
                    if temporary.exists():
                        temporary.unlink()
                bytes_written = len(content)
        except BaseException:
            undo_record.unlink(missing_ok=True)
            raise
        return FileMutation(
            path=str(target),
            created=not existed and content is not None,
            bytes_written=bytes_written,
            undo_reference=str(undo_reference),
        )

    def _write_undo_record(
        self,
        reference: UUID,
        *,
        target: Path,
        prior_exists: bool,
        prior: bytes,
    ) -> Path:
        destination = self._undo_directory / f"{reference}.json"
        temporary = self._undo_directory / f".{reference}.tmp"
        payload = json.dumps(
            {
                "path": str(target),
                "prior_exists": prior_exists,
                "prior_content": base64.b64encode(prior).decode("ascii"),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
        return destination

    def _resolve_existing_file(self, path: Path) -> Path:
        if not path.is_absolute():
            raise PermissionError("file paths must be absolute")
        try:
            target = path.resolve(strict=True)
        except OSError as error:
            raise FileNotFoundError("file was not found") from error
        self._require_allowed(target)
        if not target.is_file() or target.is_symlink():
            raise PermissionError("only regular files can be read")
        return target

    def _resolve_write_target(self, path: Path) -> Path:
        if not path.is_absolute():
            raise PermissionError("file paths must be absolute")
        try:
            parent = path.parent.resolve(strict=True)
        except OSError as error:
            raise FileNotFoundError("parent directory was not found") from error
        target = parent / path.name
        if target.exists() or target.is_symlink():
            target = target.resolve(strict=True)
        self._require_allowed(target)
        return target

    def _require_allowed(self, path: Path) -> None:
        if not any(path == root or root in path.parents for root in self._roots):
            raise PermissionError("file path is outside the allowed roots")

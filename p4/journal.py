"""Remediator receipt journal: append-only intent -> result records.

`Journal()` keeps records in memory. `Journal(path)` is durable: each record is one JSON line
appended to `path` and fsynced before `append` returns, then a small index (`<path>.idx`:
committed record count and byte length) is replaced by write-to-temp + fsync + atomic rename.
Reopening `Journal(path)` recovers every committed record: it trusts the indexed prefix,
accepts complete well-formed lines after it (a record fsynced just before a crash that beat
the index update), and truncates a torn tail.

A restarted runbook rebuilds its state from these records (runbooks/base.py).
"""
import json
import os
from pathlib import Path
from types import MappingProxyType

KINDS = ("intent", "result")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    def __init__(self, path=None, sync: bool = True) -> None:
        self._entries: list[MappingProxyType] = []
        self._path = None if path is None else Path(path)
        self._sync = sync
        self._size = 0
        if self._path is not None:
            self._recover()

    def append(self, kind: str, **fields) -> int:
        """Record `kind` ("intent" or "result") with `fields`; returns the journal index."""
        if kind not in KINDS:
            raise ValueError(f"unknown journal record kind {kind!r}")
        record = {"i": len(self._entries), "kind": kind, **fields}
        if self._path is not None:
            line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
            with self._path.open("ab") as fh:
                fh.write(line)
                fh.flush()
                if self._sync:
                    os.fsync(fh.fileno())
            self._size += len(line)
            self._entries.append(MappingProxyType(record))
            self._write_index()
        else:
            self._entries.append(MappingProxyType(record))
        return record["i"]

    @property
    def entries(self) -> tuple:
        return tuple(self._entries)

    # -- durability --------------------------------------------------------------------------
    @property
    def index_path(self) -> Path:
        return self._path.with_name(self._path.name + ".idx")

    def _write_index(self) -> None:
        tmp = self._path.with_name(self._path.name + ".idx.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump({"count": len(self._entries), "bytes": self._size}, fh)
            fh.flush()
            if self._sync:
                os.fsync(fh.fileno())
        os.replace(tmp, self.index_path)
        if self._sync:
            _fsync_dir(self._path.parent)

    def _recover(self) -> None:
        if not self._path.exists():
            self._path.touch()
            self._write_index()
            return
        data = self._path.read_bytes()
        indexed = 0
        if self.index_path.exists():
            idx = json.loads(self.index_path.read_text(encoding="utf-8"))
            indexed = idx["bytes"]
            if indexed > len(data):
                raise ValueError(f"{self._path}: index claims {indexed} bytes, file has {len(data)}")
        pos = 0
        while pos < len(data):
            end = data.find(b"\n", pos)
            if end < 0:
                break  # torn tail: the write never completed
            try:
                record = json.loads(data[pos:end])
            except ValueError:
                record = None
            if not (isinstance(record, dict) and record.get("i") == len(self._entries)
                    and record.get("kind") in KINDS):
                if end < indexed:
                    raise ValueError(f"{self._path}: committed record at byte {pos} is corrupt")
                break
            self._entries.append(MappingProxyType(record))
            pos = end + 1
        if pos < indexed:
            raise ValueError(f"{self._path}: committed records end at byte {indexed}, recovered {pos}")
        if pos < len(data):
            os.truncate(self._path, pos)
        self._size = pos
        self._write_index()

"""Remediator receipt journal: in-memory and durable (JSONL + fsync + atomic index) forms,
and recovery of committed records after a crash mid-write."""
import json

import pytest

from p4.journal import Journal


def fill(j, n=3):
    for i in range(n):
        j.append("intent", t=i, op="cancel", uid=f"P{i:03d}", key=f"K{i}")


def test_in_memory_journal_is_append_only():
    j = Journal()
    fill(j)
    assert [e["i"] for e in j.entries] == [0, 1, 2]
    with pytest.raises(TypeError):
        j.entries[0]["t"] = 9
    with pytest.raises(ValueError):
        j.append("note")


def test_durable_journal_survives_reopen(tmp_path):
    path = tmp_path / "j.jsonl"
    j = Journal(path)
    fill(j)
    j.append("result", t=4, op="cancel", uid="P000", key="K0", result="CANCELLED")
    again = Journal(path)
    assert [dict(e) for e in again.entries] == [dict(e) for e in j.entries]
    assert json.loads((tmp_path / "j.jsonl.idx").read_text()) == {"count": 4, "bytes": path.stat().st_size}
    again.append("intent", t=5, op="status", uid="P001", key=None)
    assert len(Journal(path).entries) == 5


def test_torn_tail_is_truncated(tmp_path):
    path = tmp_path / "j.jsonl"
    fill(Journal(path))
    good = path.read_bytes()
    with path.open("ab") as fh:
        fh.write(b'{"i":3,"kind":"intent","t":')  # crash in the middle of a write
    j = Journal(path)
    assert len(j.entries) == 3 and path.read_bytes() == good
    j.append("intent", t=9, op="cancel", uid="P009", key="K9")
    assert [e["i"] for e in Journal(path).entries] == [0, 1, 2, 3]


def test_record_fsynced_before_index_update_is_kept(tmp_path):
    path = tmp_path / "j.jsonl"
    fill(Journal(path))
    with path.open("ab") as fh:  # the line made it to disk, the index rename did not
        fh.write(b'{"i":3,"key":"K3","kind":"intent","op":"cancel","t":3,"uid":"P003"}\n')
    j = Journal(path)
    assert len(j.entries) == 4
    assert json.loads((tmp_path / "j.jsonl.idx").read_text())["count"] == 4


def test_out_of_sequence_tail_record_is_dropped(tmp_path):
    path = tmp_path / "j.jsonl"
    fill(Journal(path))
    with path.open("ab") as fh:
        fh.write(b'{"i":7,"kind":"intent"}\n')
    assert len(Journal(path).entries) == 3


def test_corrupt_committed_record_is_an_error(tmp_path):
    path = tmp_path / "j.jsonl"
    fill(Journal(path))
    data = bytearray(path.read_bytes())
    data[5] = ord("#")
    path.write_bytes(bytes(data))
    with pytest.raises(ValueError):
        Journal(path)

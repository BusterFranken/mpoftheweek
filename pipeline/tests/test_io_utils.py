import json

from pipeline import io_utils


def test_dumps_stable_sorted_keys():
    out = io_utils.dumps_stable({"b": 1, "a": {"z": 1, "y": 2}})
    assert out == '{\n  "a": {\n    "y": 2,\n    "z": 1\n  },\n  "b": 1\n}\n'


def test_dumps_records_one_line_per_record():
    records = [{"b": 1, "a": 2}, {"x": "é"}]
    out = io_utils.dumps_records(records)
    lines = out.splitlines()
    assert lines[0] == "["
    assert lines[1] == '{"a":2,"b":1},'
    assert lines[2] == '{"x":"é"}'
    assert lines[3] == "]"
    assert json.loads(out) == [{"a": 2, "b": 1}, {"x": "é"}]


def test_dumps_records_empty():
    assert io_utils.dumps_records([]) == "[]\n"


def test_write_and_read_roundtrip(tmp_path):
    path = tmp_path / "sub" / "x.json"
    io_utils.write_json(path, [{"a": 1}], records=True)
    assert io_utils.read_json(path) == [{"a": 1}]
    assert io_utils.read_json(tmp_path / "missing.json", default=[]) == []

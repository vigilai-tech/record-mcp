import json

import pytest
from conftest import example

from record_mcp.inputs import InputError, load_traces


def test_inline_object_list_and_string():
    doc = example("aml_triage.json")
    assert load_traces(doc, allow_files=False) == [doc]
    assert load_traces([doc, doc], allow_files=False) == [doc, doc]
    assert load_traces(json.dumps(doc), allow_files=False) == [doc]
    jsonl = "\n".join(json.dumps(d) for d in (doc, doc))
    assert len(load_traces(jsonl, allow_files=False)) == 2


def test_json_and_jsonl_files(tmp_path):
    doc = example("aml_triage.json")
    (tmp_path / "a.json").write_text(json.dumps([doc, doc]))
    (tmp_path / "b.jsonl").write_text(json.dumps(doc) + "\n\n" + json.dumps(doc) + "\n")
    assert len(load_traces(path=str(tmp_path / "a.json"), allow_files=True)) == 2
    assert len(load_traces(path=str(tmp_path / "b.jsonl"), allow_files=True)) == 2


def test_file_input_can_be_disabled(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    with pytest.raises(InputError, match="only accepted when record-mcp runs over stdio"):
        load_traces(path=str(tmp_path / "a.json"), allow_files=False)


@pytest.mark.parametrize("kwargs, message", [
    ({}, "exactly one"),
    ({"inline": {}, "path": "x.json"}, "exactly one"),
    ({"inline": "{not json"}, "not valid JSON"),
    ({"inline": []}, "No traces"),
    ({"inline": [1]}, "expected an object"),
    ({"path": "/definitely/missing.json"}, "File not found"),
])
def test_bad_input(kwargs, message):
    with pytest.raises(InputError, match=message):
        load_traces(allow_files=True, **kwargs)


def test_openinference_spans_are_adapted(engine):
    span = {
        "name": "aml-agent",
        "start_time": 1_779_000_000_000_000_000,
        "context": {"span_id": "abc123"},
        "attributes": {"input.value": "Triage alert 42", "output.value": "Close alert",
                       "llm.model_name": "claude-opus-5-5"},
    }
    [doc] = load_traces(span, input_format="openinference", allow_files=False)
    assert doc["prompt"] == "Triage alert 42" and doc["openinference_span_id"] == "abc123"
    ids = {v.rule_id for v in engine.check_trace(doc).violations}
    # The adapter's placeholder authority_basis must be flagged.
    assert "REC-INT-002" in ids

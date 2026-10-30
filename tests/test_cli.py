import json

import pytest
from conftest import EXAMPLES, example

from record_mcp.cli import main


def run(capsys, *argv) -> tuple[int, str]:
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    return exc.value.code, capsys.readouterr().out


def test_check_clean_example(capsys):
    code, out = run(capsys, "check", str(EXAMPLES / "aml_triage.json"))
    assert code == 0 and "Status: PASS" in out


def test_check_fails_on_threshold(capsys, tmp_path):
    doc = example("aml_triage.json")
    doc["data_at_decision"][0]["payload_hash"] = "nope"
    (tmp_path / "bad.json").write_text(json.dumps(doc))
    code, out = run(capsys, "check", str(tmp_path / "bad.json"), "--format", "json")
    assert code == 1
    assert json.loads(out)["violations"][0]["rule_id"] == "REC-PROV-003"
    code, _ = run(capsys, "check", str(tmp_path / "bad.json"), "--fail-on", "critical")
    assert code == 0


def test_check_chain(capsys):
    files = [str(EXAMPLES / n) for n in ("aml_triage.json", "trade_surveillance_l1.json")]
    code, out = run(capsys, "check", "--chain", "--format", "json", *files)
    assert code == 0 and json.loads(out)["trace_count"] == 2


def test_input_error_exit_code(capsys):
    code, _ = run(capsys, "check", "/definitely/missing.json")
    assert code == 2


def test_rules_markdown(capsys):
    main(["rules", "--format", "markdown", "--no-generated"])
    out = capsys.readouterr().out
    assert out.startswith("| Rule |") and "REC-CHAIN-006" in out and "GAP." not in out


def test_demo_chain_example(capsys):
    repo = EXAMPLES.parents[2]
    code, out = run(capsys, "check", "--chain", "--format", "json",
                    str(repo / "examples" / "aml_case_closed_without_review.jsonl"))
    report = json.loads(out)
    assert code == 1
    assert {v["rule_id"] for v in report["chain_violations"]} == {"REC-CHAIN-002", "REC-CHAIN-003"}


def test_readme_rule_table_is_current():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "update_readme", EXAMPLES.parents[2] / "scripts" / "update_readme.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.render(mod.README.read_text()) == mod.README.read_text()

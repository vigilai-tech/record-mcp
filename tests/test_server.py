"""End-to-end through the MCP protocol using the SDK's in-process Client."""

import json

import pytest
from conftest import EXAMPLES, example
from mcp import Client

from record_mcp.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def server(catalog):
    return build_server(allow_file_input=True, catalog=catalog)


async def test_tools_are_listed_with_schemas(server):
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"check_trace", "check_trace_chain", "list_rules", "get_rule",
                          "list_frameworks"}
    for tool in tools.values():
        assert tool.output_schema, tool.name
        assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint


async def test_check_trace_inline(server):
    doc = example("aml_triage.json")
    doc["final_action"]["escalation_reason"] = ""
    async with Client(server) as client:
        result = await client.call_tool("check_trace", {"trace": doc})
    assert not result.is_error
    report = result.structured_content
    assert report["status"] == "warn"
    assert [v["rule_id"] for v in report["violations"]] == ["REC-ESC-002"]
    assert report["violations"][0]["citations"][0]["framework"] == "eu_ai_act"


async def test_check_trace_path_over_stdio(server):
    async with Client(server) as client:
        result = await client.call_tool(
            "check_trace", {"path": str(EXAMPLES / "trade_surveillance_l1.json")})
    assert result.structured_content["record_id"] == "a3f8c2e1-7b4d-4f9a-b3e6-2d1c8a0f5e7b"


async def test_path_rejected_on_network_server(catalog):
    async with Client(build_server(allow_file_input=False, catalog=catalog)) as client:
        result = await client.call_tool("check_trace", {"path": str(EXAMPLES / "aml_triage.json")})
    assert result.is_error
    assert "only accepted when record-mcp runs over stdio" in result.content[0].text


async def test_check_trace_rejects_multiple(server):
    jsonl = "\n".join(json.dumps(example("aml_triage.json")) for _ in range(2))
    async with Client(server) as client:
        result = await client.call_tool("check_trace", {"trace": jsonl})
    assert result.is_error and "check_trace_chain" in result.content[0].text


async def test_check_trace_chain(server):
    a, b = example("aml_triage.json"), example("aml_triage.json")
    async with Client(server) as client:
        result = await client.call_tool("check_trace_chain", {"traces": [a, b]})
    report = result.structured_content
    assert report["trace_count"] == 2 and report["status"] == "fail"
    assert [v["rule_id"] for v in report["chain_violations"]] == ["REC-CHAIN-001"]


async def test_unknown_framework_is_tool_error(server):
    async with Client(server) as client:
        result = await client.call_tool(
            "check_trace", {"trace": example("aml_triage.json"), "frameworks": ["nope"]})
    assert result.is_error and "Unknown framework" in result.content[0].text


async def test_rule_tools_and_resources(server):
    async with Client(server) as client:
        chain_rules = (await client.call_tool("list_rules", {"scope": "chain"})).structured_content
        rule = (await client.call_tool("get_rule", {"rule_id": "REC-HO-001"})).structured_content
        missing = await client.call_tool("get_rule", {"rule_id": "NOPE"})
        frameworks = (await client.call_tool("list_frameworks", {})).structured_content
        schema = await client.read_resource("record://schema")
        rule_yaml = await client.read_resource("record://rules/REC-CHAIN-006")
        prompt = await client.get_prompt("audit_trace", {"chain": "yes"})
    assert chain_rules["count"] == 6
    assert rule["applies_to"]["operating_domains"] == ["supervised"]
    assert missing.is_error
    assert {f["id"] for f in frameworks["frameworks"]} >= {"eu_ai_act", "bsa_fincen"}
    assert '"title": "RECORD"' in schema.contents[0].text
    assert "1020.320(b)(3)" in rule_yaml.contents[0].text
    assert "check_trace_chain" in prompt.messages[0].content.text

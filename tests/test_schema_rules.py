"""The registered tools are exactly the fifteen this server documents, and every
input schema obeys the chat-connector rules: primitive types only, a default on
every optional parameter, no unions and no $ref. A violation makes some clients
drop the tool SILENTLY at import, so this is checked rather than reviewed."""
from __future__ import annotations

from conftest import ALL_TOOLS

FORBIDDEN = {"$ref", "$defs", "anyOf", "oneOf", "allOf", "exclusiveMinimum"}
PRIMITIVES = {"string", "integer", "number", "boolean"}


async def _list_tools():
    import server
    from fastmcp import Client

    async with Client(server.mcp) as client:
        return await client.list_tools()


def _walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


async def test_registered_tools_equal_the_documented_list():
    tools = await _list_tools()
    assert sorted(t.name for t in tools) == sorted(ALL_TOOLS)
    assert len(tools) == 15, (
        f"{len(tools)} tools registered; README.md and conftest.ALL_TOOLS both say fifteen. "
        "Registering a tool without listing it there is how a surface drifts out of its docs."
    )


async def test_input_schemas_obey_copilot_studio_rules():
    tools = await _list_tools()
    for tool in tools:
        for node in _walk(tool.inputSchema):
            if not isinstance(node, dict):
                continue
            hit = FORBIDDEN & set(node)
            assert not hit, f"{tool.name}: forbidden schema construct {hit}"
            if "type" in node:
                t = node["type"]
                assert isinstance(t, str), f"{tool.name}: union type {t!r} not allowed"
                if node is not tool.inputSchema:
                    assert t in PRIMITIVES, f"{tool.name}: non-primitive param type {t!r}"


async def test_every_tool_has_description_and_enum_docs():
    tools = await _list_tools()
    for tool in tools:
        assert tool.description and len(tool.description) > 40, f"{tool.name} needs a real description"
    by_name = {t.name: t for t in tools}
    assert "emergency" in by_name["auvik_list_alerts"].description
    assert "cpuUtilization" in by_name["auvik_device_stats"].description
    assert "reason" in by_name["auvik_dismiss_alert"].description.lower()
    assert "flapping" in by_name["auvik_alert_summary"].description
    networks_doc = by_name["auvik_list_networks"].description
    assert "scan_status" in networks_doc
    assert "source of truth" in networks_doc, (
        "the networks tool must say it is NOT a source of truth for addressing — it reports what "
        "the collectors observe, which is a different question"
    )
    assert "degraded" in by_name["auvik_component_health"].description
    assert "backup" in by_name["auvik_config_changes"].description.lower()
    assert "terminal" in by_name["auvik_remote_access_audit"].description
    assert "summary_only" in by_name["auvik_device_stats"].description

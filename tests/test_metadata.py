"""Metadata may improve while the published 0.6 input/output contract stays fixed."""

import asyncio
import hashlib
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from openvsp_mcp.tool import build_tool


def without_descriptions(value):
    if isinstance(value, dict):
        return {k: without_descriptions(v) for k, v in value.items() if k != "description"}
    if isinstance(value, list):
        return [without_descriptions(v) for v in value]
    return value


def contract_digest(schema):
    canonical = json.dumps(without_descriptions(schema), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def tool_catalog():
    app = FastMCP("metadata-regression")
    build_tool(app)
    return asyncio.run(app.list_tools())


def test_existing_tool_names_and_input_output_contracts_are_unchanged():
    # Captured before editing commit 42d792b8777c (0.6.0), not regenerated from 0.7.
    # Keep required/default/enum/bounds/extra-field rules, titles and nested refs;
    # strip only descriptions. Digests avoid duplicating shared schemas 16 times.
    baseline = json.loads(
        (Path(__file__).parent / "fixtures" / "tool_contract_0_6.json").read_text()
    )["tools"]
    tools = tool_catalog()
    assert len(tools) == len(baseline) == 16
    assert {tool.name for tool in tools} == set(baseline)
    for tool in tools:
        assert contract_digest(tool.inputSchema) == baseline[tool.name]["input_sha256"], tool.name
        assert contract_digest(tool.outputSchema) == baseline[tool.name]["output_sha256"], tool.name


def test_nested_parameters_and_existing_output_fields_are_documented():
    def check_fields(schema):
        if isinstance(schema, dict):
            for name, field in schema.get("properties", {}).items():
                assert field.get("description", "").strip(), name
            for value in schema.values():
                check_fields(value)
        elif isinstance(schema, list):
            for value in schema:
                check_fields(value)

    for tool in tool_catalog():
        assert tool.description.strip()
        check_fields(tool.inputSchema)
        # Dynamic dict outputs remain open; only already typed fields get docs.
        check_fields(tool.outputSchema)


def test_annotations_match_file_and_execution_boundaries():
    tools = {t.name.removeprefix("openvsp."): t for t in tool_catalog()}
    read_only = {"inspect", "read_results"}
    scripts = {"create_model", "modify", "preview", "preflight", "run_vspaero", "sweep"}
    destructive = scripts | {"set_parameters", "batch_cancel", "batch_resume"}
    for name, tool in tools.items():
        hints = tool.annotations
        assert hints is not None
        assert hints.readOnlyHint is (name in read_only)
        assert hints.openWorldHint is (name in scripts)
        if name not in read_only:
            assert hints.destructiveHint is (name in destructive)
            assert hints.idempotentHint is (name == "batch_status")

    # Resume inherits the shared selector but must not describe it as cancel-only.
    resume = json.dumps(tools["batch_resume"].inputSchema)
    assert "non-successful" in resume

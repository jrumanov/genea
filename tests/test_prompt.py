from genea.agent.prompt import MCP_INSTRUCTIONS, SYSTEM_PROMPT

SHARED = ("Always call find_person first", "Documented:", "Inferred:", "Answer in the language")


def test_shared_rules_are_in_both():
    for needle in SHARED:
        assert needle in SYSTEM_PROMPT
        assert needle in MCP_INSTRUCTIONS


def test_agent_prompt_keeps_write_rules():
    assert "propose_change" in SYSTEM_PROMPT
    assert "write_note" in SYSTEM_PROMPT


def test_mcp_instructions_mention_no_write_tools():
    for word in ("propose_change", "write_note", "genea changes"):
        assert word not in MCP_INSTRUCTIONS
    assert "read-only" in MCP_INSTRUCTIONS

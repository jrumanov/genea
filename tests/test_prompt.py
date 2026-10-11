from genea.agent.prompt import MCP_INSTRUCTIONS, SYSTEM_PROMPT

SHARED = ("Always call find_person first", "Documented:", "Inferred:", "Answer in the language")


def test_shared_rules_are_in_both():
    for needle in SHARED:
        assert needle in SYSTEM_PROMPT
        assert needle in MCP_INSTRUCTIONS


def test_agent_prompt_keeps_write_rules():
    assert "propose_change" in SYSTEM_PROMPT
    assert "write_note" in SYSTEM_PROMPT


def test_mcp_instructions_allow_only_analysis_writes():
    for word in ("propose_change", "write_note", "genea changes"):
        assert word not in MCP_INSTRUCTIONS
    assert ".genea/analyses/" in MCP_INSTRUCTIONS
    assert "Never claim that the family tree itself was changed" in MCP_INSTRUCTIONS


def test_summary_hits_are_navigation_not_evidence():
    for prompt in (SYSTEM_PROMPT, MCP_INSTRUCTIONS):
        assert 'kind "summary"' in prompt and "Never cite a summary" in prompt
        assert "@14:20]" in prompt  # how a moment in a recording is cited
    assert "\n6. Search hits" in SYSTEM_PROMPT
    assert "\n7. Search hits" in MCP_INSTRUCTIONS

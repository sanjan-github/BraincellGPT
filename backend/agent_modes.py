"""Built-in Roo-style modes and their server-enforced capability groups."""

MODES = {
    "code": {
        "label": "Code",
        "groups": frozenset({"read", "edit", "command", "workflow"}),
        "instructions": "Implement focused changes, inspect existing code first, and report what changed.",
    },
    "ask": {
        "label": "Ask",
        "groups": frozenset({"read", "workflow"}),
        "instructions": "Explain and investigate. Do not modify files or run commands.",
    },
    "architect": {
        "label": "Architect",
        "groups": frozenset({"read", "markdown_edit", "workflow"}),
        "instructions": "Analyze the project and produce a concrete plan. File changes are limited to Markdown documentation.",
    },
    "debug": {
        "label": "Debug",
        "groups": frozenset({"read", "edit", "command", "workflow"}),
        "instructions": "Diagnose systematically, gather evidence, identify the cause, then make a focused fix when permitted.",
    },
    "orchestrator": {
        "label": "Orchestrator",
        "groups": frozenset({"read", "workflow"}),
        "instructions": "Break the request into a short ordered plan and coordinate work through explicit follow-up tasks.",
    },
}


def normalize_mode(mode: str | None) -> str:
    key = mode.strip().lower() if isinstance(mode, str) else "code"
    if key not in MODES:
        raise ValueError("Choose a supported agent mode: code, ask, architect, debug, or orchestrator.")
    return key

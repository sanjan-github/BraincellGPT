from app.services.personality import build_system_instruction


def test_system_instruction_mentions_action_honesty():
    prompt = build_system_instruction("normal", True)
    assert "never claim to have performed an action" in prompt


def test_memory_disabled_does_not_claim_past_session_access():
    prompt = build_system_instruction("professional", False)
    assert "Do not infer or claim access to past sessions" in prompt


def test_memory_enabled_uses_history_context():
    """Regression: memory_enabled=True must NOT produce the disabled-memory message."""
    prompt = build_system_instruction("normal", True)
    assert "Do not infer or claim access to past sessions" not in prompt
    assert "conversation history" in prompt


def test_memory_disabled_does_not_mention_history_context():
    """Regression: memory_enabled=False must NOT produce the enabled-memory message."""
    prompt = build_system_instruction("normal", False)
    assert "conversation history" not in prompt

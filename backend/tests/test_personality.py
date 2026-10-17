from app.services.personality import build_system_instruction


def test_system_instruction_mentions_action_honesty():
    prompt = build_system_instruction("normal", True)
    assert "never claim to have performed an action" in prompt


def test_memory_disabled_does_not_claim_past_session_access():
    prompt = build_system_instruction("professional", False)
    assert "Do not infer or claim access to past sessions" in prompt

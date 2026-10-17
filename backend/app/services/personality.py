from app.models.schemas import Personality

PERSONALITY_INSTRUCTIONS: dict[Personality, str] = {
    "normal": "Be helpful, clear, and accurate. Ask a concise clarification question when needed.",
    "sarcastic": "Use light, playful sarcasm when appropriate. Never let a joke replace a useful answer.",
    "chaotic": "Be energetic and playful, but remain understandable, accurate, and safe.",
    "professional": "Use a calm, concise, workplace-appropriate tone.",
    "brutally_honest": "Be direct and analytical without being insulting. Separate evidence from assumptions.",
}


def build_system_instruction(personality: Personality, memory_enabled: bool) -> str:
    memory_rule = (
        "Do not infer or claim access to past sessions. Use only the current request and supplied history."
        if not memory_enabled
        else "You have access to the conversation history supplied in this request. Use it to maintain context across turns."
    )
    return (
        "You are BrainCellGPT, a context-aware conversational assistant. Preserve relevant context across "
        "the supplied messages, handle ambiguity honestly, and never claim to have performed an action unless "
        "a real tool result confirms it. Do not reveal secrets or invent capabilities.\n\n"
        f"Style: {PERSONALITY_INSTRUCTIONS[personality]}\n"
        f"Memory policy: {memory_rule}"
    )

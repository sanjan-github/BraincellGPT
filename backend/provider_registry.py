"""Shared registry for hosted OpenAI-compatible provider endpoints."""

OPENAI_COMPATIBLE_PROVIDERS = {
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "env_key": "GROQ_API_KEY", "env_model": "GROQ_MODEL"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "env_key": "OPENAI_API_KEY", "env_model": "OPENAI_MODEL"},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-3.8-flash", "env_key": "GEMINI_API_KEY", "env_model": "GEMINI_MODEL"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini", "env_key": "OPENROUTER_API_KEY", "env_model": "OPENROUTER_MODEL"},
    "nvidia_nim": {"label": "NVIDIA NIM", "base_url": "https://integrate.api.nvidia.com/v1", "model": "meta/llama-3.3-70b-instruct", "env_key": "NVIDIA_NIM_API_KEY", "env_model": "NVIDIA_NIM_MODEL"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "env_key": "DEEPSEEK_API_KEY", "env_model": "DEEPSEEK_MODEL"},
    "mistral": {"label": "Mistral", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest", "env_key": "MISTRAL_API_KEY", "env_model": "MISTRAL_MODEL"},
    "xai": {"label": "xAI", "base_url": "https://api.x.ai/v1", "model": "grok-3-mini", "env_key": "XAI_API_KEY", "env_model": "XAI_MODEL"},
    "together": {"label": "Together AI", "base_url": "https://api.together.xyz/v1", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "env_key": "TOGETHER_API_KEY", "env_model": "TOGETHER_MODEL"},
    "fireworks": {"label": "Fireworks AI", "base_url": "https://api.fireworks.ai/inference/v1", "model": "accounts/fireworks/models/llama-v3p3-70b-instruct", "env_key": "FIREWORKS_API_KEY", "env_model": "FIREWORKS_MODEL"},
    "cerebras": {"label": "Cerebras", "base_url": "https://api.cerebras.ai/v1", "model": "llama-3.3-70b", "env_key": "CEREBRAS_API_KEY", "env_model": "CEREBRAS_MODEL"},
}

AZURE_OPENAI_PROVIDER = {
    "label": "Azure OpenAI",
    "env_key": "AZURE_OPENAI_API_KEY",
    "env_endpoint": "AZURE_OPENAI_ENDPOINT",
    "env_model": "AZURE_OPENAI_DEPLOYMENT",
    "api_style": "azure-openai-v1",
    "auth_header": "api-key",
    "capabilities": {"chat": True, "streaming": True, "tools": True, "image_payload": "openai", "model_support": "deployment-dependent"},
}


def provider_capabilities() -> dict[str, dict]:
    """Describe transport features separately from model-specific support."""
    capabilities = {
        name: {
            "api_style": "openai-chat-completions",
            "chat": True,
            "streaming": True,
            "tools": True,
            "image_payload": "openai",
            "model_support": "model-dependent",
        }
        for name in OPENAI_COMPATIBLE_PROVIDERS
    }
    capabilities["azure_openai"] = dict(AZURE_OPENAI_PROVIDER["capabilities"], api_style=AZURE_OPENAI_PROVIDER["api_style"])
    capabilities.update({
        "anthropic": {"api_style": "anthropic-messages", "chat": True, "streaming": True, "tools": True, "image_payload": "anthropic", "model_support": "model-dependent"},
        "ollama": {"api_style": "openai-compatible-local", "chat": True, "streaming": True, "tools": True, "image_payload": "openai", "model_support": "model-dependent"},
        "lmstudio": {"api_style": "openai-compatible-local", "chat": True, "streaming": True, "tools": True, "image_payload": "openai", "model_support": "model-dependent"},
        "custom": {"api_style": "openai-compatible-custom", "chat": True, "streaming": True, "tools": True, "image_payload": "openai", "model_support": "server-dependent"},
    })
    return capabilities

import httpx
from app.core.config import settings
from app.models.schemas import ChatMessage, Personality
from app.services.personality import build_system_instruction


class ModelConfigurationError(RuntimeError):
    pass


class ModelProviderError(RuntimeError):
    pass


async def generate_reply(message: str, history: list[ChatMessage], personality: Personality, memory_enabled: bool) -> str:
    if not settings.gemini_api_key:
        raise ModelConfigurationError("GEMINI_API_KEY is not configured. Add it to backend/.env.")

    contents = []
    for item in history[-settings.max_context_messages :]:
        contents.append({
            "role": "user" if item.role == "user" else "model",
            "parts": [{"text": item.content}],
        })
    contents.append({"role": "user", "parts": [{"text": message}]})

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    payload = {
        "system_instruction": {"parts": [{"text": build_system_instruction(personality, memory_enabled)}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1200},
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, params={"key": settings.gemini_api_key}, json=payload)
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        reply = "".join(part.get("text", "") for part in parts).strip()
        if not reply:
            raise ModelProviderError("The model returned an empty response.")
        return reply
    except httpx.HTTPStatusError as exc:
        # Avoid returning request URLs because they can contain the API key.
        raise ModelProviderError(f"Model provider returned HTTP {exc.response.status_code}.") from exc
    except httpx.RequestError as exc:
        raise ModelProviderError("Could not reach the model provider. Check network connectivity.") from exc

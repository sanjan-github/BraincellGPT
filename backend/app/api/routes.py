from fastapi import APIRouter, HTTPException
from app.models.schemas import ChatRequest, ChatResponse
from app.services.gemini import ModelConfigurationError, ModelProviderError, generate_reply

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    # History is supplied by the client in this starter version; it is not durable or trusted storage.
    try:
        reply = await generate_reply(
            message=request.message,
            history=request.history,
            personality=request.personality,
            memory_enabled=request.memory_enabled,
        )
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ModelProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatResponse(conversation_id=request.conversation_id, reply=reply, model="gemini")

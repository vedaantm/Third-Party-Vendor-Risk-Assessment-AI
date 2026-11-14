# backend/app/llm/config.py — Groq chat models (was app/config_watsonx.py)

from langchain_groq import ChatGroq

from app.settings import settings

# PRIMARY_MODEL = "llama-3.3-70b-versatile"   # same model, same capability
PRIMARY_MODEL = "openai/gpt-oss-120b"


def get_llm(
    model_id:   str   = PRIMARY_MODEL,
    temperature: float = 0.0,
    max_tokens:  int   = 1000,
) -> ChatGroq:
    return ChatGroq(
        model=model_id,
        api_key=settings.groq_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )

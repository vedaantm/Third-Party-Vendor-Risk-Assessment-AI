import os
from langchain_groq import ChatGroq
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY  = os.environ["GROQ_API_KEY"]
# PRIMARY_MODEL = "llama-3.3-70b-versatile"   # same model, same capability
PRIMARY_MODEL = "openai/gpt-oss-120b"

def get_llm(
    model_id:   str   = PRIMARY_MODEL,
    temperature: float = 0.0,
    max_tokens:  int   = 1000,
) -> ChatGroq:
    return ChatGroq(
        model=model_id,
        api_key=GROQ_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
    )
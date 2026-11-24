"""
Read a SOC 2 report or ISO 27001 certificate into structured facts with the
LLM, as structured output (ADR 0002), using the versioned prompts in
app/prompts.

The document text is fenced in <vendor_content source=...> and the prompt
says it's data, never instructions. Anything that looks like the closing tag
inside the document is defused first, so the text can't end the fence early.
"""

import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.llm.config import get_llm
from app.log import get_logger
from app.prompts import Prompt, load_prompt
from app.services.documents.schemas import Iso27001Certificate, Soc2Report

log = get_logger(__name__)

# Enough for a long SOC 2 report's text within the model's context; the rest
# is cut, and the result records that it was.
MAX_DOCUMENT_CHARS = 120_000
MAX_OUTPUT_TOKENS = 4_000

PROMPTS: dict[str, tuple[Prompt, type[BaseModel]]] = {
    "soc2": (load_prompt("soc2_extraction", 1), Soc2Report),
    "iso27001": (load_prompt("iso27001_extraction", 1), Iso27001Certificate),
}


class ExtractionError(RuntimeError):
    """The model's reply couldn't be read into the schema."""


_FENCE_TAG = re.compile(r"<\s*/?\s*vendor_content", re.IGNORECASE)


def _defuse(text: str) -> str:
    return _FENCE_TAG.sub(lambda m: m.group(0).replace("<", "&lt;"), text)


def _source_label(filename: str) -> str:
    return re.sub(r'["<>\r\n]', "_", filename)[:200]


def fence_document(pages: list[str], filename: str) -> tuple[str, bool]:
    """The pages as one fenced block, and whether it had to be cut short."""
    body, used, truncated = [], 0, False
    for number, text in enumerate(pages, start=1):
        chunk = f"[page {number}]\n{_defuse(text)}\n"
        if used + len(chunk) > MAX_DOCUMENT_CHARS:
            truncated = True
            break
        body.append(chunk)
        used += len(chunk)
    fenced = (
        f'<vendor_content source="{_source_label(filename)}">\n'
        + "".join(body)
        + "</vendor_content>"
    )
    return fenced, truncated


async def extract(kind: str, pages: list[str], filename: str) -> dict[str, Any]:
    """
    {"prompt": "<name>.v<N>", "truncated": bool, "fields": <schema dump>}.
    Raises ExtractionError when the reply doesn't fit the schema; API errors
    propagate to the caller, which records the failure.
    """
    prompt, schema = PROMPTS[kind]
    fenced, truncated = fence_document(pages, filename)
    messages = [
        SystemMessage(content=prompt.text),
        HumanMessage(content=fenced),
    ]
    llm = get_llm(max_tokens=MAX_OUTPUT_TOKENS)
    structured = llm.with_structured_output(
        schema, method="json_schema", include_raw=True
    )
    # include_raw=True: a dict with "raw", "parsed" and "parsing_error"
    out: dict[str, Any] = await structured.ainvoke(messages)  # type: ignore[assignment]
    parsed = out.get("parsed")
    if parsed is None:
        log.warning(
            "documents.schema_mismatch",
            kind=kind,
            error=str(out.get("parsing_error"))[:300],
        )
        raise ExtractionError("the model's reply didn't match the extraction schema")
    return {
        "prompt": prompt.id,
        "truncated": truncated,
        "fields": parsed.model_dump(mode="json"),
    }

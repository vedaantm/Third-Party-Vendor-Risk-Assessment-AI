"""
Spot text in a vendor document that addresses an AI model.

A real audit report or certificate never tells its reader to ignore
instructions or set an evidence tier. When a document does, it can't earn a
top tier, whatever the model extracted from it (assessment.py), and the
reason is shown to the client.
"""

import re

_PATTERNS = [
    r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) (?:instructions|prompts?|rules)",
    r"disregard (?:all |any )?(?:the )?(?:previous|prior|above|earlier)",
    r"instructions? (?:to|for) the (?:ai|llm|model|assistant)",
    r"\byou are (?:an? )?(?:ai|llm|language model|assistant)\b",
    r"\bevidence[_ ]tier\b",
    r"\bverified_document\b",
    r"<\s*/?\s*vendor_content",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


def find_instructions(pages: list[str]) -> list[dict[str, int | str]]:
    """Each suspicious phrase found, with its 1-based page (at most 10)."""
    hits: list[dict[str, int | str]] = []
    for number, text in enumerate(pages, start=1):
        for pattern in _COMPILED:
            for match in pattern.finditer(text):
                hits.append({"page": number, "text": match.group(0).strip()[:100]})
                if len(hits) >= 10:
                    return hits
    return hits

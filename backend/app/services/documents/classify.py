"""
Is this a SOC 2 report, an ISO 27001 certificate, or something else?

Decided in code from marker phrases, not by the model, so a document can't
talk its way into a different parser.
"""

import re
from typing import Literal

DocumentKind = Literal["soc2", "iso27001", "other"]

# (pattern, weight). Strong markers name the document type itself.
_SOC2 = [
    (r"\bsoc\s*2\b", 3),
    (r"system and organi[sz]ation controls", 3),
    (r"trust services criteria", 2),
    (r"service auditor", 2),
    (r"\btype\s*(?:1|2|i|ii)\b", 1),
    (r"complementary user entity controls", 1),
]
_ISO27001 = [
    (r"\biso\s*/?\s*(?:iec\s*)?27001\b", 3),
    (r"certificate of (?:registration|certification|approval)", 2),
    (r"certification body", 1),
    (r"certificate (?:number|no\.?)", 1),
    (r"accredit", 1),
    (r"information security management system", 1),
]
# A document needs this much evidence, and a clear lead, to be either kind
_MIN_SCORE = 5


def _score(text: str, markers: list[tuple[str, int]]) -> int:
    return sum(weight for pattern, weight in markers if re.search(pattern, text))


def classify(pages: list[str]) -> DocumentKind:
    # The first pages say what a document is; later ones may mention anything
    text = "\n".join(pages[:5]).lower()
    soc2, iso = _score(text, _SOC2), _score(text, _ISO27001)
    if max(soc2, iso) < _MIN_SCORE or soc2 == iso:
        return "other"
    return "soc2" if soc2 > iso else "iso27001"

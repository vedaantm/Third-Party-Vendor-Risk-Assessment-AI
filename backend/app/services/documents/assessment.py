"""
Evidence tiers for parsed documents, decided here in code and never by the
model. The inputs are the extracted facts after citations.verify_all.

SOC 2:     VERIFIED_DOCUMENT only for a Type 2 report with an unqualified
           opinion whose period ended within the last 12 months (or whose
           bridge letter covers the gap), with those three facts quoted from
           the document. Anything else is UNVERIFIED_DOCUMENT, with reasons.
ISO 27001: CORROBORATED while the certificate is valid, EXPIRED after its
           expiry date, UNVERIFIED_DOCUMENT when the dates can't be trusted.

Either kind is UNVERIFIED_DOCUMENT when the text addresses an AI model
(injection.py): a genuine report never does.

A qualified, adverse or disclaimed opinion also sets `modified_opinion`,
which the evaluation treats as a red flag (spec decisions log).
"""

from datetime import date, timedelta
from typing import Any

VERIFIED_DOCUMENT = "VERIFIED_DOCUMENT"
CORROBORATED = "CORROBORATED"
EXPIRED = "EXPIRED"
UNVERIFIED_DOCUMENT = "UNVERIFIED_DOCUMENT"

DOCUMENT_TIERS = (VERIFIED_DOCUMENT, CORROBORATED, EXPIRED, UNVERIFIED_DOCUMENT)

# How old a SOC 2 period end (or bridge letter) may be and still count
SOC2_FRESHNESS = timedelta(days=365)
MODIFIED_OPINIONS = ("qualified", "adverse", "disclaimer")


def _reason(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _value(field: dict[str, Any] | None) -> Any:
    return field.get("value") if isinstance(field, dict) else None


def _verified(field: dict[str, Any] | None) -> bool:
    return isinstance(field, dict) and bool(field.get("verified"))


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _suspicious_reason(suspicious: list[dict[str, Any]]) -> dict[str, str]:
    pages = sorted({int(hit["page"]) for hit in suspicious})
    return _reason(
        "suspicious_instructions",
        "The document contains text addressed to an AI model (page "
        + ", ".join(str(p) for p in pages)
        + "), so it can't be trusted as evidence without a manual review.",
    )


def assess_soc2(
    report: dict[str, Any], today: date, suspicious: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Tier and reasons for a verified Soc2Report dump."""
    reasons: list[dict[str, str]] = []
    if suspicious:
        reasons.append(_suspicious_reason(suspicious))

    report_type = _value(report.get("report_type"))
    if report_type == 1:
        reasons.append(
            _reason(
                "type_1",
                "Type 1 report: controls were assessed at one point in time, not "
                "tested over a period.",
            )
        )
    elif report_type != 2:
        reasons.append(
            _reason("type_unknown", "The report type (1 or 2) wasn't found.")
        )

    opinion = _value(report.get("opinion"))
    modified = opinion in MODIFIED_OPINIONS
    if modified:
        reasons.append(
            _reason(
                "opinion_modified",
                f"The service auditor's opinion is {opinion}, not unqualified.",
            )
        )
    elif opinion != "unqualified":
        reasons.append(
            _reason("opinion_unknown", "The auditor's opinion wasn't found.")
        )

    period_end = _as_date(_value(report.get("period_end")))
    bridge = report.get("bridge_letter") or None
    bridge_through = _as_date(bridge.get("covers_through")) if bridge else None
    bridge_ok = (
        bridge_through is not None and _verified(bridge) and bridge_through <= today
    )
    if period_end is None:
        reasons.append(
            _reason("period_unknown", "The end of the audit period wasn't found.")
        )
    elif period_end > today:
        reasons.append(
            _reason(
                "period_in_future",
                f"The audit period ends in the future ({period_end}).",
            )
        )
    else:
        covered_until = (
            max(period_end, bridge_through)
            if bridge_ok and bridge_through
            else period_end
        )
        if today - covered_until > SOC2_FRESHNESS:
            reasons.append(
                _reason(
                    "stale_period",
                    f"The audit period ended {period_end}, more than 12 months ago"
                    + (
                        f", and the bridge letter only covers to {bridge_through}."
                        if bridge_ok
                        else ", with no bridge letter covering the gap."
                    ),
                )
            )

    # The deciding facts must be quoted from the document itself
    unquoted = [
        name
        for name in ("report_type", "opinion", "period_end")
        if _value(report.get(name)) is not None and not _verified(report.get(name))
    ]
    if unquoted:
        reasons.append(
            _reason(
                "unverified_citation",
                "These facts couldn't be matched to the document text: "
                + ", ".join(unquoted)
                + ".",
            )
        )

    return {
        "tier": UNVERIFIED_DOCUMENT if reasons else VERIFIED_DOCUMENT,
        "reasons": reasons,
        "modified_opinion": modified,
        "opinion": opinion,
        "assessed_on": today.isoformat(),
    }


def assess_iso27001(
    cert: dict[str, Any], today: date, suspicious: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Tier and reasons for a verified Iso27001Certificate dump."""
    reasons: list[dict[str, str]] = []
    if suspicious:
        reasons.append(_suspicious_reason(suspicious))
    issued = _as_date(_value(cert.get("issue_date")))
    expiry = _as_date(_value(cert.get("expiry_date")))

    if expiry is None:
        reasons.append(
            _reason("expiry_unknown", "The certificate's expiry date wasn't found.")
        )
    elif not _verified(cert.get("expiry_date")):
        reasons.append(
            _reason(
                "unverified_citation",
                "The expiry date couldn't be matched to the document text.",
            )
        )
    if issued is not None and issued > today:
        reasons.append(
            _reason("issued_in_future", f"The issue date {issued} is in the future.")
        )
    if issued is not None and expiry is not None and expiry < issued:
        reasons.append(
            _reason("dates_inconsistent", "The expiry date is before the issue date.")
        )

    if reasons:
        tier = UNVERIFIED_DOCUMENT
    elif expiry is not None and expiry < today:
        tier = EXPIRED
        reasons.append(_reason("expired", f"The certificate expired on {expiry}."))
    else:
        tier = CORROBORATED

    return {
        "tier": tier,
        "reasons": reasons,
        "modified_opinion": False,
        "assessed_on": today.isoformat(),
    }

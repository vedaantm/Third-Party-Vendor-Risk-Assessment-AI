"""
What the model is asked to extract from a SOC 2 report or an ISO 27001
certificate, requested as structured output (ADR 0002).

Every field carries the page it came from and a short verbatim quote, so
the client can check it and so the code can confirm the quote is really on
that page (see citations.py). The model never decides the evidence tier:
there is no tier field here, and assessment.py works it out from these facts.
"""

from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

Opinion = Literal["unqualified", "qualified", "adverse", "disclaimer"]


class Cited(BaseModel, Generic[T]):
    """One extracted value with where it came from."""

    # Extra keys (e.g. an "evidence_tier" a hostile document talked the model
    # into adding) are dropped, never stored.
    model_config = ConfigDict(extra="ignore")

    value: T | None = Field(
        default=None, description="null if the document does not say"
    )
    page: int | None = Field(default=None, description="1-based page the value is on")
    quote: str | None = Field(
        default=None, description="Short verbatim quote from that page (max ~200 chars)"
    )


class ControlException(BaseModel):
    """A deviation the service auditor found while testing a control."""

    model_config = ConfigDict(extra="ignore")

    control: str = Field(description="Control id or name, e.g. CC6.1")
    description: str = Field(description="What the auditor found")
    page: int | None = Field(default=None, description="1-based page")
    quote: str | None = Field(default=None, description="Short verbatim quote")


class BridgeLetter(BaseModel):
    """A management letter covering the time since the audit period ended."""

    model_config = ConfigDict(extra="ignore")

    covers_through: date | None = Field(
        default=None, description="Last date the letter covers (YYYY-MM-DD)"
    )
    page: int | None = None
    quote: str | None = None


class Soc2Report(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auditor_firm: Cited[str] = Field(default_factory=Cited[str])
    report_type: Cited[Literal[1, 2]] = Field(
        default_factory=Cited[Literal[1, 2]], description="1 for Type 1, 2 for Type 2"
    )
    period_start: Cited[date] = Field(
        default_factory=Cited[date], description="Start of the review period (Type 2)"
    )
    period_end: Cited[date] = Field(
        default_factory=Cited[date],
        description="End of the review period, or the as-of date of a Type 1 report",
    )
    systems_in_scope: Cited[list[str]] = Field(default_factory=Cited[list[str]])
    trust_services_criteria: Cited[list[str]] = Field(
        default_factory=Cited[list[str]],
        description="e.g. Security, Availability, Confidentiality",
    )
    control_exceptions: list[ControlException] = Field(default_factory=list)
    complementary_user_entity_controls: Cited[list[str]] = Field(
        default_factory=Cited[list[str]]
    )
    subservice_organisations: Cited[list[str]] = Field(default_factory=Cited[list[str]])
    opinion: Cited[Opinion] = Field(
        default_factory=Cited[Opinion], description="The service auditor's opinion"
    )
    bridge_letter: BridgeLetter | None = Field(
        default=None, description="null unless a bridge letter is included"
    )


class Iso27001Certificate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    certificate_number: Cited[str] = Field(default_factory=Cited[str])
    certification_body: Cited[str] = Field(default_factory=Cited[str])
    accreditation_body: Cited[str] = Field(default_factory=Cited[str])
    scope: Cited[str] = Field(default_factory=Cited[str])
    issue_date: Cited[date] = Field(default_factory=Cited[date])
    expiry_date: Cited[date] = Field(default_factory=Cited[date])

from typing import TypedDict, Optional

class AgentOutput(TypedDict):
    score: float
    explanation: str
    evidence: list
    confidence: float
    red_flags: list

class VendorRiskState(TypedDict):
    # Input
    vendor_id: str
    vendor_name: str
    company_id: str
    evaluation_id: str

    # Weights
    weight_financial: float
    weight_compliance: float
    weight_operational: float
    weight_reputation: float
    weight_strategic: float

    # Raw data
    financial_data: dict
    compliance_data: dict
    operational_data: dict
    reputation_data: dict
    strategic_data: dict

    # Gate A
    data_complete: bool
    missing_fields: list

    # Agent outputs
    financial_output: Optional[dict]
    compliance_output: Optional[dict]
    operational_output: Optional[dict]
    reputation_output: Optional[dict]
    strategic_output: Optional[dict]

    # Gate B
    conflict_detected: bool
    conflict_details: Optional[str]
    human_review_notes: Optional[str]

    # Meta-agent outputs
    composite_score: Optional[float]
    risk_score: Optional[float]
    fit_score: Optional[float]
    final_recommendation: Optional[str]
    aggregate_confidence: Optional[float]
    final_explanation: Optional[str]

    # Pipeline
    pipeline_status: str
    error_message: Optional[str]
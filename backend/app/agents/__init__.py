"""
The agents of the MediShield intake pipeline.

    preprocessor  -- image -> text, fields, blur            (vision LLM + Laplacian)
    classifier    -- text -> doc_type                       (content markers)
    kyc           -- insurance validity + identity          (parallel with claims)
    claims        -- claim or clinical extraction           (parallel with kyc)
    policy        -- coverage of CPT codes via Qdrant RAG
    fraud         -- six detectors from dataset_summary.md
    orchestrator  -- final APPROVE / REJECT / ESCALATE
"""

from app.agents.claims import claims_agent
from app.agents.classifier import classifier_agent
from app.agents.fraud import fraud_agent
from app.agents.kyc import kyc_agent
from app.agents.orchestrator import orchestrator_agent
from app.agents.policy import policy_agent
from app.agents.preprocessor import preprocessor_agent

__all__ = [
    "preprocessor_agent", "classifier_agent", "kyc_agent", "claims_agent",
    "policy_agent", "fraud_agent", "orchestrator_agent",
]

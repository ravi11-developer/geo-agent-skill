from pydantic import BaseModel
from typing import Literal

class SuggestedAction(BaseModel):
    summary: str
    priority: Literal["critical", "high", "medium", "low"]

class Finding(BaseModel):
    id: str
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    evidence: str
    suggested_action: SuggestedAction

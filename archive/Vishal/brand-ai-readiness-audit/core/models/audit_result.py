from typing import List, Optional
from pydantic import BaseModel, HttpUrl
from .finding import Finding

class AuditSummary(BaseModel):
    total_findings: int
    critical: int
    high: int
    medium: int

class AuditResult(BaseModel):
    site: HttpUrl
    audited_at: str
    summary: AuditSummary
    findings: List[Finding]

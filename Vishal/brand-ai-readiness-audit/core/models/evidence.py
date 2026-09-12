from typing import Any, Optional
from pydantic import BaseModel, HttpUrl

class Evidence(BaseModel):
    source_url: HttpUrl
    type: str
    value: Any
    location: str
    method: str

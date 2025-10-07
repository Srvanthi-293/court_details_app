from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field

class QueryLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    case_type: str
    case_number: int
    year: int
    court_level: str
    status: str = "ok"              # "ok" | "error"
    source_url: str = ""            # portal URL used
    html_preview: str = ""          # first 500 chars of HTML
    created_at: datetime = Field(default_factory=datetime.utcnow)

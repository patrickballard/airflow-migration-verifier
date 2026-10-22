from typing import Optional
from pydantic import BaseModel
class M(BaseModel):
    nickname: Optional[str]  # v1: optional *and* defaults to None
print("M() ->", M().nickname)

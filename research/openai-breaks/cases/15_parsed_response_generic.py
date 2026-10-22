from pydantic import BaseModel
from openai.types.responses import ParsedResponse
class M(BaseModel):
    a: int
print("ParsedResponse[M]:", ParsedResponse[M].__name__)

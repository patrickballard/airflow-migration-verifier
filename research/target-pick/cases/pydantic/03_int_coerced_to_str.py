from pydantic import BaseModel
class M(BaseModel):
    code: str
print("code=123 ->", repr(M(code=123).code))  # v1 silently made it "123"

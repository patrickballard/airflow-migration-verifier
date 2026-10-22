from typing import Optional

from pydantic import BaseModel, BaseSettings, Field, root_validator, validator


class Settings(BaseSettings):
    debug: bool = False


class User(BaseModel):
    id: int
    code: str
    nickname: Optional[str]
    email: str = Field(..., regex=r".+@.+")

    class Config:
        orm_mode = True

    @validator("code")
    def upper(cls, v):
        return v.upper()

    @root_validator
    def check(cls, values):
        return values


u = User(id=1, code=123, email="a@b.c")  # relies on v1 coercing 123 -> "123"
print(u.dict(), Settings().debug, u.nickname)

from typing import Optional

from pydantic import field_validator, BaseModel, Field, root_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    debug: bool = False


class User(BaseModel):
    id: int
    code: str
    nickname: Optional[str] = None
    email: str = Field(..., pattern=r".+@.+")
    model_config = SettingsConfigDict(from_attributes=True)

    @field_validator("code")
    @classmethod
    def upper(cls, v):
        return v.upper()

    @root_validator
    def check(cls, values):
        return values


u = User(id=1, code=123, email="a@b.c")  # relies on v1 coercing 123 -> "123"
print(u.dict(), Settings().debug, u.nickname)

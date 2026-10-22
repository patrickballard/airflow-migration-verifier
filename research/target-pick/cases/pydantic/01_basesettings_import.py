from pydantic import BaseSettings  # settings models, very common in apps
class S(BaseSettings):
    debug: bool = False
print("BaseSettings ok:", S().debug)

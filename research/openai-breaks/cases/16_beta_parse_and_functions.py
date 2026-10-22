import warnings; warnings.simplefilter("error")
from pydantic import BaseModel
from openai import OpenAI
from mock import start
class A(BaseModel):
    a: int
c = OpenAI(api_key="x", base_url=start())
p = c.beta.chat.completions.parse(model="m", messages=[{"role": "user", "content": "x"}], response_format=A)
f = c.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}], functions=[{"name": "f", "parameters": {}}], function_call="auto")
print("beta parse:", p.choices[0].message.parsed, "| functions= ok")

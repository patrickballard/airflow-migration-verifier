from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain_core.language_models.fake import FakeListLLM
chain = LLMChain(llm=FakeListLLM(responses=["hi"]), prompt=PromptTemplate.from_template("say {x}"))
print("LLMChain ->", chain.invoke({"x": "hi"})["text"])

from langchain.agents import AgentType, initialize_agent
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain_core.language_models.fake import FakeListLLM
from langchain_core.tools import tool


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return text


llm = FakeListLLM(responses=["hi"])
chain = LLMChain(llm=llm, prompt=PromptTemplate.from_template("say {x}"))
print(chain.invoke({"x": "hi"})["text"])
agent = initialize_agent([echo], llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION)
print(type(agent).__name__)

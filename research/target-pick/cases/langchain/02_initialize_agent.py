from langchain.agents import AgentType, initialize_agent  # classic agents
print("initialize_agent:", callable(initialize_agent), AgentType.ZERO_SHOT_REACT_DESCRIPTION.value)

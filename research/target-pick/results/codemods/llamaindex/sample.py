from llama_index.core import Document, MockEmbedding, ServiceContext, VectorStoreIndex
from llama_index.core.llms import MockLLM

ctx = ServiceContext.from_defaults(llm=MockLLM(), embed_model=MockEmbedding(embed_dim=8))
index = VectorStoreIndex.from_documents([Document(text="hello")], service_context=ctx)
print(type(index).__name__)

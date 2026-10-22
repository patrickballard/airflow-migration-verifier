# ServiceContext (LLM + embeddings config passed to every index) was replaced by a global Settings.
try:
    from llama_index.core import ServiceContext
    from llama_index.core.embeddings import MockEmbedding
    from llama_index.core.llms import MockLLM
except ImportError:
    from llama_index import ServiceContext
    from llama_index import MockEmbedding
    from llama_index.llms import MockLLM
ctx = ServiceContext.from_defaults(llm=MockLLM(), embed_model=MockEmbedding(embed_dim=8))
print("ServiceContext.from_defaults ->", type(ctx).__name__)

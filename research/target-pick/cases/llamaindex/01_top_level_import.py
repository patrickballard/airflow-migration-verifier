from llama_index import Document, VectorStoreIndex  # pre-0.10 import path
print("ok", VectorStoreIndex.__name__, Document(text="x").text)

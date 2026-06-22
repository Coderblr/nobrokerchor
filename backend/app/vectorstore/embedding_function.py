import hashlib

from chromadb import Documents, EmbeddingFunction, Embeddings

EMBEDDING_DIMENSIONS = 32


class OfflineHashEmbeddingFunction(EmbeddingFunction):
    """ChromaDB's default embedding function downloads an ONNX model from the internet on first
    use - this fails with a DNS/getaddrinfo error on networks that block that egress (e.g. a
    locked-down corporate desktop). This app never runs semantic similarity search (`.query()`);
    every read goes through `.get(where=...)` metadata filtering instead, so the actual embedding
    values are irrelevant - only "produce *some* fixed-length vector, fully offline" matters."""

    def __call__(self, input: Documents) -> Embeddings:
        embeddings = []
        for text in input:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            repeated = (digest * (EMBEDDING_DIMENSIONS // len(digest) + 1))[:EMBEDDING_DIMENSIONS]
            embeddings.append([byte / 255.0 for byte in repeated])
        return embeddings

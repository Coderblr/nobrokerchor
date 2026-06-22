import logging
from functools import lru_cache

import chromadb

from app.core.config import settings
from app.vectorstore.collections import COLLECTION_NAMES
from app.vectorstore.embedding_function import OfflineHashEmbeddingFunction

logger = logging.getLogger(__name__)

_embedding_function = OfflineHashEmbeddingFunction()


@lru_cache
def get_chroma_client() -> chromadb.ClientAPI:
    persist_dir = str(settings.resolved_path(settings.chroma_persist_dir))
    return chromadb.PersistentClient(path=persist_dir)


def init_collections() -> None:
    client = get_chroma_client()
    for name in COLLECTION_NAMES:
        client.get_or_create_collection(name=name, embedding_function=_embedding_function)


def get_collection(name: str):
    if name not in COLLECTION_NAMES:
        raise ValueError(f"Unknown ChromaDB collection: {name}")
    return get_chroma_client().get_or_create_collection(name=name, embedding_function=_embedding_function)


def add_document(collection_name: str, doc_id: str, text: str, metadata: dict) -> None:
    """Knowledge-base writes are a supplementary side effect of agent runs (browsable later on
    the Knowledge Base page), not part of the actual generated deliverable - a write failure here
    must never abort an otherwise-successful agent/pipeline run."""
    try:
        collection = get_collection(collection_name)
        collection.upsert(ids=[doc_id], documents=[text], metadatas=[metadata])
    except Exception:  # noqa: BLE001 - log and continue, never let this fail the calling agent
        logger.warning("Failed to write to ChromaDB collection '%s' (doc_id=%s)", collection_name, doc_id, exc_info=True)


def get_documents(collection_name: str, where: dict) -> list[dict]:
    collection = get_collection(collection_name)
    result = collection.get(where=where)
    return [
        {"id": doc_id, "document": document, "metadata": metadata}
        for doc_id, document, metadata in zip(result["ids"], result["documents"], result["metadatas"])
    ]

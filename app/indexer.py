"""
LlamaIndex VectorStoreIndex avec embeddings Ollama (Qwen2.5-Coder).
"""
import logging
import os
from llama_index.core import VectorStoreIndex, Settings
from llama_index.embeddings.ollama import OllamaEmbedding

from app.java_classes import JAVA_CLASSES, build_documents

log = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen2.5-coder:7b")


class JavaClassIndex:

    def __init__(self):
        log.info("Initialisation index LlamaIndex — Ollama %s @ %s", EMBED_MODEL, OLLAMA_URL)

        Settings.embed_model = OllamaEmbedding(
            model_name=EMBED_MODEL,
            base_url=OLLAMA_URL,
        )
        Settings.llm = None

        docs = build_documents()
        self._index   = VectorStoreIndex.from_documents(docs)
        self._class_map = {c["name"]: c for c in JAVA_CLASSES}

        log.info("Index prêt — %d classes Java indexées", len(JAVA_CLASSES))

    def find_relevant_classes(self, query: str, top_k: int = 3) -> list[dict]:
        retriever = self._index.as_retriever(similarity_top_k=top_k)
        nodes     = retriever.retrieve(query)

        results = []
        for node in nodes:
            name = node.metadata.get("name", "?")
            cls  = self._class_map.get(name, {})
            results.append({
                "name":       name,
                "package":    node.metadata.get("package", cls.get("package", "?")),
                "complexity": node.metadata.get("complexity", cls.get("complexity", "?")),
                "migration":  node.metadata.get("migration", cls.get("migration_hint", "")),
                "score":      round(node.score, 4) if node.score else 0.0,
            })
        return results

"""RAG query engine package for Project Astra.

Public surface:
    ask(question, ...)        -- full retrieval-augmented answer (LLM call added in a later step)
    retrieve(question, ...)   -- vector search only, returns ranked Source records
    build_prompt(question, sources) -- assemble system + user prompt from retrieved context
    SCORE_THRESHOLD           -- minimum top-1 similarity required to attempt an answer
"""

from rag.query_engine import (
    ask,
    retrieve,
    build_prompt,
    SCORE_THRESHOLD,
    DEFAULT_TOP_K,
)

__all__ = [
    "ask",
    "retrieve",
    "build_prompt",
    "SCORE_THRESHOLD",
    "DEFAULT_TOP_K",
]

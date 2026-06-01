"""Retrieval-augmented query engine for Genshin lore questions.

This module covers Phase 2 up to (but not including) the LLM generation call:

    1. embed_query(question)      -- encode the question into a 384-dim unit vector
    2. retrieve(question, ...)    -- vector search against the Pinecone index
    3. score-threshold refusal    -- bail out before any LLM if nothing is relevant
    4. build_prompt(...)          -- assemble the system + user prompt from context

The actual Gemini (and Groq fallback) generation step is added in a later step;
its insertion point is marked clearly inside `ask()`.

The embedding model and metric MUST match embed_all.py so query vectors live in
the same space as the stored chunk vectors.
"""

import os
from dotenv import load_dotenv

# Keep model/index config in sync with the embedding pipeline. These are the
# single source of truth; importing avoids drift between upsert and query time.
try:
    from embed_all import MODEL_NAME, INDEX_NAME
except ImportError:  # pragma: no cover - fallback if run outside project root
    MODEL_NAME = "all-MiniLM-L6-v2"
    INDEX_NAME = "genshin-lore"

# Retrieval configuration ---------------------------------------------------

DEFAULT_TOP_K = 10

# LLM generation configuration ----------------------------------------------

# Temporarily on 3.1 Flash-Lite: higher free-tier RPD (500 vs 20 on 2.5) on current AI Studio quotas.
GEMINI_MODEL = "gemini-3.1-flash-lite"          # primary
GROQ_MODEL = "llama-3.3-70b-versatile"          # fallback (OpenAI-compatible)

# Low temperature keeps answers grounded in the retrieved context rather than
# creative. Output cap is generous for a few paragraphs of lore prose.
GENERATION_TEMPERATURE = 0.3
GENERATION_MAX_TOKENS = 800

# Minimum top-1 cosine similarity required to even attempt an answer. Below this
# we assume the question is off-topic (or unsupported) and refuse WITHOUT calling
# any LLM. This is the cheapest and strongest anti-proxy guardrail: a pure math
# check, not an LLM judgment. Validated empirically -- on-topic lore queries score
# ~0.55-0.66 while off-topic queries (e.g. "capital of France") top out near ~0.32.
SCORE_THRESHOLD = 0.35

REFUSAL_MESSAGE = (
    "I can only answer questions about Genshin Impact lore -- characters, "
    "Archon Quests, and World Quest storylines. Try asking about a character's "
    "background, a quest's events, or how two characters are connected."
)

# Lazy singletons -----------------------------------------------------------

_embedding_model = None
_pinecone_index = None
_gemini_client = None
_groq_client = None


def _get_embedding_model():
    """Load the sentence-transformers model once and cache it."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(MODEL_NAME)
    return _embedding_model


def _get_pinecone_index():
    """Connect to the Pinecone index once and cache the handle."""
    global _pinecone_index
    if _pinecone_index is None:
        from pinecone import Pinecone

        load_dotenv()
        api_key = os.environ.get("PINECONE_API_KEY", "").strip()
        if not api_key or api_key.startswith("YOUR"):
            raise SystemExit(
                "PINECONE_API_KEY is not set. Paste your real key into .env."
            )
        _pinecone_index = Pinecone(api_key=api_key).Index(INDEX_NAME)
    return _pinecone_index


def _get_gemini_client():
    """Create the google-genai client once and cache it."""
    global _gemini_client
    if _gemini_client is None:
        from google import genai

        load_dotenv()
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set in .env.")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


def _get_groq_client():
    """Create the Groq client once and cache it."""
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        load_dotenv()
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set in .env.")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


# Query embedding -----------------------------------------------------------

def embed_query(question):
    """Encode a question into a normalized 384-dim vector (cosine-ready)."""
    model = _get_embedding_model()
    vector = model.encode(question, normalize_embeddings=True)
    return vector.tolist()


# Metadata filtering --------------------------------------------------------

def _build_pinecone_filter(filters):
    """Translate a simple {field: value} dict into a Pinecone filter expression.

    A scalar value becomes an exact match ($eq); a list becomes a membership
    test ($in). Returns None when there is nothing to filter on.
    """
    if not filters:
        return None
    expression = {}
    for field, value in filters.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            cleaned = [str(item) for item in value if item is not None]
            if cleaned:
                expression[field] = {"$in": cleaned}
        else:
            expression[field] = {"$eq": value}
    return expression or None


# Retrieval -----------------------------------------------------------------

def retrieve(question, top_k=DEFAULT_TOP_K, filters=None):
    """Embed the question and return the top-k matching chunks as Source records.

    Each Source is a dict: {chunk_id, score, text, source_page, source_url,
    section, page_type, label}. `label` is a human-friendly name for citations
    (character name or quest title). Results are ordered best-first.
    """
    query_vector = embed_query(question)
    index = _get_pinecone_index()

    response = index.query(
        vector=query_vector,
        top_k=top_k,
        include_metadata=True,
        filter=_build_pinecone_filter(filters),
    )

    sources = []
    for match in response.get("matches", []):
        metadata = match.get("metadata", {}) or {}
        label = (
            metadata.get("character_name")
            or metadata.get("quest_title")
            or metadata.get("source_page")
            or "Unknown"
        )
        sources.append({
            "chunk_id": match.get("id"),
            "score": match.get("score", 0.0),
            "text": metadata.get("text", ""),
            "source_page": metadata.get("source_page"),
            "source_url": metadata.get("source_url"),
            "section": metadata.get("section"),
            "page_type": metadata.get("page_type"),
            "label": label,
        })
    return sources


# Prompt assembly -----------------------------------------------------------

SYSTEM_INSTRUCTION = (
    "You are a knowledgeable lore expert for the video game Genshin Impact. "
    "Answer the user's question using the numbered context passages provided. "
    "Try to give more information to expand user's question, not only just answer the direct question. "
    "The passages are drawn from the official wiki (character profiles, Archon "
    "Quests, and World Quest storylines).\n\n"
    "Rules:\n"
    "- Base claims on the provided context when possible. You can use outside knowledge "
    "if they match on the provided context.\n"
    "- If the context does not contain enough information to answer, say so plainly "
    "instead of guessing.\n"
    "- Only answer questions about Genshin Impact lore. Politely decline anything "
    "unrelated.\n"
    "- Write in clear, natural prose. Cite the passages you used by their number, "
    "e.g. [1], [3]."
)


def _format_context(sources):
    """Render retrieved Source records into a numbered context block."""
    blocks = []
    for position, source in enumerate(sources, start=1):
        header_parts = [source.get("label") or "Unknown"]
        if source.get("section"):
            header_parts.append(source["section"])
        header = " -- ".join(header_parts)
        blocks.append(f"[{position}] {header}\n{source.get('text', '').strip()}")
    return "\n\n".join(blocks)


def build_prompt(question, sources):
    """Assemble the system + user prompt from the question and retrieved context.

    Returns a dict {system, user}. Kept LLM-agnostic so the same prompt feeds
    both the Gemini SDK (system_instruction + contents) and the Groq fallback
    (OpenAI-style system/user messages).
    """
    context_block = _format_context(sources)
    user_content = (
        f"Context passages:\n\n{context_block}\n\n"
        f"Question: {question}\n\n"
        "Answer using only the context above, citing passage numbers."
    )
    return {"system": SYSTEM_INSTRUCTION, "user": user_content}


# LLM generation ------------------------------------------------------------

def _generate_with_gemini(prompt):
    """Generate an answer with Gemini Flash-Lite. Raises on any failure."""
    from google.genai import types

    client = _get_gemini_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt["user"],
        config=types.GenerateContentConfig(
            system_instruction=prompt["system"],
            temperature=GENERATION_TEMPERATURE,
            max_output_tokens=GENERATION_MAX_TOKENS,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def _generate_with_groq(prompt):
    """Generate an answer with Groq llama-3.3-70b-versatile. Raises on failure."""
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        temperature=GENERATION_TEMPERATURE,
        max_tokens=GENERATION_MAX_TOKENS,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("Groq returned an empty response.")
    return text


def _generate(prompt):
    """Generate an answer, trying Gemini first and falling back to Groq.

    Returns (answer_text, model_used). If both providers fail, returns a
    friendly error message with model_used=None so the caller can surface it.
    """
    try:
        return _generate_with_gemini(prompt), GEMINI_MODEL
    except Exception as gemini_error:
        try:
            return _generate_with_groq(prompt), GROQ_MODEL
        except Exception as groq_error:
            message = (
                "Both language model providers are currently unavailable. "
                f"(Gemini: {gemini_error}; Groq: {groq_error})"
            )
            return message, None


# Orchestration -------------------------------------------------------------

def ask(question, filters=None, top_k=DEFAULT_TOP_K):
    """Answer a Genshin lore question with retrieval-augmented generation.

    Returns a dict: {answer, sources, model_used, refused, top_score}.

    Flow:
      1. retrieve top-k chunks from Pinecone
      2. if the best score is below SCORE_THRESHOLD -> refuse (no LLM call)
      3. build the generation prompt
      4. generate with Gemini Flash-Lite, falling back to Groq on failure
    """
    sources = retrieve(question, top_k=top_k, filters=filters)
    top_score = sources[0]["score"] if sources else 0.0

    # Guardrail: refuse off-topic / unsupported questions before any LLM cost.
    if not sources or top_score < SCORE_THRESHOLD:
        return {
            "answer": REFUSAL_MESSAGE,
            "sources": [],
            "model_used": None,
            "refused": True,
            "top_score": top_score,
        }

    prompt = build_prompt(question, sources)
    answer, model_used = _generate(prompt)

    return {
        "answer": answer,
        "sources": sources,
        "model_used": model_used,
        "refused": False,
        "top_score": top_score,
    }

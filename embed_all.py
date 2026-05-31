"""Embed all scraped chunks and upsert them into a Pinecone serverless index.

Pipeline:
  1. Load chunks from data/characters, data/quests/archon, data/quests/world_series
  2. Embed each chunk's text with sentence-transformers all-MiniLM-L6-v2 (384-dim)
  3. Upsert vectors (id = chunk_id, values = embedding, metadata = chunk metadata + text)
     into a cosine-similarity Pinecone index, creating the index if it does not exist.

Usage:
  python embed_all.py                # embed everything and upsert
  python embed_all.py --dry-run      # load + report counts, never touch Pinecone
  python embed_all.py --limit 50     # only process the first 50 chunks (smoke test)
  python embed_all.py --recreate     # delete and recreate the index before upserting
"""

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

# Configuration -------------------------------------------------------------

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384
INDEX_NAME = "genshin-lore"
SIMILARITY_METRIC = "cosine"
UPSERT_BATCH_SIZE = 100
EMBED_BATCH_SIZE = 64

# Each source directory holds one JSON file per entity, plus a _manifest.json.
DATA_DIRECTORIES = [
    Path("data/characters"),
    Path("data/quests/archon"),
    Path("data/quests/world_series"),
]

# Pinecone metadata values must be string, number, boolean, or list-of-strings.
# These chunk keys hold the structured payload we want to keep for retrieval +
# filtering. The full chunk text is stored separately under the "text" key.
PINECONE_METADATA_KEY_ORDER_NOTE = "text is added on top of the chunk's own metadata"


# Loading -------------------------------------------------------------------

def load_all_chunks(limit=None):
    """Read every entity JSON file and return a flat list of chunk records.

    Each returned record is the raw chunk dict: {chunk_id, text, metadata}.
    Files whose name starts with "_" (e.g. _manifest.json) are skipped.
    """
    collected_chunks = []
    for directory in DATA_DIRECTORIES:
        if not directory.exists():
            print(f"  [skip] missing directory: {directory}")
            continue
        json_files = sorted(
            path for path in directory.glob("*.json") if not path.name.startswith("_")
        )
        directory_chunk_count = 0
        for json_path in json_files:
            with open(json_path, encoding="utf-8") as file_handle:
                entity_data = json.load(file_handle)
            chunks = entity_data.get("chunks", [])
            collected_chunks.extend(chunks)
            directory_chunk_count += len(chunks)
        print(f"  {directory}: {len(json_files)} files, {directory_chunk_count} chunks")

    if limit is not None:
        collected_chunks = collected_chunks[:limit]
    return collected_chunks


def build_pinecone_metadata(chunk):
    """Build a Pinecone-safe metadata dict for one chunk.

    Pinecone rejects null values and only accepts str / int / float / bool /
    list-of-strings. We drop nulls and empty lists, coerce list items to strings,
    and attach the chunk text so retrieval can return it to the LLM.
    """
    cleaned_metadata = {}
    for key, value in chunk.get("metadata", {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            cleaned_metadata[key] = value
        elif isinstance(value, (int, float, str)):
            cleaned_metadata[key] = value
        elif isinstance(value, list):
            string_items = [str(item) for item in value if item is not None]
            if string_items:
                cleaned_metadata[key] = string_items
        # Any other type (dict, etc.) is intentionally dropped.

    # Store the chunk text so the query engine can feed it to the LLM directly.
    cleaned_metadata["text"] = chunk["text"]
    return cleaned_metadata


# Pinecone ------------------------------------------------------------------

def get_pinecone_index(recreate=False):
    """Create the Pinecone client, (re)create the index if needed, return it."""
    from pinecone import Pinecone, ServerlessSpec

    api_key = os.environ.get("PINECONE_API_KEY", "").strip()
    if not api_key or api_key.startswith("PASTE_YOUR"):
        raise SystemExit(
            "PINECONE_API_KEY is not set. Paste your key into .env before running."
        )

    cloud = os.environ.get("PINECONE_CLOUD", "aws").strip() or "aws"
    region = os.environ.get("PINECONE_REGION", "us-east-1").strip() or "us-east-1"

    pinecone_client = Pinecone(api_key=api_key)
    existing_index_names = [index.name for index in pinecone_client.list_indexes()]

    if recreate and INDEX_NAME in existing_index_names:
        print(f"  Deleting existing index '{INDEX_NAME}' (--recreate)...")
        pinecone_client.delete_index(INDEX_NAME)
        existing_index_names.remove(INDEX_NAME)

    if INDEX_NAME not in existing_index_names:
        print(f"  Creating index '{INDEX_NAME}' ({EMBEDDING_DIMENSION}-dim, {SIMILARITY_METRIC}, {cloud}/{region})...")
        pinecone_client.create_index(
            name=INDEX_NAME,
            dimension=EMBEDDING_DIMENSION,
            metric=SIMILARITY_METRIC,
            spec=ServerlessSpec(cloud=cloud, region=region),
        )
        # Wait until the index is ready to accept upserts.
        while not pinecone_client.describe_index(INDEX_NAME).status["ready"]:
            time.sleep(1)
        print("  Index is ready.")
    else:
        print(f"  Reusing existing index '{INDEX_NAME}'.")

    return pinecone_client.Index(INDEX_NAME)


def upsert_in_batches(index, vector_records):
    """Upsert vector records into Pinecone in fixed-size batches."""
    total = len(vector_records)
    for batch_start in range(0, total, UPSERT_BATCH_SIZE):
        batch = vector_records[batch_start:batch_start + UPSERT_BATCH_SIZE]
        index.upsert(vectors=batch)
        print(f"  upserted {min(batch_start + UPSERT_BATCH_SIZE, total)}/{total}")


# Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Embed scraped chunks into Pinecone.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Load and embed nothing remote; just report chunk counts.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process the first N chunks (smoke test).")
    parser.add_argument("--recreate", action="store_true",
                        help="Delete and recreate the Pinecone index before upserting.")
    arguments = parser.parse_args()

    load_dotenv()

    print("Loading chunks...")
    chunks = load_all_chunks(limit=arguments.limit)
    print(f"Total chunks to process: {len(chunks)}")
    if not chunks:
        raise SystemExit("No chunks found. Run scrape_all.py / scrape_quests.py first.")

    if arguments.dry_run:
        approximate_tokens = sum(c["metadata"].get("token_estimate", 0) for c in chunks)
        print(f"[dry-run] {len(chunks)} chunks, ~{approximate_tokens} tokens. Exiting.")
        return

    print(f"Loading embedding model '{MODEL_NAME}' (first run downloads weights)...")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)

    print("Embedding chunk text...")
    chunk_texts = [chunk["text"] for chunk in chunks]
    embeddings = model.encode(
        chunk_texts,
        batch_size=EMBED_BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,  # unit vectors -> cosine == dot product
    )

    vector_records = []
    for chunk, embedding in zip(chunks, embeddings):
        vector_records.append({
            "id": chunk["chunk_id"],
            "values": embedding.tolist(),
            "metadata": build_pinecone_metadata(chunk),
        })

    print("Connecting to Pinecone...")
    index = get_pinecone_index(recreate=arguments.recreate)

    print(f"Upserting {len(vector_records)} vectors...")
    upsert_in_batches(index, vector_records)

    # Pinecone stats are eventually consistent; give it a moment then report.
    time.sleep(5)
    stats = index.describe_index_stats()
    print(f"Done. Index now reports {stats.get('total_vector_count', '?')} vectors.")


if __name__ == "__main__":
    main()

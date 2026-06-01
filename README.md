# Project Astra -- Genshin Impact Lore RAG

A Retrieval-Augmented Generation (RAG) pipeline that scrapes, chunks, and embeds Genshin Impact lore from the [Fandom Wiki](https://genshin-impact.fandom.com/), stores it in Pinecone, and answers lore questions with **Gemini 3.1 Flash-Lite** (Groq fallback).

## What it does

1. **Scrape** wiki content into per-entity JSON (characters, Archon Quest acts, World Quest Series).
2. **Chunk** text at semantic boundaries (~400 tokens) with structured metadata.
3. **Embed** chunks locally with `all-MiniLM-L6-v2` (384-dim) and upsert to a Pinecone index.
4. **Answer** questions by retrieving top-k chunks, applying a similarity threshold, and generating grounded answers with citations.

### Data sources

| Content | Entries | Chunks | Tokens |
|---------|---------|--------|--------|
| Playable characters | 124 files (~112+ with lore) | 4,400 | ~719K |
| Archon Quest acts | 44 | 310 | ~88K |
| World Quest Series | 63 series (47 with summaries) | 99 | ~23K |
| **Total embedded** | **~231** | **4,809** | **~830K** |

#### Playable characters

Four wiki page types per character:

| Page | Content | Method |
|------|---------|--------|
| Overview | Character metadata (element, weapon, region, constellation) | Infobox field extraction |
| `/Profile` | Character Stories (backstory entries gated by Friendship level) | `{{Character Story}}` template parser |
| `/Profile` | Wiki sections (Personality, Appearance, Trivia, etc.) | Heading-based splitting |
| `/Storyline` | Full narrative history across Archon/Story/World Quests | Heading-based splitting |
| `/Voice-Overs` | Lore-relevant dialogue lines (About X, More About, etc.) | `{{VO/Story}}` template parser |

#### Archon Quest acts

Act overview pages with narrative summaries for each chapter of the main story (Prologue through Song of the Welkin Moon). Metadata includes `region`, `chapter`, `act_number`, and `quest_type`.

#### World Quest Series

Important world quest chains (Aranyaka, Golden Slumber, Sacred Sakura Cleansing Ritual, etc.). Some series are marked "To be added" on the wiki and produce 0 chunks until updated.

### RAG stack (built)

| Layer | Choice |
|-------|--------|
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-dim, cosine) |
| Vector DB | Pinecone serverless index `genshin-lore` |
| Primary LLM | `gemini-3.1-flash-lite` via `google-genai` (temporary; 500 RPD free tier vs 20 on 2.5) |
| Fallback LLM | Groq `llama-3.3-70b-versatile` (on Gemini error) |
| Retrieval | Static top-k (default **10**), score threshold **0.35** |
| Guardrail | Refuse off-topic queries before any LLM call if top score &lt; 0.35 |

Each query is **single-turn** (no chat history passed to retrieval or generation yet).

## Project structure

```
project-astra/
  scraper/                   # Wiki scraping package
    wiki_api.py                # MediaWiki API client (rate-limited)
    parsers.py                 # Wikitext cleaner + template parsers
    chunking.py                # Section splitting, chunk sizing, VO grouping
    character_scraper.py       # Scrape one playable character end-to-end
    quest_scraper.py           # Archon Quest acts + World Quest Series
  rag/
    query_engine.py            # embed → retrieve → threshold → prompt → LLM
  data/
    characters/                # Per-character JSON (gitignored, regenerable)
    quests/archon/             # Archon Quest act summaries (gitignored)
    quests/world_series/       # World Quest Series summaries (gitignored)
  embed_all.py                 # Embed all chunks and upsert to Pinecone
  rag_test.py                  # Headless sanity check (batch + interactive)
  scrape_all.py                # CLI: scrape all playable characters
  scrape_quests.py             # CLI: scrape archon / world quest series
  requirements.txt
  .env.example                 # API key template (copy to .env)
```

## Quick start

### 1. Install dependencies

```bash
git clone https://github.com/<you>/genshin-impact-project-astra.git
cd genshin-impact-project-astra
pip install -r requirements.txt
```

### 2. Scrape lore (optional if you already have `data/`)

```bash
python scrape_all.py              # ~5 min -- all playable characters
python scrape_quests.py           # Archon Quest acts
python scrape_quests.py --world-quests
# or: python scrape_quests.py --all
```

Output lands in `data/characters/`, `data/quests/archon/`, and `data/quests/world_series/`, each with a `_manifest.json` index.

### 3. Configure API keys

Copy `.env.example` to `.env` and fill in:

| Variable | Required for |
|----------|----------------|
| `PINECONE_API_KEY` | Embedding upload + retrieval |
| `GEMINI_API_KEY` | Primary answer generation |
| `GROQ_API_KEY` | Fallback when Gemini fails (429, etc.) |

### 4. Embed and upload to Pinecone

```bash
python embed_all.py --dry-run     # count chunks only
python embed_all.py               # full upload (~4,809 vectors)
python embed_all.py --limit 50    # smoke test
python embed_all.py --recreate    # delete and recreate index first
```

First run downloads the embedding model weights (~90MB).

### 5. Ask questions (headless)

```bash
python rag_test.py              # scripted batch, then interactive loop
python rag_test.py --batch        # batch only
python rag_test.py --chat         # interactive only
```

Programmatic use:

```python
from rag.query_engine import ask, retrieve

# Full RAG answer
result = ask("What happened to Sandrone in the True Moon quest?")
print(result["answer"], result["model_used"], result["top_score"])

# Optional metadata filter (Pinecone)
result = ask(
    "What happened in Sumeru?",
    filters={"page_type": "archon_quest", "region": "Sumeru"},
)

# Retrieval only
sources = retrieve("Who is Columbina?", top_k=5)
```

## Design decisions

### Why per-entity JSON files?

- **Incremental updates** -- re-scrape one character or quest after a patch
- **Debugging** -- inspect one file instead of a monolithic blob
- **Memory** -- embed pipeline iterates file-by-file
- **Git-friendly** -- diffs show exactly what changed (data dirs are gitignored)

### Why MediaWiki API instead of HTML scraping?

The Fandom wiki exposes `action=query&prop=revisions` wikitext. This is more stable than rendered HTML, gives direct access to template parameters, and avoids a headless browser.

### Why section-based chunking?

Fixed-size chunking splits mid-paragraph or mid-story. Section-based chunking keeps Character Stories, voice-over groupings, and wiki headings intact. Each chunk is prefixed with `Entity -- Section` so embeddings carry context.

### Why Pinecone + local embeddings?

- Embeddings run locally (`sentence-transformers`) -- no embedding API cost
- Pinecone free tier covers ~4,800 vectors comfortably
- Chunk `text` is stored in vector metadata so retrieval returns LLM-ready context without a second DB lookup

### Why Gemini + Groq fallback?

- Flash-Lite is sufficient for closed-book RAG (facts come from retrieved chunks); using 3.1 for higher free-tier daily quota
- Groq provides a free-tier fallback when Gemini rate-limits or billing blocks requests
- Temperature 0.3 keeps answers grounded in context

### Why a score threshold before the LLM?

Off-topic queries (e.g. "write me a Python web server") can still retrieve weakly related wiki chunks. Refusing when the best cosine score is below **0.35** blocks LLM proxy abuse without calling an LLM. On-topic lore questions typically score **0.55+**; off-topic tops out near **0.32**.

## Chunk structure

Each chunk in the scraped JSON:

```json
{
  "chunk_id": "7695fc6e9c95",
  "text": "Columbina -- Character Details\n\nDay or night? Why, night, of course...",
  "metadata": {
    "page_type": "character",
    "character_name": "Columbina",
    "element": "Hydro",
    "region": "Nod-Krai",
    "source_page": "Columbina/Profile",
    "source_url": "https://genshin-impact.fandom.com/wiki/Columbina/Profile",
    "section": "Character Details",
    "section_type": "character_story",
    "token_estimate": 398
  }
}
```

Archon quest chunks use `page_type: archon_quest`, `quest_title`, `chapter`, `act_number`, and `region` instead of `character_name`. Metadata supports Pinecone filters -- e.g. `region`, `element`, `page_type`.

Vector IDs in Pinecone equal `chunk_id`. Re-scraping overwrites vectors with the same ID; shrinking or renaming sections can leave orphaned vectors (see plan note: delete-by-metadata-filter before re-embed when doing incremental sync).

## Known limitations

- **Single-turn Q&A** -- prior chat turns are not sent to retrieval or the LLM. Follow-ups like "tell me more about her" need the subject repeated or future chat-history support.
- **Broad regional questions** -- e.g. "What happened in the Sumeru archon quests?" may retrieve character voice lines or other regions' lore unless you pass `filters={"page_type": "archon_quest", "region": "Sumeru"}`.
- **No automatic filter extraction** -- region/element filters must be passed explicitly (planned for the web UI dropdowns).
- **Static top-k** -- default 10 chunks (~4K tokens of context); dynamic score-threshold k is a future improvement.

## Roadmap

- [x] Wiki scraper for playable characters (Profile, Storyline, Voice-Overs)
- [x] Wiki scraper for Archon Quest acts and World Quest Series
- [x] Embedding pipeline (Pinecone + sentence-transformers)
- [x] RAG query engine (Gemini 3.1 Flash-Lite + Groq fallback)
- [x] Score-threshold refusal (anti-proxy guardrail)
- [x] Headless sanity check (`rag_test.py`)
- [ ] Metadata-aware retrieval (auto region/type filters, query rewriting)
- [ ] Chat history (last N turns + standalone retrieval query)
- [ ] Rate limiting module + FastAPI web UI
- [ ] Expand scraping (NPCs, additional lore pages)
- [ ] Deploy to cloud

## Data source

All lore content is scraped from the [Genshin Impact Fandom Wiki](https://genshin-impact.fandom.com/), licensed under [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Genshin Impact is a trademark of HoYoverse. This project is a fan-made tool and is not affiliated with or endorsed by HoYoverse.

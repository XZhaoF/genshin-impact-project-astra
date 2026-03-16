# Project Astra -- Genshin Impact Lore RAG

A Retrieval-Augmented Generation (RAG) pipeline that scrapes, chunks, and structures Genshin Impact lore from the [Fandom Wiki](https://genshin-impact.fandom.com/) for semantic search and LLM-powered Q&A.

## What it does

The scraper extracts lore from **three content types**:

### Playable characters (112 characters, 4,400 chunks)

Four wiki page types per character:

| Page | Content | Method |
|------|---------|--------|
| Overview | Character metadata (element, weapon, region, constellation) | Infobox field extraction |
| `/Profile` | Character Stories (backstory entries gated by Friendship level) | `{{Character Story}}` template parser |
| `/Profile` | Wiki sections (Personality, Appearance, Trivia, etc.) | Heading-based splitting |
| `/Storyline` | Full narrative history across Archon/Story/World Quests | Heading-based splitting |
| `/Voice-Overs` | Lore-relevant dialogue lines (About X, More About, etc.) | `{{VO/Story}}` template parser |

### Archon Quest acts (44 acts, 310 chunks)

Act overview pages with narrative summaries for each chapter of the main story (Prologue through Song of the Welkin Moon).

### World Quest Series (47 of 63 series, 99 chunks)

Important world quest chains (Aranyaka, Golden Slumber, Sacred Sakura Cleansing Ritual, etc.). Some series are marked "To be added" on the wiki and produce 0 chunks until updated.

Each piece of content is cleaned from wikitext markup, split into chunks sized for embedding (~400 tokens), and annotated with structured metadata for filtered retrieval.

### Current stats

| Content | Entries | Chunks | Tokens |
|---------|---------|--------|--------|
| Playable characters | 112 | 4,400 | ~719K |
| Archon Quest acts | 44 | 310 | ~88K |
| World Quest Series | 47 | 99 | ~23K |
| **Total** | **203** | **4,809** | **~830K** |

## Project structure

```
project-astra/
  scraper/
    __init__.py              # Package exports
    wiki_api.py              # MediaWiki API client (rate-limited session)
    parsers.py               # Wikitext cleaner + template parsers
    chunking.py              # Section splitting, chunk sizing, VO grouping
    character_scraper.py     # Orchestrator: scrape one character end-to-end
    quest_scraper.py         # Archon Quest acts + World Quest Series scraper
  data/
    characters/              # Per-character JSON files (generated, gitignored)
    quests/
      archon/                # Archon Quest act summaries (gitignored)
      world_series/          # World Quest Series summaries (gitignored)
  scrape_all.py              # CLI: scrape all playable characters
  scrape_quests.py          # CLI: scrape archon quests and/or world quest series
```

## Quick start

```bash
# Clone and install dependencies
git clone https://github.com/<you>/genshin-impact-project-astra.git
cd genshin-impact-project-astra
pip install requests

# Scrape playable characters (~5 min)
python scrape_all.py

# Scrape Archon Quest acts (~1 min)
python scrape_quests.py

# Scrape World Quest Series (~2 min)
python scrape_quests.py --world-quests

# Or scrape everything
python scrape_quests.py --all

# Single-item scrape
python scrape_all.py --character "Raiden Shogun"
python scrape_quests.py --quest "True Moon"
python scrape_quests.py --world-quests --quest "Golden Slumber"

# Re-scrape existing
python scrape_all.py --force
python scrape_quests.py --force
```

Output lands in `data/characters/`, `data/quests/archon/`, and `data/quests/world_series/`, each with a `_manifest.json` index.

## Design decisions

### Why per-character JSON files instead of one big file?

- **Incremental updates** -- when a game patch adds new lore, re-scrape one character instead of all 112
- **Debugging** -- inspect one file to verify output, not search a 50K-line blob
- **Memory** -- the embedding pipeline can iterate file-by-file without loading everything into RAM
- **Git-friendly** -- diffs show exactly which characters changed

### Why MediaWiki API instead of HTML scraping?

The Fandom wiki exposes a `action=query&prop=revisions` API that returns raw wikitext. This is more stable than parsing rendered HTML (which changes with skin updates), gives us access to template parameters directly, and avoids needing a headless browser.

### Why section-based chunking instead of fixed-size?

Fixed-size chunking (e.g., 512 tokens) ignores semantic boundaries -- a chunk might start mid-paragraph or split a story in half. Section-based chunking preserves the natural structure of the wiki:

- Character Stories stay as complete narrative units
- Voice-over lines are grouped by lore relevance (individual "About X" lines vs. batched ambient lines)
- Wiki sections like Personality, Trivia, and Etymology keep their headings as context

Each chunk is prefixed with `Character Name -- Section Title` so the embedding captures both the content and its context.

### Why separate lore-rich voice lines from ambient ones?

Voice-over pages contain 40-60 lines per character, but they vary hugely in lore value:

- **"About Furina"** -- a paragraph of character analysis (high lore value, kept as individual chunk)
- **"Good Morning"** -- "Morning! Let's go." (low lore value, batched with similar lines)

The scraper classifies lines by title pattern (`About X`, `More About`, `Something to Share`, etc.) and word count, keeping lore-rich lines as individual vectors while batching short ambient lines to avoid hundreds of tiny, low-signal embeddings.

## Chunk structure

Each chunk in the JSON output looks like:

```json
{
  "chunk_id": "7695fc6e9c95",
  "text": "Columbina -- Character Details\n\nDay or night? Why, night, of course...",
  "metadata": {
    "page_type": "character",
    "character_name": "Columbina",
    "quality": "5",
    "weapon_type": "Catalyst",
    "element": "Hydro",
    "constellation": "Columbina Hyposelenia",
    "region": "Nod-Krai",
    "source_page": "Columbina/Profile",
    "source_url": "https://genshin-impact.fandom.com/wiki/Columbina/Profile",
    "section": "Character Details",
    "section_type": "character_story",
    "mentions": [],
    "token_estimate": 398
  }
}
```

Metadata fields enable filtered retrieval -- e.g., search only Mondstadt characters, or only voice-over chunks that mention a specific character.

## Roadmap

- [x] Wiki scraper for 112+ playable characters (Profile, Storyline, Voice-Overs)
- [x] Wiki scraper for 44 Archon Quest acts
- [x] Wiki scraper for 63 World Quest Series (47 with summaries)
- [ ] Embedding pipeline (Pinecone + sentence-transformers) for all ~4,800 chunks
- [ ] RAG query engine (Gemini 2.5 Flash-Lite + Groq fallback)
- [ ] Anti-proxy guardrails and web frontend
- [ ] Expand scraping to NPCs and additional lore pages

## Data source

All lore content is scraped from the [Genshin Impact Fandom Wiki](https://genshin-impact.fandom.com/), which is licensed under [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Genshin Impact is a trademark of HoYoverse. This project is a fan-made tool and is not affiliated with or endorsed by HoYoverse.

# Project Astra -- Genshin Impact Lore RAG

A Retrieval-Augmented Generation (RAG) pipeline that scrapes, chunks, and structures character lore from the [Genshin Impact Fandom Wiki](https://genshin-impact.fandom.com/) for semantic search and LLM-powered Q&A.

## What it does

The scraper extracts lore from **four wiki page types** per character:

| Page | Content | Method |
|------|---------|--------|
| Overview | Character metadata (element, weapon, region, constellation) | Infobox field extraction |
| `/Profile` | Character Stories (backstory entries gated by Friendship level) | `{{Character Story}}` template parser |
| `/Profile` | Wiki sections (Personality, Appearance, Trivia, etc.) | Heading-based splitting |
| `/Storyline` | Full narrative history across Archon/Story/World Quests | Heading-based splitting |
| `/Voice-Overs` | Lore-relevant dialogue lines (About X, More About, etc.) | `{{VO/Story}}` template parser |

Each piece of content is cleaned from wikitext markup, split into chunks sized for embedding (~400 tokens), and annotated with structured metadata for filtered retrieval.

### Current stats

- **112** playable characters with lore content
- **4,400** chunks across all characters
- **~719K** tokens total
- **4 chunk types**: `character_story`, `wiki_section`, `voice_over`, `voice_over_batch`

## Project structure

```
project-astra/
  scraper/
    __init__.py              # Package exports
    wiki_api.py              # MediaWiki API client (rate-limited session)
    parsers.py               # Wikitext cleaner + template parsers
    chunking.py              # Section splitting, chunk sizing, VO grouping
    character_scraper.py     # Orchestrator: scrape one character end-to-end
  data/
    characters/              # Per-character JSON files (generated, gitignored)
  scrape_all.py              # CLI entry point to scrape all characters
```

## Quick start

```bash
# Clone and install dependencies
git clone https://github.com/<you>/genshin-impact-project-astra.git
cd genshin-impact-project-astra
pip install requests

# Scrape all 112+ playable characters (~5 min with rate limiting)
python scrape_all.py

# Scrape a single character
python scrape_all.py --character "Raiden Shogun"

# Re-scrape even if JSON already exists
python scrape_all.py --force

# Test mode: scrape 4 characters only
python scrape_all.py --test
```

Output lands in `data/characters/` as one JSON file per character, plus a `_manifest.json` index.

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

- [ ] Embedding pipeline (Pinecone + sentence-transformers)
- [ ] Query interface with LLM-generated answers (Groq)
- [ ] Expand scraping to non-playable characters, quests, and world lore
- [ ] Web frontend for public access

## Data source

All lore content is scraped from the [Genshin Impact Fandom Wiki](https://genshin-impact.fandom.com/), which is licensed under [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Genshin Impact is a trademark of HoYoverse. This project is a fan-made tool and is not affiliated with or endorsed by HoYoverse.

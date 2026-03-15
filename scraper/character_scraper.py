"""
Character scraper orchestrator.

Fetches and chunks data for a single character across all page types:
overview (metadata), Profile, Storyline, and Voice-Overs.
"""

import json
from datetime import datetime, timezone

from scraper.wiki_api import get_page_wikitext
from scraper.parsers import (
    extract_character_stories,
    extract_voice_overs,
    extract_character_metadata,
)
from scraper.chunking import (
    make_chunks_from_template_entries,
    chunk_wiki_sections,
    group_voice_overs_into_chunks,
)


def scrape_character(character_name: str, verbose: bool = True) -> dict:
    """Scrape all lore pages for a single character and return structured data.

    Returns a dict ready to be serialized as JSON:
        {
            character_name, scraped_at, metadata, pages_scraped,
            stats: { total_chunks, total_tokens, by_type },
            chunks: [ ... ]
        }
    """
    log = print if verbose else lambda *a, **k: None

    log(f"\n{'=' * 70}")
    log(f"SCRAPING: {character_name}")
    log(f"{'=' * 70}")

    # ------------------------------------------------------------------
    # Step 1: Overview page -- metadata only
    # ------------------------------------------------------------------
    log(f"  [1] Fetching {character_name} overview (metadata)...")
    overview_wikitext = get_page_wikitext(character_name)
    if not overview_wikitext:
        log(f"      ERROR: Could not fetch {character_name}")
        return _empty_result(character_name)

    metadata = extract_character_metadata(overview_wikitext, character_name)
    log(f"      {json.dumps(metadata, ensure_ascii=False)}")

    all_chunks = []
    pages_scraped = []

    # ------------------------------------------------------------------
    # Step 2: Profile page -- Character Stories + wiki sections
    # ------------------------------------------------------------------
    log(f"  [2] Fetching {character_name}/Profile...")
    profile_wikitext = get_page_wikitext(f"{character_name}/Profile")
    if profile_wikitext:
        pages_scraped.append("Profile")
        log(f"      {len(profile_wikitext):,} chars")

        stories = extract_character_stories(profile_wikitext)
        log(f"      Character Stories: {len(stories)} entries")
        story_chunks = make_chunks_from_template_entries(
            stories, character_name, metadata, "Profile", "character_story",
        )
        section_chunks = chunk_wiki_sections(
            profile_wikitext, character_name, metadata, "Profile",
        )

        all_chunks.extend(story_chunks)
        all_chunks.extend(section_chunks)
        log(f"      -> {len(story_chunks)} story chunks + {len(section_chunks)} section chunks")
    else:
        log(f"      Not found (skipping)")

    # ------------------------------------------------------------------
    # Step 3: Storyline page -- full narrative history
    # ------------------------------------------------------------------
    log(f"  [3] Fetching {character_name}/Storyline...")
    storyline_wikitext = get_page_wikitext(f"{character_name}/Storyline")
    if storyline_wikitext:
        pages_scraped.append("Storyline")
        log(f"      {len(storyline_wikitext):,} chars")
        storyline_chunks = chunk_wiki_sections(
            storyline_wikitext, character_name, metadata, "Storyline",
        )
        all_chunks.extend(storyline_chunks)
        log(f"      -> {len(storyline_chunks)} storyline chunks")
    else:
        log(f"      Not found (skipping)")

    # ------------------------------------------------------------------
    # Step 4: Voice-Overs page
    # ------------------------------------------------------------------
    log(f"  [4] Fetching {character_name}/Voice-Overs...")
    vo_wikitext = get_page_wikitext(f"{character_name}/Voice-Overs")
    if vo_wikitext:
        pages_scraped.append("Voice-Overs")
        log(f"      {len(vo_wikitext):,} chars")
        voice_overs = extract_voice_overs(vo_wikitext)
        log(f"      Voice-Over lines: {len(voice_overs)} lore-relevant entries")

        vo_chunks = group_voice_overs_into_chunks(
            voice_overs, character_name, metadata,
        )
        all_chunks.extend(vo_chunks)

        lore_count = sum(
            1 for c in vo_chunks if c["metadata"]["section_type"] == "voice_over"
        )
        batch_count = sum(
            1 for c in vo_chunks if c["metadata"]["section_type"] == "voice_over_batch"
        )
        log(f"      -> {lore_count} individual lore VO chunks + {batch_count} batched ambient chunks")
    else:
        log(f"      Not found (skipping)")

    # ------------------------------------------------------------------
    # Build final result
    # ------------------------------------------------------------------
    total_tokens = sum(c["metadata"]["token_estimate"] for c in all_chunks)
    log(f"\n  TOTAL: {len(all_chunks)} chunks, {total_tokens:,} tokens")

    by_type = {}
    for chunk in all_chunks:
        section_type = chunk["metadata"]["section_type"]
        by_type[section_type] = by_type.get(section_type, 0) + 1

    return {
        "character_name": character_name,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "pages_scraped": pages_scraped,
        "stats": {
            "total_chunks": len(all_chunks),
            "total_tokens": total_tokens,
            "by_type": by_type,
        },
        "chunks": all_chunks,
    }


def _empty_result(character_name: str) -> dict:
    """Return an empty result structure for characters that couldn't be fetched."""
    return {
        "character_name": character_name,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"page_type": "character", "character_name": character_name},
        "pages_scraped": [],
        "stats": {"total_chunks": 0, "total_tokens": 0, "by_type": {}},
        "chunks": [],
    }

"""
Quest scraper for Archon Quest Acts and World Quest Series.

Fetches quest overview pages from the Genshin Impact Fandom Wiki,
extracts infobox metadata and the Summary narrative, then chunks
for RAG embedding.
"""

import re
import json
from datetime import datetime, timezone
from typing import Optional

from scraper.wiki_api import get_page_wikitext
from scraper.parsers import (
    clean_wikitext,
    extract_infobox_field,
    find_template_block,
    SKIP_SECTIONS,
)
from scraper.chunking import split_if_too_long

import hashlib


_TBA_PATTERNS = re.compile(
    r"^\s*(?:''')?\(?(?:To be added|TBA|Upcoming)\.?\)?\s*(?:''')?\.?\s*$",
    re.IGNORECASE,
)


def extract_quest_metadata(wikitext: str, quest_title: str) -> dict:
    """Extract metadata from the {{Act Infobox}} template."""
    return {
        "page_type": "archon_quest",
        "quest_title": quest_title,
        "quest_type": extract_infobox_field(wikitext, "type") or "Archon",
        "chapter": extract_infobox_field(wikitext, "chapter"),
        "act_number": extract_infobox_field(wikitext, "actNum"),
        "region": extract_infobox_field(wikitext, "region"),
        "prev_act": extract_infobox_field(wikitext, "prev"),
        "next_act": extract_infobox_field(wikitext, "next"),
        "ar_requirement": extract_infobox_field(wikitext, "ARReq"),
    }


def extract_world_quest_metadata(wikitext: str, quest_title: str) -> dict:
    """Extract metadata from the {{Chapter Infobox}} template."""
    return {
        "page_type": "world_quest_series",
        "quest_title": quest_title,
        "quest_type": "World",
        "region": extract_infobox_field(wikitext, "region"),
        "prev_quest": extract_infobox_field(wikitext, "prev"),
        "next_quest": extract_infobox_field(wikitext, "next"),
    }


def extract_quest_list(wikitext: str) -> list[str]:
    """Extract the ordered list of individual quests/acts from the page.

    Handles both ==Quests== (Archon Quest Acts) and ==List of Acts== (World Quest Series).
    """
    for heading in [r"Quests", r"List of Acts"]:
        match = re.search(rf"==\s*{heading}\s*==\s*\n(.*?)(?=\n==)", wikitext, re.DOTALL)
        if match:
            break
    else:
        return []

    quests_text = match.group(1)
    quests = []
    for line in quests_text.split("\n"):
        link_match = re.search(r"\[\[([^|\]]+?)(?:\|[^\]]+)?\]\]", line)
        if link_match and (line.strip().startswith("#") or line.strip().startswith("*")):
            quests.append(link_match.group(1))
    return quests


def extract_summary_section(wikitext: str) -> Optional[str]:
    """Extract the raw text of the ==Summary== section.

    Stops at the next level-2 heading (==Foo==) but not at level-3
    sub-headings (===Bar===) which are part of the summary content.
    """
    match = re.search(
        r"==\s*Summary\s*==\s*\n(.*?)(?=\n==[^=])",
        wikitext, re.DOTALL,
    )
    if not match:
        match = re.search(
            r"==\s*Summary\s*==\s*\n(.*)",
            wikitext, re.DOTALL,
        )
    if not match:
        return None
    return match.group(1).strip()


def chunk_quest_summary(
    summary_raw: str,
    quest_title: str,
    base_metadata: dict,
    max_chunk_tokens: int = 400,
) -> list[dict]:
    """Chunk the Summary section into RAG-ready chunks.

    Long summaries are split on ;Sub-heading markers (semicolon headers)
    and ---- separators first, then by paragraph if still too long.
    """
    source_url = (
        f"https://genshin-impact.fandom.com/wiki/"
        f"{quest_title.replace(' ', '_')}"
    )

    cleaned = clean_wikitext(summary_raw)
    if not cleaned or len(cleaned.split()) < 10:
        return []

    sub_sections = _split_summary_into_subsections(cleaned)
    chunks = []

    for section_name, section_text in sub_sections:
        if len(section_text.split()) < 10:
            continue

        display_section = f"Summary > {section_name}" if section_name else "Summary"
        text_parts = split_if_too_long(section_text, max_chunk_tokens)

        for chunk_index, part in enumerate(text_parts):
            prefixed_text = f"{quest_title} -- {display_section}\n\n{part}"
            chunk_id = hashlib.md5(
                f"{quest_title}:summary:{display_section}:{chunk_index}".encode()
            ).hexdigest()[:12]

            chunks.append({
                "chunk_id": chunk_id,
                "text": prefixed_text,
                "metadata": {
                    **base_metadata,
                    "source_page": quest_title,
                    "source_url": source_url,
                    "section": display_section,
                    "section_type": "quest_summary",
                    "heading_level": 2,
                    "chunk_index": chunk_index,
                    "token_estimate": len(prefixed_text.split()),
                },
            })

    return chunks


def _split_summary_into_subsections(text: str) -> list[tuple[str, str]]:
    """Split a summary by sub-headings into (name, text) tuples.

    Handles three formats:
      - ;Heading markers (Archon Quests, some World Quests)
      - ===Heading=== wiki sub-headings (some World Quest Series)
      - Plain text with no sub-headings (returns as single section)
    """
    text = re.sub(r"\n-{4,}\n?", "\n", text)

    has_semicolons = bool(re.search(r"(?:^|\n);.+\n", text))
    has_triple_equals = bool(re.search(r"(?:^|\n)===.+===\s*\n", text))

    if has_triple_equals:
        return _split_on_pattern(text, r"\n(===.+===)\s*\n", strip_equals=True)
    elif has_semicolons:
        if text.startswith(";"):
            text = "\n" + text
        return _split_on_pattern(text, r"\n;(.+)\n")
    else:
        return [("", text.strip())]


def _split_on_pattern(
    text: str, pattern: str, strip_equals: bool = False,
) -> list[tuple[str, str]]:
    """Generic splitter: split text on a heading regex pattern."""
    if strip_equals and text.lstrip().startswith("==="):
        text = "\n" + text

    parts = re.split(pattern, text)

    if len(parts) == 1:
        return [("", text.strip())]

    sub_sections = []

    if parts[0].strip():
        sub_sections.append(("", parts[0].strip()))

    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        if strip_equals:
            heading = heading.strip("= ")
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            sub_sections.append((heading, body))

    return sub_sections


def scrape_quest_act(quest_title: str, verbose: bool = True) -> dict:
    """Scrape a single Archon Quest act page and return structured data."""
    log = print if verbose else lambda *a, **k: None

    log(f"  Fetching: {quest_title}")
    wikitext = get_page_wikitext(quest_title)
    if not wikitext:
        log(f"    ERROR: Could not fetch page")
        return _empty_result(quest_title, "archon_quest")

    metadata = extract_quest_metadata(wikitext, quest_title)
    chapter_display = metadata.get("chapter") or "Unknown"
    act_display = metadata.get("act_number") or "?"
    log(f"    {chapter_display} Act {act_display} | Region: {metadata.get('region', 'N/A')}")

    quest_list = extract_quest_list(wikitext)
    log(f"    Individual quests: {len(quest_list)}")

    summary_raw = extract_summary_section(wikitext)
    chunks = []

    if summary_raw:
        log(f"    Summary: {len(summary_raw):,} chars")
        chunks = chunk_quest_summary(summary_raw, quest_title, metadata)
        log(f"    -> {len(chunks)} chunks")
    else:
        log(f"    No Summary section found")

    total_tokens = sum(c["metadata"]["token_estimate"] for c in chunks)

    return {
        "quest_title": quest_title,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "quest_list": quest_list,
        "stats": {
            "total_chunks": len(chunks),
            "total_tokens": total_tokens,
        },
        "chunks": chunks,
    }


def scrape_world_quest_series(quest_title: str, verbose: bool = True) -> dict:
    """Scrape a single World Quest Series page and return structured data."""
    log = print if verbose else lambda *a, **k: None

    log(f"  Fetching: {quest_title}")
    wikitext = get_page_wikitext(quest_title)
    if not wikitext:
        log(f"    ERROR: Could not fetch page")
        return _empty_result(quest_title, "world_quest_series")

    metadata = extract_world_quest_metadata(wikitext, quest_title)
    log(f"    Region: {metadata.get('region', 'N/A')}")

    quest_list = extract_quest_list(wikitext)
    log(f"    Acts: {len(quest_list)}")

    summary_raw = extract_summary_section(wikitext)
    chunks = []

    if summary_raw:
        cleaned_check = clean_wikitext(summary_raw)
        if _TBA_PATTERNS.match(cleaned_check):
            log(f"    Summary: marked as 'To be added' -- skipping")
        else:
            log(f"    Summary: {len(summary_raw):,} chars")
            chunks = chunk_quest_summary(summary_raw, quest_title, metadata)
            log(f"    -> {len(chunks)} chunks")
    else:
        log(f"    No Summary section found")

    total_tokens = sum(c["metadata"]["token_estimate"] for c in chunks)

    return {
        "quest_title": quest_title,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "quest_list": quest_list,
        "stats": {
            "total_chunks": len(chunks),
            "total_tokens": total_tokens,
        },
        "chunks": chunks,
    }


def _empty_result(quest_title: str, page_type: str = "archon_quest") -> dict:
    return {
        "quest_title": quest_title,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"page_type": page_type, "quest_title": quest_title},
        "quest_list": [],
        "stats": {"total_chunks": 0, "total_tokens": 0},
        "chunks": [],
    }

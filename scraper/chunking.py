"""
Chunking and splitting logic for RAG data.

Converts parsed wikitext sections and template entries into
appropriately-sized chunks with metadata for vector storage.
"""

import re
import hashlib

from scraper.parsers import clean_wikitext, SKIP_SECTIONS


# ---------------------------------------------------------------------------
# Text splitting utilities
# ---------------------------------------------------------------------------

def split_if_too_long(text: str, max_tokens: int = 400) -> list[str]:
    """Split text into chunks that fit within max_tokens (word count proxy).

    First tries paragraph breaks (double newline), then falls back to
    single newline breaks for dense bulleted lists.
    """
    words = text.split()
    if len(words) <= max_tokens:
        return [text]

    result = _split_on_separator(text, "\n\n", max_tokens)
    if all(len(chunk.split()) <= max_tokens for chunk in result):
        return result

    final = []
    for chunk in result:
        if len(chunk.split()) > max_tokens:
            sub = _split_on_separator(chunk, "\n", max_tokens)
            final.extend(sub)
        else:
            final.append(chunk)
    return final


def _split_on_separator(text: str, separator: str, max_tokens: int) -> list[str]:
    """Greedily merge text parts (split by separator) up to max_tokens."""
    parts = text.split(separator)
    sub_chunks = []
    current_chunk = ""

    for part in parts:
        candidate = (current_chunk + separator + part).strip()
        if len(candidate.split()) > max_tokens and current_chunk.strip():
            sub_chunks.append(current_chunk.strip())
            current_chunk = part
        else:
            current_chunk = candidate

    if current_chunk.strip():
        sub_chunks.append(current_chunk.strip())

    return sub_chunks if sub_chunks else [text]


# ---------------------------------------------------------------------------
# Template-entry chunking (Character Stories, Voice-Overs as individual)
# ---------------------------------------------------------------------------

def make_chunks_from_template_entries(
    entries: list[dict],
    character_name: str,
    base_metadata: dict,
    source_page: str,
    section_type: str,
    max_chunk_tokens: int = 400,
) -> list[dict]:
    """Create chunks from a list of parsed template entries (stories or VOs)."""
    chunks = []
    source_url = (
        f"https://genshin-impact.fandom.com/wiki/"
        f"{character_name.replace(' ', '_')}/{source_page}"
    )

    for entry in entries:
        entry_metadata = {
            **base_metadata,
            "source_page": f"{character_name}/{source_page}",
            "source_url": source_url,
            "section": entry["title"],
            "section_type": section_type,
            "heading_level": 3,
            "mentions": entry.get("mentions", []),
        }
        if entry.get("friendship_level"):
            entry_metadata["friendship_level"] = entry["friendship_level"]
        if entry.get("quest_requirement"):
            entry_metadata["quest_requirement"] = entry["quest_requirement"]

        prefix = f"{character_name} -- {entry['title']}"
        body_chunks = split_if_too_long(entry["text"], max_chunk_tokens)

        for chunk_index, body_chunk in enumerate(body_chunks):
            full_text = f"{prefix}\n\n{body_chunk}"
            chunk_id = hashlib.md5(
                f"{character_name}:{section_type}:{entry['title']}:{chunk_index}".encode()
            ).hexdigest()[:12]
            chunks.append({
                "chunk_id": chunk_id,
                "text": full_text,
                "metadata": {
                    **entry_metadata,
                    "chunk_index": chunk_index,
                    "token_estimate": len(full_text.split()),
                },
            })

    return chunks


# ---------------------------------------------------------------------------
# Wiki section-based chunking
# ---------------------------------------------------------------------------

def chunk_wiki_sections(
    wikitext: str,
    character_name: str,
    base_metadata: dict,
    source_page: str,
    max_chunk_tokens: int = 400,
) -> list[dict]:
    """Chunk cleaned wikitext by == and === headings."""
    cleaned = clean_wikitext(wikitext)
    source_url = (
        f"https://genshin-impact.fandom.com/wiki/"
        f"{character_name.replace(' ', '_')}/{source_page}"
    )

    chunks = []
    parts = re.split(r"\n(={2,})(.+?)\1\n", cleaned)

    current_section = "Introduction"
    current_level = 2
    parent_section = None

    for i, part in enumerate(parts):
        if i % 3 == 1:
            current_level = len(part)
            continue
        elif i % 3 == 2:
            section_name = part.strip()
            if current_level == 2:
                parent_section = section_name
            current_section = section_name
            continue

        text = part.strip()

        if current_level >= 3 and parent_section:
            display_section = f"{parent_section} > {current_section}"
        else:
            display_section = current_section

        if current_section in SKIP_SECTIONS:
            continue
        if parent_section and parent_section in SKIP_SECTIONS:
            continue
        if current_section == "Character Stories" and len(text.split()) < 50:
            continue
        if current_section == "Story":
            continue
        if len(text.split()) < 10:
            continue

        sub_chunks = split_if_too_long(text, max_chunk_tokens)
        for chunk_index, sub_chunk in enumerate(sub_chunks):
            prefixed_text = f"{character_name} -- {display_section}\n\n{sub_chunk}"
            chunk_id = hashlib.md5(
                f"{character_name}:{source_page}:{display_section}:{chunk_index}".encode()
            ).hexdigest()[:12]

            chunks.append({
                "chunk_id": chunk_id,
                "text": prefixed_text,
                "metadata": {
                    **base_metadata,
                    "source_page": f"{character_name}/{source_page}",
                    "source_url": source_url,
                    "section": display_section,
                    "section_type": "wiki_section",
                    "heading_level": current_level,
                    "chunk_index": chunk_index,
                    "token_estimate": len(sub_chunk.split()),
                },
            })

    return chunks


# ---------------------------------------------------------------------------
# Voice-Over grouping (individual lore lines + batched ambient lines)
# ---------------------------------------------------------------------------

def group_voice_overs_into_chunks(
    voice_overs: list[dict],
    character_name: str,
    base_metadata: dict,
    max_chunk_tokens: int = 400,
) -> list[dict]:
    """Group voice-over lines into chunks.

    Lore-rich "About X" lines stay individual. Short ambient lines
    get batched together to avoid hundreds of tiny vectors.
    """
    individual_lines = []
    batch_lines = []

    for voice_over in voice_overs:
        title = voice_over["title"]
        is_lore_rich = (
            title.startswith("About ")
            or title.startswith("More About ")
            or "Something to Share" in title
            or "Interesting Things" in title
            or "Birthday" in title
            or "Hobbies" in title
            or "Troubles" in title
        )

        if is_lore_rich and len(voice_over["text"].split()) > 30:
            individual_lines.append(voice_over)
        else:
            batch_lines.append(voice_over)

    chunks = []
    source_url = (
        f"https://genshin-impact.fandom.com/wiki/"
        f"{character_name.replace(' ', '_')}/Voice-Overs"
    )

    for voice_over in individual_lines:
        prefix = f"{character_name} (Voice-Over) -- {voice_over['title']}"
        full_text = f"{prefix}\n\n{voice_over['text']}"
        chunk_id = hashlib.md5(
            f"{character_name}:vo:{voice_over['title']}".encode()
        ).hexdigest()[:12]

        chunks.append({
            "chunk_id": chunk_id,
            "text": full_text,
            "metadata": {
                **base_metadata,
                "source_page": f"{character_name}/Voice-Overs",
                "source_url": source_url,
                "section": voice_over["title"],
                "section_type": "voice_over",
                "heading_level": 3,
                "mentions": voice_over.get("mentions", []),
                "chunk_index": 0,
                "token_estimate": len(full_text.split()),
            },
        })

    if batch_lines:
        current_batch_text = ""
        batch_index = 0

        for voice_over in batch_lines:
            line = f"[{voice_over['title']}]: {voice_over['text']}"
            candidate = (current_batch_text + "\n" + line).strip()

            if len(candidate.split()) > max_chunk_tokens and current_batch_text.strip():
                chunks.append(_make_batch_chunk(
                    character_name, base_metadata, source_url,
                    current_batch_text.strip(), batch_index,
                ))
                current_batch_text = line
                batch_index += 1
            else:
                current_batch_text = candidate

        if current_batch_text.strip():
            chunks.append(_make_batch_chunk(
                character_name, base_metadata, source_url,
                current_batch_text.strip(), batch_index,
            ))

    return chunks


def _make_batch_chunk(
    character_name: str,
    base_metadata: dict,
    source_url: str,
    batch_text: str,
    batch_index: int,
) -> dict:
    """Create a single batched voice-over chunk."""
    prefix = f"{character_name} (Voice-Overs) -- Ambient & Misc"
    full_text = f"{prefix}\n\n{batch_text}"
    chunk_id = hashlib.md5(
        f"{character_name}:vo_batch:{batch_index}".encode()
    ).hexdigest()[:12]

    return {
        "chunk_id": chunk_id,
        "text": full_text,
        "metadata": {
            **base_metadata,
            "source_page": f"{character_name}/Voice-Overs",
            "source_url": source_url,
            "section": f"Voice-Overs (batch {batch_index + 1})",
            "section_type": "voice_over_batch",
            "heading_level": 3,
            "chunk_index": batch_index,
            "token_estimate": len(full_text.split()),
        },
    }

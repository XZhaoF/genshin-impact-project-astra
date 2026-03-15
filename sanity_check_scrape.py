"""
Sanity check v3: Scrape character Profile, Storyline, and Voice-Over pages
from Genshin Impact Fandom Wiki for RAG data structure.

Pages scraped per character:
  - Overview page (metadata only — infobox fields)
  - /Profile (Character Stories template + wiki sections)
  - /Storyline (full narrative history — wiki sections, if page exists)
  - /Voice-Overs (VO/Story template — lore-relevant lines only)
"""

import requests
import re
import json
import hashlib
from typing import Optional

BASE_URL = "https://genshin-impact.fandom.com/api.php"

SKIP_SECTIONS = {
    "Navigation",
    "References",
    "Notes",
    "Change History",
    "Character Trials",
    "Other Languages",
    "Combat",
}

# Voice-over groups that are NOT useful for lore (gameplay-only)
SKIP_VO_PREFIXES = {
    "vo_12_",  # Receiving a Gift
    "vo_14_",  # Ascension
}


# =============================================================================
# 1. SCRAPING — Fetch raw wikitext via MediaWiki API
# =============================================================================

def get_page_wikitext(title: str) -> Optional[str]:
    params = {
        "action": "query",
        "titles": title,
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
    }
    response = requests.get(BASE_URL, params=params).json()
    pages = response["query"]["pages"]
    for page_id, page_data in pages.items():
        if page_id == "-1":
            return None
        if "revisions" in page_data:
            return page_data["revisions"][0]["slots"]["main"]["*"]
    return None


# =============================================================================
# 2. TEMPLATE PARSERS (shared utilities)
# =============================================================================

def find_template_block(wikitext: str, template_name: str) -> Optional[str]:
    """Find a {{TemplateName|...}} block, handling nested braces."""
    pattern = r"\{\{" + re.escape(template_name) + r"\s*\n?\|"
    match = re.search(pattern, wikitext)
    if not match:
        return None

    start = match.start()
    depth = 0
    position = start

    while position < len(wikitext):
        if wikitext[position:position + 2] == "{{":
            depth += 1
            position += 2
        elif wikitext[position:position + 2] == "}}":
            depth -= 1
            if depth == 0:
                return wikitext[start + 2:position]
            position += 2
        else:
            position += 1

    return None


def extract_template_param(template_content: str, param_name: str) -> Optional[str]:
    """Extract a named parameter, handling multiline values and nested templates."""
    pattern = rf"\|{param_name}\s*=\s*"
    match = re.search(pattern, template_content)
    if not match:
        return None

    value_start = match.end()
    depth = 0
    position = value_start
    result = []

    while position < len(template_content):
        char = template_content[position]
        two_char = template_content[position:position + 2]

        if two_char == "{{":
            depth += 1
            result.append("{{")
            position += 2
        elif two_char == "}}":
            if depth == 0:
                break
            depth -= 1
            result.append("}}")
            position += 2
        elif char == "|" and depth == 0:
            rest = template_content[position + 1:]
            if re.match(r"\s*\w+\s*=", rest):
                break
            else:
                result.append(char)
                position += 1
        else:
            result.append(char)
            position += 1

    return "".join(result).strip() or None


def remove_template_block(text: str, template_name: str) -> str:
    """Remove an entire {{TemplateName|...}} block, handling nested braces."""
    pattern = r"\{\{" + re.escape(template_name) + r"\s*\n?\|"
    match = re.search(pattern, text)
    if not match:
        return text

    start = match.start()
    depth = 0
    position = start

    while position < len(text):
        if text[position:position + 2] == "{{":
            depth += 1
            position += 2
        elif text[position:position + 2] == "}}":
            depth -= 1
            if depth == 0:
                return text[:start] + text[position + 2:]
            position += 2
        else:
            position += 1

    return text


# =============================================================================
# 3. CHARACTER STORY TEMPLATE PARSER
# =============================================================================

def extract_character_stories(wikitext: str) -> list[dict]:
    story_match = find_template_block(wikitext, "Character Story")
    if not story_match:
        return []

    template_content = story_match
    stories = []
    story_index = 1

    while True:
        title = extract_template_param(template_content, f"title{story_index}")
        text = extract_template_param(template_content, f"text{story_index}")

        if title is None and text is None:
            break

        if text and text.strip() and text.strip().lower() != "not yet available":
            mentions_raw = extract_template_param(template_content, f"mention{story_index}")
            friendship = extract_template_param(template_content, f"friendship{story_index}")
            quest = extract_template_param(template_content, f"quest{story_index}")

            mentions = []
            if mentions_raw:
                mentions = [m.strip() for m in mentions_raw.split(",") if m.strip()]

            stories.append({
                "title": title.strip() if title else f"Story {story_index}",
                "text": clean_wikitext(text),
                "mentions": mentions,
                "friendship_level": friendship.strip() if friendship else None,
                "quest_requirement": quest.strip() if quest else None,
            })

        story_index += 1

    return stories


# =============================================================================
# 4. VOICE-OVER TEMPLATE PARSER
# =============================================================================

def extract_voice_overs(wikitext: str) -> list[dict]:
    """
    Parse {{VO/Story|character=...|vo_XX_YY_title=...|vo_XX_YY_tx=...|vo_XX_YY_mention=...}}
    into a list of {title, text, mentions, friendship_level, quest_requirement}.

    Numbering: vo_GG_NN_ where GG is the group and NN is the entry within that group.
    """
    vo_match = find_template_block(wikitext, "VO/Story")
    if not vo_match:
        return []

    template_content = vo_match
    voice_overs = []

    for group in range(1, 20):
        for entry in range(1, 20):
            prefix = f"vo_{group:02d}_{entry:02d}"

            if any(prefix.startswith(skip) for skip in SKIP_VO_PREFIXES):
                continue

            title = extract_template_param(template_content, f"{prefix}_title")
            text = extract_template_param(template_content, f"{prefix}_tx")

            if title is None and text is None:
                break

            if not text or not text.strip():
                continue

            cleaned_text = clean_wikitext(text)
            if len(cleaned_text.split()) < 5:
                continue

            # Replace {character} placeholder in title
            character_param = extract_template_param(template_content, "character")
            if title and character_param:
                title = title.replace("{character}", character_param)

            mentions_raw = extract_template_param(template_content, f"{prefix}_mention")
            friendship = extract_template_param(template_content, f"{prefix}_friendship")
            quest = extract_template_param(template_content, f"{prefix}_quest")

            mentions = []
            if mentions_raw:
                mentions = [m.strip() for m in re.split(r"[,;]", mentions_raw) if m.strip()]

            voice_overs.append({
                "title": title.strip() if title else f"Voice-Over {group}.{entry}",
                "text": cleaned_text,
                "mentions": mentions,
                "friendship_level": friendship.strip() if friendship else None,
                "quest_requirement": quest.strip() if quest else None,
            })

    return voice_overs


# =============================================================================
# 5. CLEANING — Strip wikitext markup into plain readable text
# =============================================================================

def clean_wikitext(raw: str) -> str:
    text = raw

    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[(?:File|Image):.*?\]\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[Category:.*?\]\]", "", text, flags=re.IGNORECASE)

    noise_templates = [
        r"\{\{Character Tabs\}\}",
        r"\{\{Language Tabs\}\}",
        r"\{\{Under Construction\}\}",
        r"\{\{Character Navbox.*?\}\}",
        r"\{\{Traveler Navbox.*?\}\}",
        r"\{\{Fatui Navbox.*?\}\}",
        r"\{\{Character Ascensions and Stats\}\}",
        r"\{\{Traveler Talents and Constellations\}\}",
        r"\{\{Trials by Character\}\}",
        r"\{\{Change History.*?\}\}",
        r"\{\{Reflist.*?\}\}",
        r"\{\{Redirect\|.*?\}\}",
        r"\{\{See also\|.*?\}\}",
        r"\{\{Namecard\|.*?\}\}",
        r"\{\{Constellation Lore\|.*?\}\}",
        r"\{\{Character Mentions.*?\}\}",
        r"\{\{Other Languages[^}]*\}\}",
        r"\{\{Official Introduction[^}]*\}\}",
    ]
    for pattern in noise_templates:
        text = re.sub(pattern, "", text, flags=re.DOTALL)

    text = remove_template_block(text, "Character Story")
    text = remove_template_block(text, "Quests and Events")
    text = remove_template_block(text, "VO/Story")
    text = remove_template_block(text, "Combat VO")
    text = remove_template_block(text, "Character Infobox")

    text = re.sub(r"\{\{Quest\|([^|}]+)[^}]*\}\}", r'"\1"', text)
    text = re.sub(r"\{\{Ref/[^}]+\}\}", "", text)
    text = re.sub(r"\{\{Ref/[^}]+\}\}", "", text)
    text = re.sub(r"\{\{MC\|([^|}]+)\|([^|}]+)[^}]*\}\}", r"\1/\2", text)
    text = re.sub(r"\{\{(Anemo|Geo|Electro|Dendro|Hydro|Pyro|Cryo)\}\}", r"\1", text)
    text = re.sub(r"\{\{Traveler\}\}", "Traveler", text)
    text = re.sub(r"\{\{Traveler's Sibling\}\}", "Traveler's Sibling", text)
    text = re.sub(r"\{\{w\|([^|}]+)(?:\|([^}]+))?\}\}", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\{\{Lang\|[^}]*\}\}", "", text)
    text = re.sub(r"\{\{(?:zh|ja|ko|en)\|([^}]+)\}\}", r"\1", text)
    text = re.sub(r"\{\{tt\|([^|}]+)\|[^}]*\}\}", r"\1", text)
    text = re.sub(r"\{\{Rubi\|([^|}]+)\|[^}]*\}\}", r"\1", text)
    text = re.sub(r"\{\{Ancient Name\|[^|}]*\|([^|}]+)[^}]*\}\}", r"\1", text)

    def format_quote(match):
        content = match.group(0)[2:-2]
        parts = content.split("|")
        if len(parts) >= 3:
            return f'"{parts[1].strip()}" -- {parts[2].strip()}'
        elif len(parts) >= 2:
            return f'"{parts[1].strip()}"'
        return ""
    text = re.sub(r"\{\{Quote\|[^}]*\}\}", format_quote, text)

    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\]", "", text)
    text = re.sub(r"'{2,3}", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^/]*/?>", "", text)

    text = text.replace("&mdash;", " -- ")
    text = text.replace("&ndash;", "-")
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&#8209;", "-")

    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[\[[a-z-]+:[^\]]+\]\]", "", text)
    text = re.sub(r"__TOC__", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)

    return text.strip()


# =============================================================================
# 6. METADATA — Extract infobox fields from the overview page
# =============================================================================

def extract_infobox_field(wikitext: str, field: str) -> Optional[str]:
    pattern = rf"\|{field}\s*=\s*(.+?)(?:\n\||\n\}})"
    match = re.search(pattern, wikitext, re.DOTALL)
    if not match:
        return None

    value = match.group(1).strip()
    value = re.sub(r"<!--.*", "", value, flags=re.DOTALL).strip()
    value = re.sub(r"\{\{[^}]*\}\}", "", value)
    value = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"<br\s*/?>", " / ", value)
    value = re.sub(r"<[^>]+>", "", value)

    value = value.strip()
    return value if value and value != "--" else None


def extract_character_metadata(overview_wikitext: str, character_name: str) -> dict:
    return {
        "page_type": "character",
        "character_name": extract_infobox_field(overview_wikitext, "name") or character_name,
        "quality": extract_infobox_field(overview_wikitext, "quality"),
        "weapon_type": extract_infobox_field(overview_wikitext, "weapon"),
        "element": extract_infobox_field(overview_wikitext, "element"),
        "constellation": extract_infobox_field(overview_wikitext, "constellation"),
        "affiliation": extract_infobox_field(overview_wikitext, "affiliation"),
        "region": extract_infobox_field(overview_wikitext, "region"),
        "title": extract_infobox_field(overview_wikitext, "title"),
    }


# =============================================================================
# 7. CHUNKING — Section-based + template-based chunking
# =============================================================================

def make_chunks_from_template_entries(entries: list[dict], character_name: str,
                                      base_metadata: dict, source_page: str,
                                      section_type: str,
                                      max_chunk_tokens: int = 400) -> list[dict]:
    """Shared chunking logic for Character Stories and Voice-Overs."""
    chunks = []
    source_url = f"https://genshin-impact.fandom.com/wiki/{character_name.replace(' ', '_')}/{source_page}"

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
                "metadata": {**entry_metadata, "chunk_index": chunk_index,
                             "token_estimate": len(full_text.split())},
            })

    return chunks


def chunk_wiki_sections(wikitext: str, character_name: str, base_metadata: dict,
                        source_page: str, max_chunk_tokens: int = 400) -> list[dict]:
    """Chunk cleaned wikitext by == and === headings."""
    cleaned = clean_wikitext(wikitext)
    source_url = f"https://genshin-impact.fandom.com/wiki/{character_name.replace(' ', '_')}/{source_page}"

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


def split_if_too_long(text: str, max_tokens: int = 400) -> list[str]:
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


# =============================================================================
# 8. VOICE-OVER GROUPING — Batch small VO lines into combined chunks
# =============================================================================

def group_voice_overs_into_chunks(voice_overs: list[dict], character_name: str,
                                  base_metadata: dict,
                                  max_chunk_tokens: int = 400) -> list[dict]:
    """
    Voice-over lines are short (20-80 words each). Group related lines into
    combined chunks to avoid hundreds of tiny vectors. "About X" lines stay
    individual since they're lore-rich. Short ambient lines get batched.
    """
    individual_lines = []
    batch_lines = []

    for voice_over in voice_overs:
        title = voice_over["title"]
        is_lore_rich = (
            title.startswith("About ") or
            title.startswith("More About ") or
            "Something to Share" in title or
            "Interesting Things" in title or
            "Birthday" in title or
            "Hobbies" in title or
            "Troubles" in title
        )

        if is_lore_rich and len(voice_over["text"].split()) > 30:
            individual_lines.append(voice_over)
        else:
            batch_lines.append(voice_over)

    chunks = []
    source_url = f"https://genshin-impact.fandom.com/wiki/{character_name.replace(' ', '_')}/Voice-Overs"

    # Individual lore-rich lines
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

    # Batch short ambient lines together
    if batch_lines:
        current_batch_text = ""
        current_batch_titles = []
        batch_index = 0

        for voice_over in batch_lines:
            line = f"[{voice_over['title']}]: {voice_over['text']}"
            candidate = (current_batch_text + "\n" + line).strip()

            if len(candidate.split()) > max_chunk_tokens and current_batch_text.strip():
                prefix = f"{character_name} (Voice-Overs) -- Ambient & Misc"
                full_text = f"{prefix}\n\n{current_batch_text.strip()}"
                chunk_id = hashlib.md5(
                    f"{character_name}:vo_batch:{batch_index}".encode()
                ).hexdigest()[:12]
                chunks.append({
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
                })
                current_batch_text = line
                batch_index += 1
            else:
                current_batch_text = candidate

        if current_batch_text.strip():
            prefix = f"{character_name} (Voice-Overs) -- Ambient & Misc"
            full_text = f"{prefix}\n\n{current_batch_text.strip()}"
            chunk_id = hashlib.md5(
                f"{character_name}:vo_batch:{batch_index}".encode()
            ).hexdigest()[:12]
            chunks.append({
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
            })

    return chunks


# =============================================================================
# 9. MAIN SCRAPER — Orchestrates all page types
# =============================================================================

def scrape_character(character_name: str) -> list[dict]:
    print(f"\n{'=' * 70}")
    print(f"SCRAPING: {character_name}")
    print(f"{'=' * 70}")

    # Step 1: Overview page for metadata
    print(f"  [1] Fetching {character_name} overview (metadata)...")
    overview_wikitext = get_page_wikitext(character_name)
    if not overview_wikitext:
        print(f"      ERROR: Could not fetch {character_name}")
        return []

    metadata = extract_character_metadata(overview_wikitext, character_name)
    print(f"      {json.dumps(metadata, ensure_ascii=False)}")

    all_chunks = []

    # Step 2: Profile page — Character Stories + wiki sections
    print(f"  [2] Fetching {character_name}/Profile...")
    profile_wikitext = get_page_wikitext(f"{character_name}/Profile")
    if profile_wikitext:
        print(f"      {len(profile_wikitext):,} chars")

        stories = extract_character_stories(profile_wikitext)
        print(f"      Character Stories: {len(stories)} entries")
        story_chunks = make_chunks_from_template_entries(
            stories, character_name, metadata, "Profile", "character_story")

        section_chunks = chunk_wiki_sections(
            profile_wikitext, character_name, metadata, "Profile")

        all_chunks.extend(story_chunks)
        all_chunks.extend(section_chunks)
        print(f"      -> {len(story_chunks)} story chunks + {len(section_chunks)} section chunks")
    else:
        print(f"      Not found (skipping)")

    # Step 3: Storyline page — full narrative history
    print(f"  [3] Fetching {character_name}/Storyline...")
    storyline_wikitext = get_page_wikitext(f"{character_name}/Storyline")
    if storyline_wikitext:
        print(f"      {len(storyline_wikitext):,} chars")
        storyline_chunks = chunk_wiki_sections(
            storyline_wikitext, character_name, metadata, "Storyline")
        all_chunks.extend(storyline_chunks)
        print(f"      -> {len(storyline_chunks)} storyline chunks")
    else:
        print(f"      Not found (skipping)")

    # Step 4: Voice-Overs page
    print(f"  [4] Fetching {character_name}/Voice-Overs...")
    vo_wikitext = get_page_wikitext(f"{character_name}/Voice-Overs")
    if vo_wikitext:
        print(f"      {len(vo_wikitext):,} chars")
        voice_overs = extract_voice_overs(vo_wikitext)
        print(f"      Voice-Over lines: {len(voice_overs)} lore-relevant entries")
        vo_chunks = group_voice_overs_into_chunks(
            voice_overs, character_name, metadata)
        all_chunks.extend(vo_chunks)
        lore_count = sum(1 for c in vo_chunks if c["metadata"]["section_type"] == "voice_over")
        batch_count = sum(1 for c in vo_chunks if c["metadata"]["section_type"] == "voice_over_batch")
        print(f"      -> {lore_count} individual lore VO chunks + {batch_count} batched ambient chunks")
    else:
        print(f"      Not found (skipping)")

    total_tokens = sum(c["metadata"]["token_estimate"] for c in all_chunks)
    print(f"\n  TOTAL: {len(all_chunks)} chunks, {total_tokens:,} tokens")

    return all_chunks


def display_chunks(chunks: list[dict], character_name: str):
    print(f"\n{'=' * 70}")
    print(f"RAG CHUNKS FOR: {character_name}")
    print(f"{'=' * 70}")

    for i, chunk in enumerate(chunks):
        meta = chunk["metadata"]
        source_short = meta["source_page"].split("/")[-1]
        mentions_str = ""
        if meta.get("mentions"):
            mentions_str = f"  mentions=[{', '.join(meta['mentions'][:3])}]"
        print(f"  [{i+1:02d}] [{source_short:10s}] {meta['section_type']:18s} "
              f"tokens={meta['token_estimate']:4d}  {meta['section']}{mentions_str}")


def main():
    all_results = {}

    for character_name in ["Columbina", "Skirk"]:
        chunks = scrape_character(character_name)
        all_results[character_name] = chunks
        display_chunks(chunks, character_name)

    output = {}
    for character_name, chunks in all_results.items():
        output[character_name] = {
            "total_chunks": len(chunks),
            "total_tokens": sum(c["metadata"]["token_estimate"] for c in chunks),
            "chunks": chunks,
        }

    output_path = "sanity_check_output.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    for character_name, data in output.items():
        story_count = sum(1 for c in data["chunks"] if c["metadata"]["section_type"] == "character_story")
        section_count = sum(1 for c in data["chunks"] if c["metadata"]["section_type"] == "wiki_section")
        vo_count = sum(1 for c in data["chunks"] if c["metadata"]["section_type"] in ("voice_over", "voice_over_batch"))
        print(f"  {character_name}: {data['total_chunks']} chunks ({story_count} stories, "
              f"{section_count} sections, {vo_count} voice-overs), {data['total_tokens']:,} tokens")
    print(f"\nFull JSON saved to: {output_path}")


if __name__ == "__main__":
    main()

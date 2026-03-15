"""
Wikitext template parsers and text cleaner.

Handles extraction of Character Story and VO/Story templates,
infobox metadata, and conversion of raw wikitext to plain text.
"""

import re
from typing import Optional


SKIP_SECTIONS = {
    "Navigation",
    "References",
    "Notes",
    "Change History",
    "Character Trials",
    "Other Languages",
    "Combat",
}

SKIP_VO_PREFIXES = {
    "vo_12_",  # Receiving a Gift
    "vo_14_",  # Ascension
}


# ---------------------------------------------------------------------------
# Template utilities
# ---------------------------------------------------------------------------

def find_template_block(wikitext: str, template_name: str) -> Optional[str]:
    """Find a {{TemplateName|...}} block, handling nested braces.

    Returns the inner content (between the opening {{ and closing }}).
    """
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
    """Extract a named parameter value, handling multiline values and nested templates."""
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


# ---------------------------------------------------------------------------
# Character Story template parser
# ---------------------------------------------------------------------------

def extract_character_stories(wikitext: str) -> list[dict]:
    """Parse the {{Character Story|...}} template into a list of story entries."""
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

            mentions = _parse_mentions(mentions_raw)

            stories.append({
                "title": title.strip() if title else f"Story {story_index}",
                "text": clean_wikitext(text),
                "mentions": mentions,
                "friendship_level": friendship.strip() if friendship else None,
                "quest_requirement": quest.strip() if quest else None,
            })

        story_index += 1

    return stories


# ---------------------------------------------------------------------------
# Voice-Over template parser
# ---------------------------------------------------------------------------

def extract_voice_overs(wikitext: str) -> list[dict]:
    """Parse {{VO/Story|...}} into a list of voice-over entries.

    Numbering follows vo_GG_NN_ where GG = group, NN = entry.
    Skips gameplay-only groups (gifts, ascension) and very short lines.
    """
    vo_match = find_template_block(wikitext, "VO/Story")
    if not vo_match:
        return []

    template_content = vo_match
    character_param = extract_template_param(template_content, "character")
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

            if title and character_param:
                title = title.replace("{character}", character_param)

            mentions_raw = extract_template_param(template_content, f"{prefix}_mention")
            friendship = extract_template_param(template_content, f"{prefix}_friendship")
            quest = extract_template_param(template_content, f"{prefix}_quest")

            mentions = _parse_mentions(mentions_raw)

            voice_overs.append({
                "title": title.strip() if title else f"Voice-Over {group}.{entry}",
                "text": cleaned_text,
                "mentions": mentions,
                "friendship_level": friendship.strip() if friendship else None,
                "quest_requirement": quest.strip() if quest else None,
            })

    return voice_overs


def _parse_mentions(mentions_raw: Optional[str]) -> list[str]:
    """Split a mentions string into a clean list, stripping HTML comments."""
    if not mentions_raw:
        return []
    cleaned = re.sub(r"<!--.*?-->", "", mentions_raw, flags=re.DOTALL)
    return [m.strip() for m in re.split(r"[,;]", cleaned) if m.strip()]


# ---------------------------------------------------------------------------
# Wikitext cleaner
# ---------------------------------------------------------------------------

_NOISE_TEMPLATES = [
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

_BLOCK_TEMPLATES_TO_REMOVE = [
    "Character Story",
    "Quests and Events",
    "VO/Story",
    "Combat VO",
    "Character Infobox",
]


def clean_wikitext(raw: str) -> str:
    """Convert raw wikitext into clean plain text for RAG chunking."""
    text = raw

    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[(?:File|Image):.*?\]\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[Category:.*?\]\]", "", text, flags=re.IGNORECASE)

    for pattern in _NOISE_TEMPLATES:
        text = re.sub(pattern, "", text, flags=re.DOTALL)

    for template_name in _BLOCK_TEMPLATES_TO_REMOVE:
        text = remove_template_block(text, template_name)

    text = re.sub(r"\{\{Quest\|([^|}]+)[^}]*\}\}", r'"\1"', text)
    text = re.sub(r"\{\{Ref/[^}]+\}\}", "", text)
    text = re.sub(r"\{\{MC\|([^|}]+)\|([^|}]+)[^}]*\}\}", r"\1/\2", text)
    text = re.sub(r"\{\{(Anemo|Geo|Electro|Dendro|Hydro|Pyro|Cryo)\}\}", r"\1", text)
    text = re.sub(r"\{\{Traveler\}\}", "Traveler", text)
    text = re.sub(r"\{\{Traveler's Sibling\}\}", "Traveler's Sibling", text)
    text = re.sub(
        r"\{\{w\|([^|}]+)(?:\|([^}]+))?\}\}",
        lambda m: m.group(2) or m.group(1),
        text,
    )
    text = re.sub(r"\{\{Lang\|[^}]*\}\}", "", text)
    text = re.sub(r"\{\{(?:zh|ja|ko|en)\|([^}]+)\}\}", r"\1", text)
    text = re.sub(r"\{\{tt\|([^|}]+)\|[^}]*\}\}", r"\1", text)
    text = re.sub(r"\{\{Rubi\|([^|}]+)\|[^}]*\}\}", r"\1", text)
    text = re.sub(r"\{\{Ancient Name\|[^|}]*\|([^|}]+)[^}]*\}\}", r"\1", text)

    def _format_quote(match):
        content = match.group(0)[2:-2]
        parts = content.split("|")
        if len(parts) >= 3:
            return f'"{parts[1].strip()}" -- {parts[2].strip()}'
        elif len(parts) >= 2:
            return f'"{parts[1].strip()}"'
        return ""

    text = re.sub(r"\{\{Quote\|[^}]*\}\}", _format_quote, text)

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


# ---------------------------------------------------------------------------
# Metadata extraction from character infobox
# ---------------------------------------------------------------------------

def extract_infobox_field(wikitext: str, field: str) -> Optional[str]:
    """Extract a single field value from a Character Infobox template."""
    pattern = rf"\|{field}\s*=[ \t]*(.*?)(?:\n\||\n\}})"
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
    """Build a metadata dict from a character's overview page infobox."""
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

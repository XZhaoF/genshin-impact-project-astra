"""
Genshin Impact wiki scraper package for RAG data extraction.

Scrapes character lore from Profile, Storyline, and Voice-Over pages
on the Genshin Impact Fandom Wiki.
"""

from scraper.wiki_api import get_page_wikitext, get_category_members
from scraper.parsers import (
    extract_character_stories,
    extract_voice_overs,
    extract_character_metadata,
    clean_wikitext,
)
from scraper.chunking import (
    make_chunks_from_template_entries,
    chunk_wiki_sections,
    group_voice_overs_into_chunks,
)
from scraper.character_scraper import scrape_character
from scraper.quest_scraper import scrape_quest_act, scrape_world_quest_series

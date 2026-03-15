"""
CLI runner to scrape all playable Genshin Impact characters.

Usage:
    python scrape_all.py                       # Scrape all characters
    python scrape_all.py --character Columbina  # Scrape one character
    python scrape_all.py --force                # Re-scrape even if JSON exists
    python scrape_all.py --test                 # Scrape 4 test characters only
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from scraper.wiki_api import get_category_members, set_request_delay
from scraper.character_scraper import scrape_character

DATA_DIR = Path("data/characters")

# Characters with unusual wiki page names (title != page slug)
PAGE_NAME_OVERRIDES = {
    "Traveler": "Traveler",
}

TEST_CHARACTERS = ["Columbina", "Skirk", "Albedo", "Raiden Shogun"]


def fetch_character_list() -> list[str]:
    """Get all playable character names from the wiki category."""
    print("Fetching playable character list from wiki...")
    names = get_category_members("Playable Characters")
    print(f"  Found {len(names)} playable characters")
    return names


def write_character_json(result: dict, output_dir: Path):
    """Write a single character's scraped data to its JSON file."""
    safe_name = result["character_name"].replace("/", "_").replace("\\", "_")
    filepath = output_dir / f"{safe_name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return filepath


def generate_manifest(output_dir: Path):
    """Read all character JSON files and write _manifest.json."""
    characters = []
    total_chunks = 0
    total_tokens = 0

    for filepath in sorted(output_dir.glob("*.json")):
        if filepath.name.startswith("_"):
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        stats = data.get("stats", {})
        character_chunks = stats.get("total_chunks", 0)
        character_tokens = stats.get("total_tokens", 0)
        total_chunks += character_chunks
        total_tokens += character_tokens

        characters.append({
            "name": data["character_name"],
            "file": filepath.name,
            "chunks": character_chunks,
            "tokens": character_tokens,
            "pages": data.get("pages_scraped", []),
        })

    manifest = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "total_characters": len(characters),
        "total_chunks": total_chunks,
        "total_tokens": total_tokens,
        "characters": characters,
    }

    manifest_path = output_dir / "_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Genshin Impact character lore from the Fandom Wiki.",
    )
    parser.add_argument(
        "--character", "-c",
        help="Scrape a single character by name (e.g. 'Columbina')",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Re-scrape characters even if their JSON already exists",
    )
    parser.add_argument(
        "--test", "-t",
        action="store_true",
        help=f"Test mode: only scrape {len(TEST_CHARACTERS)} characters",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between API calls (default: 0.5)",
    )
    args = parser.parse_args()

    set_request_delay(args.delay)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Determine which characters to scrape
    if args.character:
        characters = [args.character]
    elif args.test:
        characters = TEST_CHARACTERS
        print(f"TEST MODE: scraping {len(characters)} characters only")
    else:
        characters = fetch_character_list()

    # Scrape loop
    succeeded = 0
    failed = []
    skipped = 0

    for index, character_name in enumerate(characters, 1):
        safe_name = character_name.replace("/", "_").replace("\\", "_")
        json_path = DATA_DIR / f"{safe_name}.json"

        if json_path.exists() and not args.force:
            print(f"  [{index}/{len(characters)}] {character_name} -- already exists, skipping")
            skipped += 1
            continue

        print(f"\n  [{index}/{len(characters)}] Scraping {character_name}...")

        try:
            result = scrape_character(character_name, verbose=True)
            filepath = write_character_json(result, DATA_DIR)

            if result["stats"]["total_chunks"] > 0:
                succeeded += 1
                print(f"  -> Saved to {filepath}")
            else:
                failed.append(character_name)
                print(f"  -> WARNING: {character_name} produced 0 chunks")

        except Exception as error:
            failed.append(character_name)
            print(f"  -> ERROR scraping {character_name}: {error}", file=sys.stderr)

    # Generate manifest
    print(f"\n{'=' * 70}")
    print("Generating manifest...")
    manifest = generate_manifest(DATA_DIR)

    # Summary
    print(f"\n{'=' * 70}")
    print("SCRAPE COMPLETE")
    print(f"{'=' * 70}")
    print(f"  Succeeded: {succeeded}")
    print(f"  Skipped (already existed): {skipped}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print(f"  Failed characters: {', '.join(failed)}")
    print(f"\n  Total characters in manifest: {manifest['total_characters']}")
    print(f"  Total chunks: {manifest['total_chunks']:,}")
    print(f"  Total tokens: {manifest['total_tokens']:,}")
    print(f"\n  Output directory: {DATA_DIR.resolve()}")
    print(f"  Manifest: {(DATA_DIR / '_manifest.json').resolve()}")


if __name__ == "__main__":
    main()

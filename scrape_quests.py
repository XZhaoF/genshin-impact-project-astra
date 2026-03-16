"""
CLI runner to scrape quest pages from the Genshin Impact Fandom Wiki.

Usage:
    python scrape_quests.py                                    # Scrape all archon quest acts
    python scrape_quests.py --world-quests                     # Scrape all world quest series
    python scrape_quests.py --all                              # Scrape both
    python scrape_quests.py --quest "True Moon"                # Scrape one archon quest
    python scrape_quests.py --world-quests --quest "Aranyaka"  # Scrape one world quest
    python scrape_quests.py --force                            # Re-scrape even if JSON exists
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scraper.wiki_api import get_category_members, set_request_delay
from scraper.quest_scraper import scrape_quest_act, scrape_world_quest_series

ARCHON_DIR = Path("data/quests/archon")
WORLD_DIR = Path("data/quests/world_series")


def write_quest_json(result: dict, output_dir: Path) -> Path:
    safe_name = (
        result["quest_title"]
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", " -")
    )
    filepath = output_dir / f"{safe_name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    return filepath


def generate_manifest(output_dir: Path, quest_type: str) -> dict:
    quests = []
    total_chunks = 0
    total_tokens = 0

    for filepath in sorted(output_dir.glob("*.json")):
        if filepath.name.startswith("_"):
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        stats = data.get("stats", {})
        quest_chunks = stats.get("total_chunks", 0)
        quest_tokens = stats.get("total_tokens", 0)
        total_chunks += quest_chunks
        total_tokens += quest_tokens

        meta = data.get("metadata", {})
        entry = {
            "title": data["quest_title"],
            "file": filepath.name,
            "region": meta.get("region"),
            "chunks": quest_chunks,
            "tokens": quest_tokens,
            "individual_quests": len(data.get("quest_list", [])),
        }
        if meta.get("chapter"):
            entry["chapter"] = meta["chapter"]
        if meta.get("act_number"):
            entry["act_number"] = meta["act_number"]
        quests.append(entry)

    manifest = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "quest_type": quest_type,
        "total_entries": len(quests),
        "total_chunks": total_chunks,
        "total_tokens": total_tokens,
        "entries": quests,
    }

    manifest_path = output_dir / "_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return manifest


def run_scrape(
    quest_titles: list[str],
    scrape_fn,
    output_dir: Path,
    quest_type: str,
    force: bool,
):
    """Generic scrape loop for both quest types."""
    output_dir.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    failed = []
    skipped = 0

    for index, quest_title in enumerate(quest_titles, 1):
        safe_name = (
            quest_title.replace("/", "_").replace("\\", "_").replace(":", " -")
        )
        json_path = output_dir / f"{safe_name}.json"

        if json_path.exists() and not force:
            print(f"  [{index}/{len(quest_titles)}] {quest_title} -- already exists, skipping")
            skipped += 1
            continue

        print(f"\n  [{index}/{len(quest_titles)}] {quest_title}")

        try:
            result = scrape_fn(quest_title, verbose=True)
            filepath = write_quest_json(result, output_dir)

            if result["stats"]["total_chunks"] > 0:
                succeeded += 1
                print(f"    Saved to {filepath}")
            else:
                failed.append(quest_title)
                print(f"    WARNING: {quest_title} produced 0 chunks")

        except Exception as error:
            failed.append(quest_title)
            print(f"    ERROR scraping {quest_title}: {error}", file=sys.stderr)

    print(f"\n{'=' * 70}")
    print(f"Generating {quest_type} manifest...")
    manifest = generate_manifest(output_dir, quest_type)

    print(f"\n{'=' * 70}")
    print(f"{quest_type.upper()} SCRAPE COMPLETE")
    print(f"{'=' * 70}")
    print(f"  Succeeded: {succeeded}")
    print(f"  Skipped (already existed): {skipped}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print(f"  Failed: {', '.join(failed)}")
    print(f"\n  Total in manifest: {manifest['total_entries']}")
    print(f"  Total chunks: {manifest['total_chunks']:,}")
    print(f"  Total tokens: {manifest['total_tokens']:,}")
    print(f"\n  Output directory: {output_dir.resolve()}")

    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Scrape quest summaries from the Genshin Impact Fandom Wiki.",
    )
    parser.add_argument(
        "--quest", "-q",
        help="Scrape a single quest by title",
    )
    parser.add_argument(
        "--world-quests", "-w",
        action="store_true",
        help="Scrape World Quest Series instead of Archon Quest Acts",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Scrape both Archon Quest Acts and World Quest Series",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Re-scrape even if JSON already exists",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between API calls (default: 0.5)",
    )
    args = parser.parse_args()

    set_request_delay(args.delay)

    run_archon = not args.world_quests or args.all
    run_world = args.world_quests or args.all

    if args.quest and not args.world_quests:
        run_scrape([args.quest], scrape_quest_act, ARCHON_DIR, "Archon Quest", args.force)
        return

    if args.quest and args.world_quests:
        run_scrape([args.quest], scrape_world_quest_series, WORLD_DIR, "World Quest Series", args.force)
        return

    if run_archon:
        print("Fetching Archon Quest act list from wiki...")
        archon_titles = get_category_members("Archon Quest Acts")
        print(f"  Found {len(archon_titles)} archon quest acts\n")
        run_scrape(archon_titles, scrape_quest_act, ARCHON_DIR, "Archon Quest", args.force)

    if run_world:
        print("\nFetching World Quest Series list from wiki...")
        world_titles = get_category_members("World Quest Series")
        print(f"  Found {len(world_titles)} world quest series\n")
        run_scrape(world_titles, scrape_world_quest_series, WORLD_DIR, "World Quest Series", args.force)


if __name__ == "__main__":
    main()

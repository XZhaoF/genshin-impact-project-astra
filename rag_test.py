"""Headless sanity check for the Project Astra RAG pipeline.

Runs the full loop end-to-end without a web UI:

  - A scripted batch of test questions on startup (lore facts, cross-character,
    relationships, region lookups, and off-topic queries that should be refused).
  - An interactive terminal loop so you can ask your own questions.

Usage:
  python rag_test.py            # scripted batch, then interactive prompt
  python rag_test.py --batch    # scripted batch only, then exit
  python rag_test.py --chat     # skip the batch, go straight to interactive

Type 'quit', 'exit', or Ctrl+C to leave the interactive loop.
"""

import argparse

from rag.query_engine import ask, SCORE_THRESHOLD, GEMINI_MODEL, GROQ_MODEL

# Scripted questions. The last two are intentionally off-topic and should be
# refused by the score threshold before any LLM call.
SCRIPTED_QUESTIONS = [
    "What element does the Raiden Shogun use?",
    "Who is Columbina and what is her connection to the moon?",
    "What happened to Sandrone in the True Moon quest?",
    "Who is the Geo Archon?",
    "How are Tartaglia and the Traveler connected?",
    "What is the Aranyaka world quest about?",
    "Write me a Python web server",          # off-topic -> refuse
    "What is the capital of France?",         # off-topic -> refuse
]

SEPARATOR = "=" * 78


def _print_result(question, result):
    """Pretty-print one ask() result with sources and diagnostics."""
    print(SEPARATOR)
    print(f"Q: {question}")
    print("-" * 78)
    print(result["answer"])

    status = "REFUSED (below threshold)" if result["refused"] else f"model={result['model_used']}"
    print("-" * 78)
    print(f"  top_score={result['top_score']:.3f}  threshold={SCORE_THRESHOLD}  {status}")

    if result["sources"]:
        print("  sources:")
        for position, source in enumerate(result["sources"], start=1):
            label = source.get("label") or "?"
            section = source.get("section") or ""
            print(f"    [{position}] {source['score']:.3f}  {label} | {section}")
    print()


def run_batch():
    """Run all scripted questions through the pipeline."""
    print(f"\nRunning {len(SCRIPTED_QUESTIONS)} scripted questions")
    print(f"Primary: {GEMINI_MODEL}   Fallback: {GROQ_MODEL}\n")
    for question in SCRIPTED_QUESTIONS:
        try:
            result = ask(question)
        except Exception as error:  # surface unexpected failures, keep going
            print(SEPARATOR)
            print(f"Q: {question}")
            print(f"  ERROR: {error}\n")
            continue
        _print_result(question, result)


def run_interactive():
    """Interactive question loop until the user quits."""
    print(SEPARATOR)
    print("Interactive mode. Ask a Genshin lore question ('quit' to exit).")
    print(SEPARATOR)
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return
        if not question:
            continue
        if question.lower() in {"quit", "exit", "q"}:
            print("Bye.")
            return
        try:
            result = ask(question)
        except Exception as error:
            print(f"  ERROR: {error}")
            continue
        _print_result(question, result)


def main():
    parser = argparse.ArgumentParser(description="Headless RAG sanity check.")
    parser.add_argument("--batch", action="store_true",
                        help="Run the scripted batch only, then exit.")
    parser.add_argument("--chat", action="store_true",
                        help="Skip the scripted batch, go straight to interactive.")
    arguments = parser.parse_args()

    if not arguments.chat:
        run_batch()
    if not arguments.batch:
        run_interactive()


if __name__ == "__main__":
    main()

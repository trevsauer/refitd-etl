#!/usr/bin/env python3
"""
Export curated products in OpenAI fine-tuning format (JSONL).

Uses CurationHistoryService to fetch training data and formats each example
for OpenAI's chat completions fine-tuning format.

Usage:
    python scripts/export_training_data.py --output training.jsonl
    python scripts/export_training_data.py -o training.jsonl --min-confidence 4
    python scripts/export_training_data.py -o training.jsonl --no-approved-only

Requires: SUPABASE_URL and SUPABASE_KEY (or .env)
Run from project root.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

console = Console()

# OpenAI fine-tuning cost estimates (per 1M tokens, approximate)
# GPT-4o: ~$25/1M training tokens; GPT-4o-mini: ~$4/1M
COST_PER_M_TOKENS_GPT4O = 25.0
CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def _format_curator_feedback(tags_final: dict) -> str:
    """Build CURATOR FEEDBACK section from deleted_tags, added_tags, modified_tags."""
    if not tags_final or not isinstance(tags_final, dict):
        return ""

    lines: list[str] = []

    # Deleted tags
    for field_name, entries in (tags_final.get("deleted_tags") or {}).items():
        if not entries:
            continue
        items = entries if isinstance(entries, list) else [entries]
        for item in items:
            if isinstance(item, dict):
                value = item.get("value") or item.get("tag")
                reason = (item.get("reason") or "").strip()
                if value:
                    line = f"- Removed '{value}' from {field_name}"
                    if reason:
                        line += f": {reason}"
                    lines.append(line)
            elif isinstance(item, str):
                lines.append(f"- Removed '{item}' from {field_name}")

    # Added tags
    for field_name, entries in (tags_final.get("added_tags") or {}).items():
        if not entries:
            continue
        items = entries if isinstance(entries, list) else [entries]
        for item in items:
            if isinstance(item, dict):
                value = item.get("value") or item.get("tag")
                reason = (item.get("reason") or "").strip()
                if value:
                    line = f"- Added '{value}' to {field_name}"
                    if reason:
                        line += f": {reason}"
                    lines.append(line)
            elif isinstance(item, str):
                lines.append(f"- Added '{item}' to {field_name}")

    # Modified tags
    for field_name, entry in (tags_final.get("modified_tags") or {}).items():
        if not isinstance(entry, dict):
            continue
        from_val = entry.get("from")
        to_val = entry.get("to")
        reason = (entry.get("reason") or "").strip()
        if from_val is not None and to_val is not None:
            line = f"- Changed {field_name} from '{from_val}' to '{to_val}'"
            if reason:
                line += f": {reason}"
            lines.append(line)

    if not lines:
        return ""
    return "CURATOR FEEDBACK\n" + "\n".join(lines)


def build_user_content(record: dict) -> str:
    """Build user message content as JSON (product data for tagging)."""
    products = record.get("products") or {}
    if not isinstance(products, dict):
        products = {}
    product = {
        "title": (
            record.get("product_name")
            or products.get("name")
            or record.get("name")
            or "Unknown"
        ),
        "category": record.get("category") or products.get("category") or "Unknown",
        "description": record.get("description") or products.get("description") or "",
        "brand": (
            products.get("brand_name")
            or record.get("brand_name")
            or "Unknown"
        ),
    }
    return json.dumps(product, indent=2)


def build_example(record: dict, system_prompt: str) -> dict:
    """Build one OpenAI fine-tuning example."""
    user_content = build_user_content(record)
    corrected_tags = record.get("corrected_tags") or {}
    assistant_content = json.dumps(corrected_tags, indent=2)

    # Append per-tag curator feedback to system message when present
    feedback_section = _format_curator_feedback(corrected_tags)
    system_content = system_prompt
    if feedback_section:
        system_content = system_prompt + "\n\n" + feedback_section

    return {
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export curated products in OpenAI fine-tuning format (JSONL)."
    )
    parser.add_argument(
        "-o", "--output-file",
        type=Path,
        required=True,
        help="Output JSONL file path (e.g. training.jsonl)",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=3,
        help="Minimum confidence_in_correction (1-5). Default: 3",
    )
    parser.add_argument(
        "--approved-only",
        action="store_true",
        default=True,
        help="Only include records with include_in_training=True (default)",
    )
    parser.add_argument(
        "--no-approved-only",
        action="store_false",
        dest="approved_only",
        help="Include all curation records regardless of include_in_training",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of examples to export (default: all)",
    )
    args = parser.parse_args()

    try:
        from src.ai.refitd_tagger import SYSTEM_PROMPT
        from src.services.curation_history_service import CurationHistoryService
    except ImportError as e:
        console.print(f"[red]Import error: {e}[/red]")
        return 1

    service = CurationHistoryService()
    console.print("[cyan]Fetching training data from Supabase...[/cyan]")

    try:
        records = service.get_training_data(
            min_confidence=args.min_confidence,
            approved_only=args.approved_only,
            limit=args.limit,
        )
    except Exception as e:
        console.print(f"[red]Failed to fetch training data: {e}[/red]")
        return 1

    if not records:
        console.print("[yellow]No training examples found.[/yellow]")
        return 0

    out_path = args.output_file.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_tokens_est = 0
    category_counts: dict[str, int] = {}

    with open(out_path, "w") as f:
        for record in records:
            example = build_example(record, SYSTEM_PROMPT)
            f.write(json.dumps(example, ensure_ascii=False) + "\n")

            # Stats
            example_str = json.dumps(example, ensure_ascii=False)
            total_tokens_est += estimate_tokens(example_str)

            cat = record.get("category") or "unknown"
            category_counts[cat] = category_counts.get(cat, 0) + 1

    # Print statistics
    console.print(f"\n[green]✓ Exported {len(records)} examples to {out_path}[/green]\n")
    console.print("[bold]Statistics[/bold]")
    console.print(f"  Total examples:  {len(records)}")
    console.print(f"  Est. tokens:     ~{total_tokens_est:,}")

    table = Table(title="Category distribution")
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        table.add_row(cat, str(count))
    console.print(table)

    cost_gpt4o = (total_tokens_est / 1_000_000) * COST_PER_M_TOKENS_GPT4O
    console.print(
        f"\n[dim]Estimated training cost (GPT-4o, ~${COST_PER_M_TOKENS_GPT4O}/1M tokens): "
        f"~${cost_gpt4o:.2f}[/dim]"
    )
    console.print(
        "[dim]Note: Actual cost depends on model and tokenizer. "
        "Run: openai api fine_tunes.create -t <file>[/dim]\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

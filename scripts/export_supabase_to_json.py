#!/usr/bin/env python3
"""
Export products from Supabase to a single JSON file.

Use this to get a full dump of your scrape (including AI tagging results)
for upload to Claude or other tools. Includes: product_id, name, category,
url, tags_ai_raw, tags_final, curation_status_refitd, and all other columns.

Usage:
    python scripts/export_supabase_to_json.py
    python scripts/export_supabase_to_json.py -o my_export.json
    python scripts/export_supabase_to_json.py --tagged-only   # Only products with tags_final
    python scripts/export_supabase_to_json.py --limit 50

Requires: SUPABASE_URL and SUPABASE_KEY (or .env), or uses project defaults.
Run from project root.
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

# Project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

console = Console()


def json_serial(obj):
    """Serialize dates and other non-JSON types."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Supabase products (including AI tags) to a JSON file."
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output JSON file path (default: data/zara/mens/supabase_export.json)",
    )
    parser.add_argument(
        "--tagged-only",
        action="store_true",
        help="Only export products that have tags_final (AI tagging completed)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of products to export (default: all, up to 1000)",
    )
    args = parser.parse_args()

    try:
        from src.loaders.supabase_loader import SupabaseLoader
    except Exception as e:
        console.print(f"[red]Could not import SupabaseLoader: {e}[/red]")
        return 1

    loader = SupabaseLoader()
    out_path = args.output
    if out_path is None:
        out_path = Path("data/zara/mens/supabase_export.json")
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    console.print("[cyan]Fetching products from Supabase...[/cyan]")
    try:
        query = loader.client.table("products").select("*")
        if args.limit:
            query = query.limit(args.limit)
        response = query.execute()
    except Exception as e:
        console.print(f"[red]Supabase query failed: {e}[/red]")
        return 1

    products = response.data or []
    if args.tagged_only:
        products = [p for p in products if p.get("tags_final")]
        console.print(f"[dim]Filtered to {len(products)} product(s) with tags_final.[/dim]")
    if not products:
        console.print("[yellow]No products to export.[/yellow]")
        payload = {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "total_products": 0,
            "tagged_only": args.tagged_only,
            "products": [],
        }
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2, default=json_serial)
        console.print(f"[dim]Wrote empty export to {out_path}[/dim]")
        return 0

    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "total_products": len(products),
        "tagged_only": args.tagged_only,
        "products": products,
    }

    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=json_serial)

    console.print(f"[green]Exported {len(products)} product(s) to [bold]{out_path}[/bold][/green]")
    tagged = sum(1 for p in products if p.get("tags_final"))
    if tagged != len(products):
        console.print(f"[dim]{tagged} of those have tags_final (AI tagging).[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

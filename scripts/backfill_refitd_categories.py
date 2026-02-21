#!/usr/bin/env python3
"""
Backfill category_refitd and top_layer_role for existing products.

Run after adding the new columns to the products table (see docs/supabase_schema.sql).
Uses the same retailer → ReFitd mapping as the scraper pipeline.

Usage:
    python scripts/backfill_refitd_categories.py

Requires: SUPABASE_URL and SUPABASE_KEY in .env (or hardcoded defaults in loader).
"""

import os
import sys
from pathlib import Path

# Project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from rich.console import Console
from supabase import create_client

from src.loaders.refitd_category_mapping import get_refitd_slots

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
console = Console()

DEFAULT_SUPABASE_URL = "https://uochfddhtkzrvcmfwksm.supabase.co"
DEFAULT_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVvY2hmZGRodGt6cnZjbWZ3a3NtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njg1MDA1NDEsImV4cCI6MjA4NDA3NjU0MX0.mzBTf1GV8_Vk-nIMvf26PxI_MAqZfStzRTEZBEvHyLU"


def main() -> int:
    url = os.getenv("SUPABASE_URL") or DEFAULT_SUPABASE_URL
    key = os.getenv("SUPABASE_KEY") or DEFAULT_SUPABASE_KEY
    client = create_client(url, key)

    console.print("[cyan]Fetching all products...[/cyan]")
    result = client.table("products").select("product_id, category").execute()
    rows = result.data or []
    if not rows:
        console.print("[yellow]No products found.[/yellow]")
        return 0

    console.print(f"[cyan]Updating category_refitd and top_layer_role for {len(rows)} products...[/cyan]")
    updated = 0
    for row in rows:
        product_id = row.get("product_id")
        retailer_category = (row.get("category") or "").strip()
        category_refitd, top_layer_role = get_refitd_slots(retailer_category)
        try:
            client.table("products").update({
                "category_refitd": category_refitd,
                "top_layer_role": top_layer_role,
            }).eq("product_id", product_id).execute()
            updated += 1
        except Exception as e:
            console.print(f"[red]Failed to update {product_id}: {e}[/red]")

    console.print(f"[green]✓ Updated {updated} products.[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

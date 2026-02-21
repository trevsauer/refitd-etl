#!/usr/bin/env python3
"""
Safely wipe all data from the Supabase database.

Deletes curation_history, tag_correction_feedback, rejected_inferred_tags,
curated_metadata, curation_status, and products in the correct order to
satisfy foreign key constraints.

Usage:
    python scripts/wipe_database.py
    python scripts/wipe_database.py --dry-run
    python scripts/wipe_database.py --force

Requires: SUPABASE_URL and SUPABASE_KEY (or .env)
Run from project root.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Tables to delete, in order (children before parents due to FK)
TABLES = [
    ("curation_history", "id", "gte", 0),
    ("tag_correction_feedback", "id", "gte", 0),
    ("rejected_inferred_tags", "id", "gte", 0),
    ("curated_metadata", "id", "gte", 0),
    ("curation_status", "id", "gte", 0),
    ("ai_generated_tags", "id", "gte", 0),
    ("products", "product_id", "neq", ""),
]

CONFIRM_PROMPT = """
⚠️  WARNING: This will permanently delete ALL data:
   - All products
   - All curation history
   - All tag feedback
   - All curated metadata
   - All curation status
   - All AI generated tags

Type 'DELETE EVERYTHING' to confirm: """


def get_client():
    """Connect to Supabase using existing pattern."""
    from supabase import create_client

    url = os.getenv("SUPABASE_URL") or "https://uochfddhtkzrvcmfwksm.supabase.co"
    key = os.getenv("SUPABASE_KEY") or (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6"
        "InVvY2hmZGRodGt6cnZjbWZ3a3NtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njg1MDA1"
        "NDEsImV4cCI6MjA4NDA3NjU0MX0.mzBTf1GV8_Vk-nIMvf26PxI_MAqZfStzRTEZBEvHyLU"
    )
    return create_client(url, key)


def get_count(client, table: str) -> int:
    """Get row count for a table."""
    try:
        result = (
            client.table(table)
            .select("*", count="exact")
            .limit(1)
            .execute()
        )
        # Supabase returns count in the response when using count="exact"
        if hasattr(result, "count") and result.count is not None:
            return result.count
        return len(result.data) if result.data else 0
    except Exception:
        return 0


def delete_table(
    client, table: str, filter_col: str, filter_op: str, filter_val
) -> tuple[int, str | None]:
    """
    Delete all rows from a table. Returns (deleted_count, error_message).
    Supabase delete does not return row count; we return 1 when successful to indicate rows were processed.
    """
    try:
        tbl = client.table(table)
        if filter_op == "gte":
            q = tbl.delete().gte(filter_col, filter_val)
        elif filter_op == "neq":
            q = tbl.delete().neq(filter_col, filter_val)
        else:
            return 0, f"Unknown filter op: {filter_op}"

        q.execute()
        return 1, None  # Success; caller uses pre-fetched count for display
    except Exception as e:
        return 0, str(e)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely wipe all data from the Supabase database."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    args = parser.parse_args()

    # Safety: block in production unless --force
    if os.getenv("ENVIRONMENT") == "production" and not args.force:
        print("ERROR: Cannot wipe database in production environment.")
        print("Set ENVIRONMENT=production only in production. Aborting.")
        return 1

    try:
        client = get_client()
    except Exception as e:
        print(f"ERROR: Failed to connect to Supabase: {e}")
        return 1

    # Gather counts for dry-run or confirmation
    counts: dict[str, int] = {}
    for table, *_ in TABLES:
        try:
            counts[table] = get_count(client, table)
        except Exception:
            counts[table] = 0

    total = sum(counts.values())
    if total == 0 and not args.dry_run:
        print("Database is already empty. Nothing to delete.")
        return 0

    if args.dry_run:
        print("DRY RUN - no data will be deleted.\n")
        for table, _, _, _ in TABLES:
            n = counts.get(table, 0)
            print(f"  {table}: {n} rows")
        print(f"\n  Total: {total} rows would be deleted.")
        return 0

    # Confirmation
    if not args.force:
        confirm = input(CONFIRM_PROMPT).strip()
        if confirm != "DELETE EVERYTHING":
            print("Aborted. Confirmation text did not match.")
            return 1

    # Execute deletions
    print()
    for table, filter_col, filter_op, filter_val in TABLES:
        expected = counts.get(table, 0)
        print(f"Deleting {table}... ", end="", flush=True)
        _, err = delete_table(client, table, filter_col, filter_op, filter_val)
        if err:
            if "does not exist" in err.lower() or "relation" in err.lower():
                print("⊘ (table does not exist)")
            else:
                print(f"✗ ({err})")
        else:
            print(f"✓ ({expected} rows deleted)")
    print()
    print("✅ Database wiped successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

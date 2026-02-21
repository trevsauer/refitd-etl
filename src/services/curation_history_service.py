"""
Curation history service for saving and querying manual tag corrections.

Handles insertion into curation_history, product updates, and training data export.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from supabase import Client, create_client

from src.utils.tag_comparison import compute_tag_changes, infer_error_types

load_dotenv(Path(__file__).parent.parent.parent / ".env")

DEFAULT_SUPABASE_URL = "https://uochfddhtkzrvcmfwksm.supabase.co"
DEFAULT_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVvY2hmZGRodGt6cnZjbWZ3a3NtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njg1MDA1NDEsImV4cCI6MjA4NDA3NjU0MX0.mzBTf1GV8_Vk-nIMvf26PxI_MAqZfStzRTEZBEvHyLU"


def _format_deleted_tags_notes(deleted_tags: dict[str, Any]) -> str:
    """Build bulleted list of tag removal feedback from tags_final.deleted_tags."""
    if not deleted_tags or not isinstance(deleted_tags, dict):
        return ""
    lines: list[str] = []
    for field_name, entries in deleted_tags.items():
        if not entries:
            continue
        items = entries if isinstance(entries, list) else [entries]
        for item in items:
            if isinstance(item, dict):
                value = item.get("value") or item.get("tag")
                reason = item.get("reason")
                if value:
                    line = f"- Removed '{value}' from {field_name}"
                    if reason and str(reason).strip():
                        line += f": {reason.strip()}"
                    lines.append(line)
            elif isinstance(item, str):
                lines.append(f"- Removed '{item}' from {field_name}")
    if not lines:
        return ""
    return "Tag Removals:\n" + "\n".join(lines)


def _format_added_tags_notes(added_tags: dict[str, Any]) -> str:
    """Build bulleted list of tag addition feedback from tags_final.added_tags."""
    if not added_tags or not isinstance(added_tags, dict):
        return ""
    lines: list[str] = []
    for field_name, entries in added_tags.items():
        if not entries:
            continue
        items = entries if isinstance(entries, list) else [entries]
        for item in items:
            if isinstance(item, dict):
                value = item.get("value") or item.get("tag")
                reason = item.get("reason")
                if value:
                    line = f"- Added '{value}' to {field_name}"
                    if reason and str(reason).strip():
                        line += f": {reason.strip()}"
                    lines.append(line)
            elif isinstance(item, str):
                lines.append(f"- Added '{item}' to {field_name}")
    if not lines:
        return ""
    return "Tag Additions:\n" + "\n".join(lines)


def _format_modified_tags_notes(modified_tags: dict[str, Any]) -> str:
    """Build bulleted list of tag change feedback from tags_final.modified_tags."""
    if not modified_tags or not isinstance(modified_tags, dict):
        return ""
    lines: list[str] = []
    for field_name, entry in modified_tags.items():
        if not isinstance(entry, dict):
            continue
        from_val = entry.get("from")
        to_val = entry.get("to")
        reason = entry.get("reason")
        if from_val is not None and to_val is not None:
            line = f"- Changed {field_name} from '{from_val}' to '{to_val}'"
            if reason and str(reason).strip():
                line += f": {reason.strip()}"
            lines.append(line)
    if not lines:
        return ""
    return "Tag Changes:\n" + "\n".join(lines)


def _format_change_summary(changes: dict[str, Any]) -> str:
    """Build a human-readable summary of tag changes."""
    parts: list[str] = []

    added = changes.get("added", [])
    if added:
        parts.append(f"Added: {', '.join(added)}")

    removed = changes.get("removed", [])
    if removed:
        parts.append(f"Removed: {', '.join(removed)}")

    modified = changes.get("modified", [])
    if modified:
        mod_strs = [f"{m['category']}: {m['from']} → {m['to']}" for m in modified]
        parts.append(f"Modified: {'; '.join(mod_strs)}")

    return "; ".join(parts) if parts else "No changes"


class CurationHistoryService:
    """
    Service for saving curation history and querying training data.

    Uses the same Supabase connection pattern as SupabaseLoader.
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
    ):
        """
        Initialize the curation history service.

        Args:
            supabase_url: Supabase project URL (or SUPABASE_URL env var)
            supabase_key: Supabase key (or SUPABASE_KEY env var)
        """
        self.supabase_url = (
            supabase_url or os.getenv("SUPABASE_URL") or DEFAULT_SUPABASE_URL
        )
        self.supabase_key = (
            supabase_key or os.getenv("SUPABASE_KEY") or DEFAULT_SUPABASE_KEY
        )
        self.client: Client = create_client(self.supabase_url, self.supabase_key)

    def save_curation(
        self,
        product_id: str,
        original_tags: dict,
        corrected_tags: dict,
        curator_notes: Optional[str] = None,
        confidence: Optional[int] = None,
        curator_id: str = "unknown",
        model_version: Optional[str] = None,
        prompt_version: Optional[str] = None,
        error_types: Optional[list[str]] = None,
        include_in_training: Optional[bool] = None,
    ) -> int:
        """
        Save a curation record and update the product.

        Computes changes, infers error types (if not provided), inserts into curation_history,
        and updates products (curated_at, curated_by, training_eligible=True).

        Args:
            product_id: Product being curated
            original_tags: Pre-curation tags (e.g. from tags_ai_raw or tags_final)
            corrected_tags: Post-curation tags (curator's edits)
            curator_notes: Free-text explanation of changes
            confidence: 1-5 confidence in the correction (default: 3)
            curator_id: Who curated
            model_version: AI model version that produced original tags
            prompt_version: Tagging prompt version
            error_types: Override auto-inferred error types (from curator UI)
            include_in_training: Override default True (from curator UI)

        Returns:
            The curation_history record ID

        Raises:
            Exception: On database insert/update failure
        """
        changes = compute_tag_changes(original_tags, corrected_tags)
        error_types_val = (
            error_types if error_types is not None else infer_error_types(changes)
        )
        change_summary = _format_change_summary(changes)

        # Merge all feedback (deleted, added, modified tags + general notes) into curator_notes
        tags = corrected_tags or {}
        sections: list[str] = []
        for formatter, key in [
            (_format_deleted_tags_notes, "deleted_tags"),
            (_format_added_tags_notes, "added_tags"),
            (_format_modified_tags_notes, "modified_tags"),
        ]:
            section = formatter(tags.get(key))
            if section:
                sections.append(section)
        ui_notes = (curator_notes or "").strip()
        if ui_notes:
            sections.append(f"General Notes:\n{ui_notes}")
        combined_notes = "\n\n".join(sections) if sections else None

        if confidence is not None and not (1 <= confidence <= 5):
            raise ValueError("confidence must be between 1 and 5")
        confidence_val = confidence if confidence is not None else 3
        include_val = include_in_training if include_in_training is not None else True

        now = datetime.utcnow().isoformat() + "Z"

        record = {
            "product_id": product_id,
            "original_ai_tags": original_tags,
            "corrected_tags": corrected_tags,
            "change_summary": change_summary or None,
            "curator_notes": combined_notes,
            "error_types": error_types_val,
            "confidence_in_correction": confidence_val,
            "include_in_training": include_val,
            "curator_id": curator_id,
            "model_version": model_version,
            "prompt_version": prompt_version,
        }

        result = self.client.table("curation_history").insert(record).execute()
        if not result.data or len(result.data) == 0:
            raise RuntimeError("Failed to insert curation_history record")
        curation_id = result.data[0]["id"]

        # Update product
        self.client.table("products").update({
            "curated_at": now,
            "curated_by": curator_id,
            "training_eligible": True,
        }).eq("product_id", product_id).execute()

        return curation_id

    def get_training_data(
        self,
        min_confidence: int = 3,
        approved_only: bool = True,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Query curation_history for records suitable for training.

        Joins with products to include full product data.

        Args:
            min_confidence: Minimum confidence_in_correction (1-5)
            approved_only: If True, only include records with include_in_training=True
            limit: Max number of records to return (None = no limit)

        Returns:
            List of training examples, each with curation + product fields
        """
        if approved_only:
            query = self.client.table("curation_history_training_export").select("*")
        else:
            query = self.client.table("curation_history").select(
                "*, products!inner(name, category, description)"
            )

        query = (
            query.gte("confidence_in_correction", min_confidence)
            .order("created_at", desc=True)
        )

        if limit is not None:
            query = query.limit(limit)

        result = query.execute()
        return result.data if result.data else []

    def mark_for_training(self, curation_id: int, include: bool = True) -> None:
        """
        Update whether a curation record should be included in training data.

        Use this to exclude bad examples from the training set.

        Args:
            curation_id: The curation_history.id
            include: True to include in training, False to exclude
        """
        self.client.table("curation_history").update({
            "include_in_training": include,
        }).eq("id", curation_id).execute()

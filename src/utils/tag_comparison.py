"""
Compare tag dictionaries (original vs corrected) and produce a structured diff.

Used for curation history: when a curator edits tags_final, this computes
what changed relative to the AI's original tags_ai_raw → tags_final.
"""

from __future__ import annotations

from typing import Any


# Categories that hold lists of tags (multi-value)
LIST_CATEGORIES = frozenset({
    "style_identity",
    "context",
    "construction_details",
    "pairing_tags",
})

# Scalar categories (single value) - track from/to for modifications
SCALAR_CATEGORIES = frozenset({
    "fit",
    "formality",
    "length",
    "silhouette",
    "pattern",
    "top_layer_role",
    "shoe_type",
    "profile",
    "closure",
})


def _to_list(val: Any) -> list[str]:
    """Normalize a value to a list of tag strings."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(item) if isinstance(item, str) else str(item.get("tag", item)) for item in val]
    return [str(val)]


def _to_scalar(val: Any) -> str | None:
    """Normalize a scalar value to a string or None."""
    if val is None or val == "":
        return None
    if isinstance(val, dict):
        return val.get("tag") or val.get("value")
    return str(val)


def compute_tag_changes(original_tags: dict, corrected_tags: dict) -> dict:
    """
    Compare two tag dictionaries and return a structured diff.

    Args:
        original_tags: Original tags (e.g., from tags_ai_raw after policy,
                       or pre-curation tags_final).
        corrected_tags: Corrected tags (e.g., post-curation tags_final).

    Returns:
        Dict with keys:
        - added: Items present in corrected but not original (format "category:tag")
        - removed: Items present in original but not corrected (format "category:tag")
        - modified: Scalar fields that changed (list of {"category", "from", "to"})
        - unchanged: Items unchanged (format "category:tag" for lists,
                     "category:value" for scalars)

    Example:
        >>> orig = {
        ...     "style_identity": ["classic", "minimal"],
        ...     "fit": "regular",
        ...     "formality": "casual",
        ...     "construction_details": ["flat-front"],
        ... }
        >>> corr = {
        ...     "style_identity": ["classic", "preppy"],
        ...     "fit": "relaxed",
        ...     "formality": "casual",
        ...     "construction_details": ["flat-front", "pleated"],
        ... }
        >>> changes = compute_tag_changes(orig, corr)
        >>> changes["added"]
        ['style_identity:preppy', 'construction_details:pleated']
        >>> changes["removed"]
        ['style_identity:minimal']
        >>> changes["modified"]
        [{'category': 'fit', 'from': 'regular', 'to': 'relaxed'}]
        >>> "style_identity:classic" in changes["unchanged"]
        True
    """
    added: list[str] = []
    removed: list[str] = []
    modified: list[dict[str, str]] = []
    unchanged: list[str] = []

    all_categories = set(original_tags.keys()) | set(corrected_tags.keys())

    for cat in sorted(all_categories):
        if cat in LIST_CATEGORIES:
            orig_list = _to_list(original_tags.get(cat))
            corr_list = _to_list(corrected_tags.get(cat))
            orig_set = set(orig_list)
            corr_set = set(corr_list)

            for tag in corr_set - orig_set:
                added.append(f"{cat}:{tag}")
            for tag in orig_set - corr_set:
                removed.append(f"{cat}:{tag}")
            for tag in orig_set & corr_set:
                unchanged.append(f"{cat}:{tag}")

        elif cat in SCALAR_CATEGORIES:
            orig_val = _to_scalar(original_tags.get(cat))
            corr_val = _to_scalar(corrected_tags.get(cat))

            if orig_val is None and corr_val is not None:
                added.append(f"{cat}:{corr_val}")
            elif orig_val is not None and corr_val is None:
                removed.append(f"{cat}:{orig_val}")
            elif orig_val != corr_val:
                modified.append({"category": cat, "from": orig_val or "", "to": corr_val or ""})
            elif orig_val is not None:
                unchanged.append(f"{cat}:{orig_val}")

        else:
            # Unknown category: treat as scalar
            orig_val = _to_scalar(original_tags.get(cat))
            corr_val = _to_scalar(corrected_tags.get(cat))
            if isinstance(original_tags.get(cat), list) or isinstance(corrected_tags.get(cat), list):
                orig_list = _to_list(original_tags.get(cat))
                corr_list = _to_list(corrected_tags.get(cat))
                orig_set = set(orig_list)
                corr_set = set(corr_list)
                for tag in corr_set - orig_set:
                    added.append(f"{cat}:{tag}")
                for tag in orig_set - corr_set:
                    removed.append(f"{cat}:{tag}")
                for tag in orig_set & corr_set:
                    unchanged.append(f"{cat}:{tag}")
            else:
                if orig_val is None and corr_val is not None:
                    added.append(f"{cat}:{corr_val}")
                elif orig_val is not None and corr_val is None:
                    removed.append(f"{cat}:{orig_val}")
                elif orig_val != corr_val:
                    modified.append({"category": cat, "from": orig_val or "", "to": corr_val or ""})
                elif orig_val is not None:
                    unchanged.append(f"{cat}:{orig_val}")

    return {
        "added": sorted(added),
        "removed": sorted(removed),
        "modified": modified,
        "unchanged": sorted(unchanged),
    }


def infer_error_types(changes: dict) -> list[str]:
    """
    Infer what type of errors the AI model made based on the changes dict.

    Args:
        changes: Output from compute_tag_changes(original_tags, corrected_tags).

    Returns:
        List of error type strings. Possible values:
        - 'overtagging': 2+ tags were removed (model added too many)
        - 'undertagging': 2+ tags were added (model missed tags)
        - 'wrong_construction': construction_details were removed
        - 'wrong_style_identity': style_identity tags changed (added/removed)
        - 'wrong_fit': fit was modified
        - 'wrong_formality': formality was modified
        - 'low_confidence': many minor tweaks (4+ total changes, suggesting uncertainty)

    Example:
        >>> changes = {
        ...     "added": ["context:everyday", "pairing_tags:high-versatility"],
        ...     "removed": ["style_identity:minimal", "construction_details:pleated"],
        ...     "modified": [{"category": "fit", "from": "regular", "to": "relaxed"}],
        ...     "unchanged": [],
        ... }
        >>> infer_error_types(changes)
        ['low_confidence', 'overtagging', 'undertagging', 'wrong_construction', 'wrong_fit', 'wrong_style_identity']
    """
    added = changes.get("added", [])
    removed = changes.get("removed", [])
    modified = changes.get("modified", [])

    error_types: list[str] = []

    # Overtagging: 2+ tags removed
    if len(removed) >= 2:
        error_types.append("overtagging")

    # Undertagging: 2+ tags added
    if len(added) >= 2:
        error_types.append("undertagging")

    # Wrong construction: construction_details were removed
    if any(r.startswith("construction_details:") for r in removed):
        error_types.append("wrong_construction")

    # Wrong style_identity: style_identity tags changed
    if any(r.startswith("style_identity:") for r in removed) or any(
        a.startswith("style_identity:") for a in added
    ):
        error_types.append("wrong_style_identity")

    # Wrong fit: fit was modified
    if any(m.get("category") == "fit" for m in modified):
        error_types.append("wrong_fit")

    # Wrong formality: formality was modified
    if any(m.get("category") == "formality" for m in modified):
        error_types.append("wrong_formality")

    # Low confidence: many minor tweaks (4+ total changes)
    total_changes = len(added) + len(removed) + len(modified)
    if total_changes >= 4:
        error_types.append("low_confidence")

    return sorted(error_types)

#!/usr/bin/env python3
"""
Validate a JSONL training file before uploading to OpenAI.

Checks structure, roles, and tag schema. Reports errors, warnings, and statistics.

Usage:
    python scripts/validate_training_data.py training.jsonl
    python scripts/validate_training_data.py training.jsonl --strict
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

CHARS_PER_TOKEN_ESTIMATE = 4
COST_PER_M_TOKENS_GPT4O = 25.0

FOOTWEAR_KEYWORDS = frozenset({"shoe", "shoes", "boot", "boots", "footwear"})
REQUIRED_TAGS_APPAREL = {"style_identity", "fit", "formality", "length"}
REQUIRED_TAGS_FOOTWEAR = {"shoe_type", "profile", "formality"}
OPTIONAL_TAGS = {"context", "construction_details", "pairing_tags", "silhouette", "pattern"}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def is_footwear_category(category: str) -> bool:
    """Infer if product is footwear from category."""
    if not category:
        return False
    c = category.lower()
    return any(kw in c for kw in FOOTWEAR_KEYWORDS)


def validate_example(example: dict, line_num: int) -> tuple[list[str], list[str]]:
    """
    Validate one training example. Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if "messages" not in example:
        errors.append(f"Line {line_num}: Missing 'messages' key")
        return errors, warnings

    messages = example["messages"]
    if not isinstance(messages, list):
        errors.append(f"Line {line_num}: 'messages' must be an array")
        return errors, warnings

    if len(messages) != 3:
        errors.append(
            f"Line {line_num}: Expected 3 messages, got {len(messages)}"
        )
        return errors, warnings

    roles = [m.get("role") for m in messages if isinstance(m, dict)]
    if roles != ["system", "user", "assistant"]:
        errors.append(
            f"Line {line_num}: Invalid roles {roles}; expected "
            "['system', 'user', 'assistant']"
        )
        return errors, warnings

    for i, m in enumerate(messages):
        if not isinstance(m, dict) or "content" not in m:
            errors.append(f"Line {line_num}: Message {i+1} missing 'content'")
            return errors, warnings

    user_content = messages[1].get("content", "")
    assistant_content = messages[2].get("content", "")

    try:
        user_data = json.loads(user_content)
    except json.JSONDecodeError as e:
        errors.append(f"Line {line_num}: User content is not valid JSON: {e}")
        user_data = {}

    try:
        tags = json.loads(assistant_content)
    except json.JSONDecodeError as e:
        errors.append(
            f"Line {line_num}: Assistant content is not valid tags JSON: {e}"
        )
        return errors, warnings

    if not isinstance(tags, dict):
        errors.append(f"Line {line_num}: Tags must be a JSON object")
        return errors, warnings

    category = user_data.get("category") or user_data.get("title") or ""
    is_footwear = is_footwear_category(str(category))

    if is_footwear:
        required = REQUIRED_TAGS_FOOTWEAR
    else:
        required = REQUIRED_TAGS_APPAREL

    for field in required:
        if field not in tags or tags[field] is None:
            errors.append(f"Line {line_num}: Missing required tag '{field}'")
        elif field == "style_identity" and not is_footwear:
            if not isinstance(tags[field], list) or not tags[field]:
                errors.append(
                    f"Line {line_num}: 'style_identity' must be non-empty list"
                )
        elif field == "formality":
            if not isinstance(tags[field], str) or not tags[field].strip():
                errors.append(
                    f"Line {line_num}: 'formality' must be non-empty string"
                )

    for field in OPTIONAL_TAGS:
        if field not in tags and field in (
            "context",
            "pairing_tags",
        ):
            warnings.append(
                f"Line {line_num}: Missing optional '{field}' (recommended)"
            )

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate JSONL training file for OpenAI fine-tuning."
    )
    parser.add_argument(
        "file",
        type=Path,
        help="Path to JSONL training file",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors (fail on warnings)",
    )
    args = parser.parse_args()

    path = args.file.resolve()
    if not path.exists():
        print(f"❌ File not found: {path}")
        return 1

    all_errors: list[str] = []
    all_warnings: list[str] = []
    total_tokens = 0
    n_lines = 0
    category_counts: Counter = Counter()
    tag_field_counts: Counter = Counter()

    with open(path) as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            n_lines += 1
            try:
                example = json.loads(line)
            except json.JSONDecodeError as e:
                all_errors.append(f"Line {line_num}: Invalid JSON: {e}")
                continue

            errors, warnings = validate_example(example, line_num)
            all_errors.extend(errors)
            all_warnings.extend(warnings)

            example_str = json.dumps(example, ensure_ascii=False)
            total_tokens += estimate_tokens(example_str)

            try:
                user_data = json.loads(
                    example.get("messages", [{}])[1].get("content", "{}")
                )
                cat = user_data.get("category") or "unknown"
                category_counts[cat] += 1
            except (IndexError, json.JSONDecodeError):
                pass

            try:
                tags = json.loads(
                    example.get("messages", [{}])[2].get("content", "{}")
                )
                if isinstance(tags, dict):
                    for k in tags:
                        tag_field_counts[k] += 1
            except (IndexError, json.JSONDecodeError):
                pass
    passed = not all_errors and (not args.strict or not all_warnings)

    if passed:
        print("✅ Validation passed\n")
    else:
        print("❌ Validation failed\n")

    if all_errors:
        print("Errors:")
        for e in all_errors:
            print(f"  • {e}")
        print()

    if all_warnings:
        print("Warnings:")
        for w in all_warnings[:20]:
            print(f"  • {w}")
        if len(all_warnings) > 20:
            print(f"  ... and {len(all_warnings) - 20} more")
        print()

    print("Statistics")
    print("-" * 40)
    print(f"  Total examples:  {n_lines}")
    print(f"  Est. tokens:     ~{total_tokens:,}")
    cost = (total_tokens / 1_000_000) * COST_PER_M_TOKENS_GPT4O
    print(f"  Avg tokens/example: ~{total_tokens // max(1, n_lines):,}")
    print(f"  Est. cost (GPT-4o): ~${cost:.2f}")
    print()

    if category_counts:
        print("Category distribution")
        print("-" * 40)
        for cat, count in category_counts.most_common(15):
            print(f"  {cat:<30} {count:>5}")
        print()

    if tag_field_counts:
        print("Tag field distribution")
        print("-" * 40)
        for field, count in tag_field_counts.most_common():
            pct = 100 * count / n_lines if n_lines else 0
            print(f"  {field:<25} {count:>5} ({pct:.0f}%)")
        print()

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

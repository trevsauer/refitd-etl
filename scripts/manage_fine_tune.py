#!/usr/bin/env python3
"""
Manage OpenAI fine-tuning: upload, start, status, list, cancel.

Usage:
    python scripts/manage_fine_tune.py upload training.jsonl
    python scripts/manage_fine_tune.py start file-xxx
    python scripts/manage_fine_tune.py status ftjob-xxx
    python scripts/manage_fine_tune.py list
    python scripts/manage_fine_tune.py cancel ftjob-xxx

Requires: OPENAI_API_KEY in environment or .env
Run from project root.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()

# Cost estimates (per 1M tokens, approximate)
COST_PER_M_TOKENS_GPT4O = 25.0
COST_PER_M_TOKENS_GPT4O_MINI = 4.0
CHARS_PER_TOKEN_ESTIMATE = 4


def get_client():
    """Get OpenAI client. Exits if API key missing."""
    try:
        from openai import OpenAI
    except ImportError:
        console.print("[red]OpenAI package not installed. Run: pip install openai[/red]")
        sys.exit(1)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        console.print(
            "[red]OPENAI_API_KEY not set. Add it to .env or export it.[/red]"
        )
        sys.exit(1)

    return OpenAI(api_key=api_key)


def estimate_tokens_from_file(path: Path) -> int:
    """Estimate tokens from file size (chars / 4)."""
    size = path.stat().st_size
    return max(1, size // CHARS_PER_TOKEN_ESTIMATE)


def count_lines(path: Path) -> int:
    """Count non-empty lines in JSONL file."""
    n = 0
    with open(path) as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def cmd_upload(args: argparse.Namespace) -> int:
    """Upload training file to OpenAI."""
    path = Path(args.filepath).resolve()
    if not path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        return 1

    client = get_client()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Uploading {task.description}..."),
        console=console,
    ) as progress:
        task = progress.add_task(path.name, total=None)
        with open(path, "rb") as f:
            file_obj = client.files.create(file=f, purpose="fine-tune")
        progress.update(task, completed=True)

    n_lines = count_lines(path)
    est_tokens = estimate_tokens_from_file(path)
    cost_mini = (est_tokens / 1_000_000) * COST_PER_M_TOKENS_GPT4O_MINI
    cost_4o = (est_tokens / 1_000_000) * COST_PER_M_TOKENS_GPT4O

    console.print(f"[green]✓ Upload complete[/green]")
    console.print(f"  File ID:     [bold]{file_obj.id}[/bold]")
    console.print(f"  Lines:       {n_lines}")
    console.print(f"  Est. tokens: ~{est_tokens:,}")
    console.print(
        f"  Est. cost:   [dim]gpt-4o-mini ~${cost_mini:.2f} | gpt-4o ~${cost_4o:.2f}[/dim]"
    )
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Start fine-tuning job with given file ID."""
    file_id = args.file_id
    client = get_client()

    model = getattr(args, "model", None) or "gpt-4o-mini-2024-07-18"
    suffix = getattr(args, "suffix", None) or "refitd-tagger"

    # Cost estimate (we don't have token count from file_id; use placeholder)
    console.print("[yellow]Cost estimate: Fine-tuning is billed per token.[/yellow]")
    console.print(
        "[dim]  gpt-4o-mini: ~$4/1M tokens | gpt-4o: ~$25/1M tokens[/dim]\n"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Starting fine-tuning job..."),
        console=console,
    ) as progress:
        task = progress.add_task("Creating job", total=None)
        job = client.fine_tuning.jobs.create(
            training_file=file_id,
            model=model,
            suffix=suffix,
        )
        progress.update(task, completed=True)

    console.print(f"[green]✓ Fine-tuning job started[/green]")
    console.print(f"  Job ID:   [bold]{job.id}[/bold]")
    console.print(f"  Status:   {job.status}")
    console.print(f"  Model:    {model}")
    console.print(
        "[dim]Run 'python scripts/manage_fine_tune.py status "
        f"{job.id}' to check progress.[/dim]"
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Check status of fine-tuning job."""
    job_id = args.job_id
    client = get_client()

    try:
        job = client.fine_tuning.jobs.retrieve(job_id)
    except Exception as e:
        console.print(f"[red]Failed to retrieve job: {e}[/red]")
        return 1

    status = job.status
    if status == "succeeded":
        color = "green"
        icon = "✅"
    elif status in ("failed", "cancelled"):
        color = "red"
        icon = "❌"
    elif status in ("validating_files", "queued", "running"):
        color = "yellow"
        icon = "⏳"
    else:
        color = "white"
        icon = "•"

    console.print(f"{icon} [bold {color}]Status: {status}[/bold {color}]")
    console.print(f"  Job ID:    {job.id}")
    console.print(f"  Model:     {job.model}")
    console.print(f"  Created:   {job.created_at}")

    if job.fine_tuned_model:
        console.print(f"[green]  Model ID:  [bold]{job.fine_tuned_model}[/bold][/green]")

    if status == "failed" and getattr(job, "error", None):
        err = job.error
        console.print(f"[red]  Error: {err}[/red]")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List all fine-tuning jobs."""
    client = get_client()

    limit = getattr(args, "limit", None) or 20
    jobs = list(client.fine_tuning.jobs.list(limit=limit))

    if not jobs:
        console.print("[yellow]No fine-tuning jobs found.[/yellow]")
        return 0

    table = Table(
        title="Fine-tuning Jobs",
        header_style="bold cyan",
    )
    table.add_column("Job ID", style="dim")
    table.add_column("Status")
    table.add_column("Model")
    table.add_column("Created")
    table.add_column("Fine-tuned Model", style="green")

    for job in jobs:
        status = job.status
        if status == "succeeded":
            status_str = "[green]succeeded[/green]"
        elif status in ("failed", "cancelled"):
            status_str = "[red]" + status + "[/red]"
        else:
            status_str = f"[yellow]{status}[/yellow]"

        model_id = job.fine_tuned_model or "—"
        table.add_row(
            job.id,
            status_str,
            job.model or "—",
            str(job.created_at) if job.created_at else "—",
            model_id,
        )

    console.print(table)
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    """Cancel a running fine-tuning job."""
    job_id = args.job_id
    client = get_client()

    try:
        job = client.fine_tuning.jobs.cancel(job_id)
    except Exception as e:
        console.print(f"[red]Failed to cancel job: {e}[/red]")
        return 1

    console.print(f"[yellow]Job cancelled: {job.id}[/yellow]")
    console.print(f"  Status: {job.status}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage OpenAI fine-tuning: upload, start, status, list, cancel."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # upload
    p_upload = subparsers.add_parser("upload", help="Upload training file to OpenAI")
    p_upload.add_argument("filepath", help="Path to JSONL training file")
    p_upload.set_defaults(func=cmd_upload)

    # start
    p_start = subparsers.add_parser("start", help="Start fine-tuning job")
    p_start.add_argument("file_id", help="OpenAI file ID from upload")
    p_start.add_argument(
        "--model",
        default="gpt-4o-mini-2024-07-18",
        help="Base model (default: gpt-4o-mini-2024-07-18)",
    )
    p_start.add_argument(
        "--suffix",
        default="refitd-tagger",
        help="Suffix for fine-tuned model name (default: refitd-tagger)",
    )
    p_start.set_defaults(func=cmd_start)

    # status
    p_status = subparsers.add_parser("status", help="Check fine-tuning job status")
    p_status.add_argument("job_id", help="Fine-tuning job ID")
    p_status.set_defaults(func=cmd_status)

    # list
    p_list = subparsers.add_parser("list", help="List all fine-tuning jobs")
    p_list.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max jobs to show (default: 20)",
    )
    p_list.set_defaults(func=cmd_list)

    # cancel
    p_cancel = subparsers.add_parser("cancel", help="Cancel a running job")
    p_cancel.add_argument("job_id", help="Fine-tuning job ID")
    p_cancel.set_defaults(func=cmd_cancel)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

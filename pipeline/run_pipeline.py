"""Two Brooklyn Guys Podcast - Pipeline CLI Orchestrator.

Usage:
    python run_pipeline.py setup [--check]
    python run_pipeline.py process <episodes...>
    python run_pipeline.py edit <episodes...>
    python run_pipeline.py publish <episodes...>
    python run_pipeline.py social <episodes...>
    python run_pipeline.py status [episodes...]
"""

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.helpers.episode_manifest import Manifest
from scripts.helpers.review_gate import gate_status

console = Console()


def resolve_episodes(episode_ids: tuple[str, ...] | None = None) -> list:
    """Resolve episode IDs to Episode objects."""
    manifest = Manifest.load()
    if not manifest.episodes:
        console.print("[red]No episodes found. Run 'setup' and 'normalize' first.[/red]")
        return []

    if not episode_ids:
        return manifest.episodes

    resolved = []
    for eid in episode_ids:
        ep = manifest.get_episode(eid)
        if ep:
            resolved.append(ep)
        else:
            console.print(f"[yellow]Episode '{eid}' not found, skipping[/yellow]")
    return resolved


@click.group()
def cli():
    """Two Brooklyn Guys Podcast - Production Pipeline"""
    pass


@cli.command()
@click.option("--check", is_flag=True, help="Only check environment, don't install")
def setup(check):
    """Step 0: Validate environment and install dependencies."""
    from scripts.s00_setup import run_check, run_install
    if check:
        run_check()
    else:
        run_install()


@cli.command()
@click.option("--dry-run", is_flag=True, help="Show what would happen without copying")
def normalize(dry_run):
    """Step 1: Normalize source files into episode directories."""
    from scripts.s01_normalize import normalize as run_normalize
    run_normalize(dry_run=dry_run)


@cli.command()
@click.argument("episodes", nargs=-1)
@click.option("--whisper", is_flag=True, help="Use Whisper for transcription")
@click.option("--whisper-model", default="base", help="Whisper model size")
def process(episodes, whisper, whisper_model):
    """Steps 1-4: Process episodes (normalize, extract, transcribe, analyze)."""
    ep_ids = list(episodes) if episodes else None

    # Step 1: Normalize (only if manifest doesn't exist)
    manifest_path = Path("episodes/manifest.json")
    if not manifest_path.exists():
        from scripts.s01_normalize import normalize
        normalize()

    # Step 2: Extract audio
    from scripts.s02_extract_audio import extract_all
    extract_all(ep_ids)

    # Step 3: Transcribe
    from scripts.s03_transcribe import transcribe_all
    transcribe_all(ep_ids, use_whisper=whisper, whisper_model=whisper_model)

    # Step 4: Analyze (requires Gate 1 approval)
    from scripts.s04_analyze import analyze_all
    analyze_all(ep_ids)

    console.print("\n[bold]Process complete![/bold]")
    console.print("Review the following files before continuing:")
    console.print("  1. reviews/01_transcript_review.md (Gate 1)")
    console.print("  2. reviews/02_analysis_review.json (Gate 2)")
    console.print("\nOnce both are APPROVED, run: python run_pipeline.py edit <episodes>\n")


@cli.command()
@click.argument("episodes", nargs=-1)
def edit(episodes):
    """Steps 5-7: Edit episodes (audio, video, master). Requires Gate 2 approval."""
    ep_ids = list(episodes) if episodes else None

    # Step 5: Edit audio
    from scripts.s05_edit_audio import edit_all as edit_audio
    edit_audio(ep_ids)

    # Step 6: Edit video
    from scripts.s06_edit_video import edit_all as edit_video
    edit_video(ep_ids)

    # Step 7: Master (requires Gate 3 approval)
    from scripts.s07_master import master_all
    master_all(ep_ids)

    console.print("\n[bold]Edit complete![/bold]")
    console.print("Review: reviews/03_edit_review.md (Gate 3)")
    console.print("\nOnce APPROVED, run: python run_pipeline.py publish <episodes>\n")


@cli.command()
@click.argument("episodes", nargs=-1)
@click.option("--validate-only", is_flag=True, help="Only validate RSS feed")
def publish(episodes, validate_only):
    """Steps 8-10: Publish episodes (metadata, show notes, deploy). Requires Gate 3."""
    ep_ids = list(episodes) if episodes else None

    if not validate_only:
        # Step 8: Metadata
        from scripts.s08_metadata import tag_all
        tag_all(ep_ids)

        # Step 9: Show notes
        from scripts.s09_shownotes import generate_all
        generate_all(ep_ids)

    # Step 10: Publish
    from scripts.s10_publish import publish_all
    publish_all(ep_ids, validate_only=validate_only)


@cli.command()
@click.argument("episodes", nargs=-1)
def social(episodes):
    """Step 11: Generate social media clips and audiograms."""
    ep_ids = list(episodes) if episodes else None

    from scripts.s11_social_clips import generate_all_clips
    generate_all_clips(ep_ids)


@cli.command()
@click.argument("episodes", nargs=-1)
def status(episodes):
    """Show pipeline status for all or specified episodes."""
    manifest = Manifest.load()
    if not manifest.episodes:
        console.print("[red]No episodes found. Run normalize first.[/red]")
        return

    eps = manifest.episodes
    if episodes:
        eps = [ep for ep in eps if ep.episode_id in [e.lower() for e in episodes]]

    table = Table(title="Two Brooklyn Guys — Pipeline Status")
    table.add_column("Episode", style="cyan")
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Audio")
    table.add_column("Transcript")
    table.add_column("G1", style="bold")
    table.add_column("Analysis")
    table.add_column("G2", style="bold")
    table.add_column("Edit")
    table.add_column("G3", style="bold")
    table.add_column("Master")
    table.add_column("G4", style="bold")
    table.add_column("Live")

    for ep in eps:
        gates = gate_status(ep.dir)

        def check(path: Path) -> str:
            return "[green]done[/green]" if path.exists() else "[dim]--[/dim]"

        def gate_str(g: int) -> str:
            s = gates.get(g, "NOT_STARTED")
            if s == "APPROVED":
                return "[green]OK[/green]"
            elif s == "PENDING":
                return "[yellow]PEND[/yellow]"
            return "[dim]--[/dim]"

        table.add_row(
            ep.episode_id.upper(),
            ep.date,
            ep.recording_type[:3],
            check(ep.dir / "audio" / "raw.wav"),
            check(ep.dir / "transcript" / "raw.json"),
            gate_str(1),
            check(ep.dir / "analysis" / "content_analysis.json"),
            gate_str(2),
            check(ep.dir / "audio" / "assembled.wav"),
            gate_str(3),
            check(ep.dir / "audio" / "master.mp3"),
            gate_str(4),
            "[green]LIVE[/green]" if ep.status == "published" else "[dim]--[/dim]",
        )

    console.print(table)


if __name__ == "__main__":
    # The CLI uses module names with numbers, but Python imports can't start with numbers.
    # So we use the actual filenames with number prefixes directly.
    # Remap the import references to use the actual script paths.

    # Override the import mechanism for the numbered scripts
    import importlib.util

    scripts_dir = Path(__file__).resolve().parent / "scripts"

    # Create aliases so the CLI commands can import properly
    script_mapping = {
        "scripts.s00_setup": "00_setup.py",
        "scripts.s01_normalize": "01_normalize.py",
        "scripts.s02_extract_audio": "02_extract_audio.py",
        "scripts.s03_transcribe": "03_transcribe.py",
        "scripts.s04_analyze": "04_analyze.py",
        "scripts.s05_edit_audio": "05_edit_audio.py",
        "scripts.s06_edit_video": "06_edit_video.py",
        "scripts.s07_master": "07_master.py",
        "scripts.s08_metadata": "08_metadata.py",
        "scripts.s09_shownotes": "09_shownotes.py",
        "scripts.s10_publish": "10_publish.py",
        "scripts.s11_social_clips": "11_social_clips.py",
    }

    for module_name, filename in script_mapping.items():
        filepath = scripts_dir / filename
        if filepath.exists():
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)

    cli()

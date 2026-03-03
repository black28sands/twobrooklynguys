"""Step 1: Normalize source material — standardize folder/file naming and create episode directories."""

import re
import shutil
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

# Add parent to path for helper imports
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config, source_root, episodes_root
from scripts.helpers.episode_manifest import Episode, EpisodeSource, Manifest

console = Console()

# Map of month abbreviations used in folder names
MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_folder_date(folder_name: str) -> datetime | None:
    """Parse a date from inconsistent folder names like 'Aug 7', 'Jan 8,2026', 'Feb 5, 2026'."""
    name = folder_name.strip()

    # Try pattern: "Mon DD, YYYY" or "Mon DD,YYYY"
    match = re.match(r"(\w+)\s+(\d+)\s*,\s*(\d{4})", name)
    if match:
        month_str, day, year = match.groups()
        month = MONTH_MAP.get(month_str.lower())
        if month:
            return datetime(int(year), month, int(day))

    # Try pattern: "Mon DD" (no year — assume 2025 for these older episodes)
    match = re.match(r"(\w+)\s+(\d+)$", name)
    if match:
        month_str, day = match.groups()
        month = MONTH_MAP.get(month_str.lower())
        if month:
            return datetime(2025, month, int(day))

    return None


def detect_recording_type(mp4_name: str) -> str:
    """Detect if the recording is a regular mastermind or impromptu meeting."""
    if "impromptu" in mp4_name.lower():
        return "impromptu"
    return "regular"


def find_file_case_insensitive(directory: Path, target_name: str) -> Path | None:
    """Find a file in directory matching target_name case-insensitively."""
    target_lower = target_name.lower()
    for f in directory.iterdir():
        if f.name.lower() == target_lower:
            return f
    return None


def scan_source_folders(root: Path) -> list[dict]:
    """Scan the source root for episode folders and their contents.

    Returns a list of dicts sorted by date with folder info.
    """
    episodes_found = []

    for item in root.iterdir():
        if not item.is_dir():
            continue
        # Skip pipeline/episodes/website/assets directories
        if item.name in ("pipeline", "episodes", "website", "assets", ".git"):
            continue

        date = parse_folder_date(item.name)
        if date is None:
            console.print(f"[yellow]Warning: Could not parse date from folder '{item.name}', skipping[/yellow]")
            continue

        # Find MP4 file
        mp4_files = list(item.glob("*.mp4"))
        if not mp4_files:
            console.print(f"[yellow]Warning: No MP4 found in '{item.name}', skipping[/yellow]")
            continue

        mp4 = mp4_files[0]

        # Find transcript (various naming patterns)
        transcript = (
            find_file_case_insensitive(item, "Transcript.docx")
            or find_file_case_insensitive(item, "Transcript long.docx")
        )

        # Find summary
        summary = find_file_case_insensitive(item, "Summary.docx")

        # Find short summary (various capitalizations)
        short_summary = (
            find_file_case_insensitive(item, "Shortsummary.docx")
            or find_file_case_insensitive(item, "shortsummary.docx")
            or find_file_case_insensitive(item, "Transcript short.docx")
        )

        # Collect all other files
        known = {mp4.name}
        if transcript:
            known.add(transcript.name)
        if summary:
            known.add(summary.name)
        if short_summary:
            known.add(short_summary.name)
        extra_files = [f.name for f in item.iterdir() if f.is_file() and f.name not in known]

        episodes_found.append({
            "date": date,
            "folder_name": item.name,
            "folder_path": item,
            "mp4": mp4,
            "transcript": transcript,
            "summary": summary,
            "short_summary": short_summary,
            "extra_files": extra_files,
            "recording_type": detect_recording_type(mp4.name),
        })

    # Sort by date
    episodes_found.sort(key=lambda x: x["date"])
    return episodes_found


def normalize(dry_run: bool = False) -> Manifest:
    """Run the normalization step. Creates episode directories and copies source files.

    Args:
        dry_run: If True, only print what would happen without copying files.

    Returns:
        The generated Manifest.
    """
    root = source_root()
    ep_root = episodes_root()
    ep_root.mkdir(parents=True, exist_ok=True)

    console.print("[bold]Step 1: Normalizing source material...[/bold]\n")

    # Scan source folders
    episodes_found = scan_source_folders(root)

    if not episodes_found:
        console.print("[red]No episode folders found![/red]")
        return Manifest()

    console.print(f"Found [bold]{len(episodes_found)}[/bold] episodes\n")

    # Display what we found
    table = Table(title="Episode Mapping")
    table.add_column("#", style="cyan")
    table.add_column("ID", style="cyan")
    table.add_column("Date")
    table.add_column("Original Folder")
    table.add_column("Type")
    table.add_column("MP4 Size")
    table.add_column("Transcript")
    table.add_column("Summary")

    manifest = Manifest()

    for i, ep in enumerate(episodes_found, 1):
        ep_id = f"ep{i:02d}"
        date_str = ep["date"].strftime("%Y-%m-%d")
        folder_name = f"{ep_id}-{date_str}"
        mp4_size_mb = ep["mp4"].stat().st_size / (1024 * 1024)

        source = EpisodeSource(
            original_folder=ep["folder_name"],
            mp4_filename=ep["mp4"].name,
            mp4_size_bytes=ep["mp4"].stat().st_size,
            transcript_filename=ep["transcript"].name if ep["transcript"] else "",
            summary_filename=ep["summary"].name if ep["summary"] else None,
            short_summary_filename=ep["short_summary"].name if ep["short_summary"] else None,
            extra_files=ep["extra_files"],
        )

        episode = Episode(
            episode_number=i,
            episode_id=ep_id,
            date=date_str,
            folder_name=folder_name,
            recording_type=ep["recording_type"],
            source=source,
            status="new",
        )
        manifest.episodes.append(episode)

        has_transcript = "yes" if ep["transcript"] else "[red]NO[/red]"
        has_summary = "yes" if ep["summary"] else "[yellow]no[/yellow]"

        table.add_row(
            str(i),
            ep_id.upper(),
            date_str,
            ep["folder_name"],
            ep["recording_type"],
            f"{mp4_size_mb:.0f} MB",
            has_transcript,
            has_summary,
        )

    console.print(table)

    if dry_run:
        console.print("\n[yellow]Dry run — no files copied.[/yellow]")
        return manifest

    # Create episode directories and copy files
    console.print("\n[bold]Copying source files...[/bold]\n")

    for i, (episode, ep_data) in enumerate(zip(manifest.episodes, episodes_found)):
        ep_dir = ep_root / episode.folder_name
        source_dir = ep_dir / "source"

        # Create all subdirectories
        for subdir in ["source", "audio", "video", "transcript", "analysis", "reviews", "metadata", "social"]:
            (ep_dir / subdir).mkdir(parents=True, exist_ok=True)

        # Copy MP4
        dest_mp4 = source_dir / f"{episode.episode_id}.mp4"
        if not dest_mp4.exists():
            console.print(f"  Copying {episode.episode_id}: MP4 ({episode.source.mp4_size_bytes / 1024 / 1024:.0f} MB)...")
            shutil.copy2(ep_data["mp4"], dest_mp4)
        else:
            console.print(f"  {episode.episode_id}: MP4 already exists, skipping")

        # Copy transcript
        if ep_data["transcript"]:
            dest_transcript = source_dir / "transcript.docx"
            if not dest_transcript.exists():
                shutil.copy2(ep_data["transcript"], dest_transcript)

        # Copy summary
        if ep_data["summary"]:
            dest_summary = source_dir / "summary.docx"
            if not dest_summary.exists():
                shutil.copy2(ep_data["summary"], dest_summary)

        # Copy short summary
        if ep_data["short_summary"]:
            dest_short = source_dir / "short_summary.docx"
            if not dest_short.exists():
                shutil.copy2(ep_data["short_summary"], dest_short)

        # Copy any extra files
        for extra in ep_data["extra_files"]:
            dest_extra = source_dir / extra
            if not dest_extra.exists():
                shutil.copy2(ep_data["folder_path"] / extra, dest_extra)

        episode.status = "normalized"

    # Save manifest
    manifest.save()
    console.print(f"\n[green]Manifest saved to {ep_root / 'manifest.json'}[/green]")

    # Flag EP02 if it's the small one
    for ep in manifest.episodes:
        if ep.source.mp4_size_bytes < 50_000_000:  # < 50 MB
            console.print(
                f"\n[yellow]Warning: {ep.episode_id.upper()} ({ep.date}) is only "
                f"{ep.source.mp4_size_bytes / 1024 / 1024:.0f} MB — "
                f"may be a short or partial recording. Review before processing.[/yellow]"
            )

    console.print(f"\n[bold green]Normalization complete! {len(manifest.episodes)} episodes organized.[/bold green]\n")
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 1: Normalize source material")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Only show what would happen, don't copy files",
    )
    args = parser.parse_args()

    normalize(dry_run=args.dry_run)

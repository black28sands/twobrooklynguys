"""Step 2: Extract audio from MP4 files and create WAV masters."""

import sys
from pathlib import Path

from rich.console import Console
from rich.progress import Progress

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config
from scripts.helpers.episode_manifest import Manifest
from scripts.helpers.ffmpeg_wrapper import (
    check_ffmpeg,
    check_ffprobe,
    detect_silence,
    extract_audio,
    get_duration,
    get_media_info,
)

console = Console()


def extract_episode_audio(episode, config: dict) -> dict:
    """Extract audio from a single episode's MP4 and return info."""
    ep_dir = episode.dir
    source_mp4 = ep_dir / "source" / f"{episode.episode_id}.mp4"
    output_wav = ep_dir / "audio" / "raw.wav"

    if not source_mp4.exists():
        console.print(f"  [red]{episode.episode_id}: Source MP4 not found at {source_mp4}[/red]")
        return {"error": "MP4 not found"}

    audio_cfg = config["audio"]
    info = {}

    # Get media info
    media_info = get_media_info(source_mp4)
    info["duration"] = float(media_info["format"]["duration"])
    info["size_bytes"] = int(media_info["format"]["size"])

    # Find video and audio streams
    for stream in media_info.get("streams", []):
        if stream["codec_type"] == "video":
            info["video_codec"] = stream.get("codec_name", "unknown")
            info["resolution"] = f"{stream.get('width', '?')}x{stream.get('height', '?')}"
        elif stream["codec_type"] == "audio":
            info["audio_codec"] = stream.get("codec_name", "unknown")
            info["audio_sample_rate"] = stream.get("sample_rate", "unknown")
            info["audio_channels"] = stream.get("channels", "unknown")

    # Extract audio
    if output_wav.exists():
        console.print(f"  {episode.episode_id}: WAV already exists, skipping extraction")
    else:
        console.print(f"  {episode.episode_id}: Extracting audio ({info['duration'] / 60:.1f} min)...")
        extract_audio(
            source_mp4,
            output_wav,
            sample_rate=audio_cfg["sample_rate"],
            channels=audio_cfg["channels"],
        )

    # Detect silence regions
    if output_wav.exists():
        console.print(f"  {episode.episode_id}: Detecting silence regions...")
        silences = detect_silence(output_wav)
        info["silence_regions"] = len(silences)

        # Save silence data for later use
        import json
        silence_path = ep_dir / "audio" / "silence_regions.json"
        with open(silence_path, "w", encoding="utf-8") as f:
            json.dump(silences, f, indent=2)

    return info


def extract_all(episode_ids: list[str] | None = None) -> None:
    """Extract audio for all (or specified) episodes."""
    config = get_config()
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found. Run Step 1 (normalize) first.[/red]")
        return

    if not check_ffmpeg():
        console.print("[red]FFmpeg not found. Install it: winget install Gyan.FFmpeg[/red]")
        return

    if not check_ffprobe():
        console.print("[red]FFprobe not found. It should come with FFmpeg.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 2: Extracting audio from {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        info = extract_episode_audio(episode, config)

        if "error" not in info:
            episode.duration_seconds = info.get("duration", 0)

            # Flag short recordings
            if episode.source.mp4_size_bytes < 50_000_000:
                console.print(
                    f"  [yellow]{episode.episode_id}: Small recording "
                    f"({episode.source.mp4_size_bytes / 1024 / 1024:.0f} MB) — "
                    f"review before processing further[/yellow]"
                )

    # Update manifest with durations
    manifest.save()
    console.print(f"\n[bold green]Audio extraction complete![/bold green]\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 2: Extract audio from MP4s")
    parser.add_argument("episodes", nargs="*", help="Episode IDs to process (e.g. ep01 ep02). Omit for all.")
    args = parser.parse_args()

    extract_all(args.episodes or None)

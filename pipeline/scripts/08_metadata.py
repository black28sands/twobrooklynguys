"""Step 8: Embed metadata — ID3 tags, chapter markers, artwork into final files."""

import json
import subprocess
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config, assets_root
from scripts.helpers.episode_manifest import Manifest
import scripts.helpers.ffmpeg_wrapper as _fw

console = Console()


def embed_id3_tags(episode, config: dict) -> None:
    """Embed ID3v2 tags into the master MP3."""
    master_mp3 = episode.dir / "audio" / "master.mp3"
    if not master_mp3.exists():
        console.print(f"  [yellow]{episode.episode_id}: No master MP3 found, skipping tags[/yellow]")
        return

    from mutagen.mp3 import MP3
    from mutagen.id3 import (
        ID3, TIT2, TPE1, TALB, TDRC, TCON, COMM, TCOP, TRCK, WXXX,
    )

    podcast_cfg = config["podcast"]

    # Load or create ID3 tags
    try:
        audio = MP3(str(master_mp3), ID3=ID3)
    except Exception:
        audio = MP3(str(master_mp3))
        audio.add_tags()

    # Load the review to get the chosen title
    review_path = episode.dir / "reviews" / "02_analysis_review.json"
    title = f"EP{episode.episode_number:02d}"
    if review_path.exists():
        with open(review_path) as f:
            review = json.load(f)
        chosen = review.get("chosen_title")
        if chosen:
            title = f"EP{episode.episode_number:02d}: {chosen}"
        elif review.get("title_options"):
            title = f"EP{episode.episode_number:02d}: {review['title_options'][0]}"

    # Set tags
    audio["TIT2"] = TIT2(encoding=3, text=[title])
    audio["TPE1"] = TPE1(encoding=3, text=[podcast_cfg["name"]])
    audio["TALB"] = TALB(encoding=3, text=[podcast_cfg["name"]])
    audio["TDRC"] = TDRC(encoding=3, text=[episode.date])
    audio["TCON"] = TCON(encoding=3, text=["Podcast"])
    audio["TRCK"] = TRCK(encoding=3, text=[str(episode.episode_number)])

    if podcast_cfg.get("website"):
        audio.tags.add(WXXX(encoding=3, url=podcast_cfg["website"], desc="Podcast URL"))

    audio["TCOP"] = TCOP(encoding=3, text=[f"© {episode.date[:4]} {podcast_cfg['name']}"])

    # Embed summary as comment
    analysis_path = episode.dir / "analysis" / "content_analysis.json"
    if analysis_path.exists():
        with open(analysis_path) as f:
            analysis = json.load(f)
        summary = analysis.get("episode_summary", "")
        if summary:
            audio["COMM"] = COMM(encoding=3, lang="eng", desc="", text=[summary])

    # Embed cover art if available
    from mutagen.id3 import APIC
    artwork_path = assets_root() / "artwork" / "cover-3000x3000.png"
    if not artwork_path.exists():
        artwork_path = assets_root() / "artwork" / "cover-300x300.png"

    if artwork_path.exists():
        mime = "image/png" if artwork_path.suffix == ".png" else "image/jpeg"
        with open(artwork_path, "rb") as f:
            audio["APIC"] = APIC(
                encoding=3,
                mime=mime,
                type=3,  # Cover (front)
                desc="Cover",
                data=f.read(),
            )

    audio.save()
    console.print(f"  {episode.episode_id}: ID3 tags embedded — \"{title}\"")


def generate_chapters_json(episode) -> None:
    """Generate Podcasting 2.0 chapters JSON file."""
    review_path = episode.dir / "reviews" / "02_analysis_review.json"
    if not review_path.exists():
        return

    with open(review_path) as f:
        review = json.load(f)

    chapters = review.get("chapters", [])
    if not chapters:
        return

    # Build Podcasting 2.0 chapters format
    chapters_data = {
        "version": "1.2.0",
        "chapters": [],
    }

    for ch in chapters:
        chapter_entry = {
            "title": ch.get("title", ""),
        }
        # Add startTime if we can determine it
        start_seg = ch.get("start_segment")
        if start_seg is not None:
            # Estimate time from segment number
            raw_json = episode.dir / "transcript" / "raw.json"
            if raw_json.exists():
                with open(raw_json) as f:
                    transcript = json.load(f)
                for seg in transcript.get("segments", []):
                    if seg.get("id") == start_seg and seg.get("start") is not None:
                        chapter_entry["startTime"] = round(seg["start"], 1)
                        break

        chapters_data["chapters"].append(chapter_entry)

    # Save
    chapters_path = episode.dir / "metadata" / "chapters.json"
    chapters_path.parent.mkdir(parents=True, exist_ok=True)
    with open(chapters_path, "w", encoding="utf-8") as f:
        json.dump(chapters_data, f, indent=2)

    console.print(f"  {episode.episode_id}: Chapters JSON generated ({len(chapters)} chapters)")


def embed_mp4_metadata(episode, config: dict) -> None:
    """Embed metadata into the master MP4 via FFmpeg."""
    master_mp4 = episode.dir / "video" / "master.mp4"
    if not master_mp4.exists():
        return

    podcast_cfg = config["podcast"]

    # Get title
    review_path = episode.dir / "reviews" / "02_analysis_review.json"
    title = f"EP{episode.episode_number:02d}"
    if review_path.exists():
        with open(review_path) as f:
            review = json.load(f)
        chosen = review.get("chosen_title")
        if chosen:
            title = f"EP{episode.episode_number:02d}: {chosen}"

    # Use ffmpeg to add metadata (output to temp then replace)
    temp_mp4 = master_mp4.with_suffix(".tagged.mp4")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(master_mp4),
        "-c", "copy",
        "-metadata", f"title={title}",
        "-metadata", f"artist={podcast_cfg['name']}",
        "-metadata", f"album={podcast_cfg['name']}",
        "-metadata", f"date={episode.date}",
        "-metadata", f"episode_id={episode.episode_id}",
        "-metadata", f"description={podcast_cfg.get('description', '')}",
        "-movflags", "+faststart",
        str(temp_mp4),
    ], capture_output=True, check=True)

    # Replace original with tagged version
    temp_mp4.replace(master_mp4)
    console.print(f"  {episode.episode_id}: MP4 metadata embedded")


def tag_episode(episode) -> None:
    """Apply all metadata to a single episode."""
    config = get_config()
    embed_id3_tags(episode, config)
    generate_chapters_json(episode)
    embed_mp4_metadata(episode, config)


def tag_all(episode_ids: list[str] | None = None) -> None:
    """Tag all (or specified) episodes."""
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 8: Embedding metadata for {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        tag_episode(episode)

    console.print(f"\n[bold green]Metadata complete![/bold green]\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 8: Embed metadata & tags")
    parser.add_argument("episodes", nargs="*", help="Episode IDs (e.g. ep01 ep02)")
    args = parser.parse_args()

    tag_all(args.episodes or None)

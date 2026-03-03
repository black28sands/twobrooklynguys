"""Step 9: Generate show notes, social posts, and SEO content using Claude API."""

import json
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config
from scripts.helpers.episode_manifest import Manifest
from scripts.helpers.claude_api import generate_show_notes
from scripts.helpers.review_gate import write_review_file

console = Console()


def load_transcript_text(episode) -> str:
    raw_json = episode.dir / "transcript" / "raw.json"
    if raw_json.exists():
        with open(raw_json) as f:
            data = json.load(f)
        lines = []
        for seg in data.get("segments", []):
            speaker = seg.get("speaker", "Unknown")
            text = seg.get("text", "")
            lines.append(f"{speaker}: {text}")
        return "\n".join(lines)
    return ""


def get_episode_title(episode) -> str:
    review_path = episode.dir / "reviews" / "02_analysis_review.json"
    if review_path.exists():
        with open(review_path) as f:
            review = json.load(f)
        return review.get("chosen_title") or (review.get("title_options", [""])[0])
    return ""


def generate_episode_content(episode) -> None:
    """Generate all written content for a single episode."""
    metadata_dir = episode.dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    show_notes_path = metadata_dir / "show_notes.md"
    social_posts_path = metadata_dir / "social_posts.json"

    if show_notes_path.exists() and social_posts_path.exists():
        console.print(f"  {episode.episode_id}: Content already generated, skipping")
        return

    # Load analysis
    analysis_path = episode.dir / "analysis" / "content_analysis.json"
    if not analysis_path.exists():
        console.print(f"  [red]{episode.episode_id}: No analysis found. Run Step 4 first.[/red]")
        return

    with open(analysis_path) as f:
        analysis = json.load(f)

    transcript_text = load_transcript_text(episode)
    title = get_episode_title(episode)

    console.print(f"  {episode.episode_id}: Generating show notes and social content...")

    try:
        content = generate_show_notes(
            transcript_text=transcript_text,
            analysis=analysis,
            episode_id=episode.episode_id.upper(),
            episode_title=title,
        )
    except Exception as e:
        console.print(f"  [red]{episode.episode_id}: Content generation failed: {e}[/red]")
        return

    # Save show notes
    show_notes = content.get("show_notes_md", "")
    with open(show_notes_path, "w", encoding="utf-8") as f:
        f.write(show_notes)

    # Save social posts
    social = content.get("social_posts", {})
    with open(social_posts_path, "w", encoding="utf-8") as f:
        json.dump(social, f, indent=2, ensure_ascii=False)

    # Save episode description
    desc_path = metadata_dir / "episode_description.txt"
    desc = content.get("episode_description", "")
    with open(desc_path, "w", encoding="utf-8") as f:
        f.write(desc)

    # Save SEO metadata
    seo_path = metadata_dir / "seo.json"
    seo = content.get("seo", {})
    with open(seo_path, "w", encoding="utf-8") as f:
        json.dump(seo, f, indent=2, ensure_ascii=False)

    console.print(f"    Show notes, social posts, description, SEO metadata saved")

    # Generate Review Gate 4
    generate_review_gate_4(episode, title, content)


def generate_review_gate_4(episode, title: str, content: dict) -> None:
    """Generate the pre-publish review file."""
    review_path = episode.dir / "reviews" / "04_publish_review.md"

    # Check file sizes
    mp3_path = episode.dir / "audio" / "master.mp3"
    mp4_path = episode.dir / "video" / "master.mp4"
    mp3_size = f"{mp3_path.stat().st_size / 1024 / 1024:.1f} MB" if mp3_path.exists() else "N/A"
    mp4_size = f"{mp4_path.stat().st_size / 1024 / 1024:.1f} MB" if mp4_path.exists() else "N/A"

    # Get LUFS measurement
    lufs_str = "not measured"

    review_content = f"""# Pre-Publish Review - {episode.episode_id.upper()}: "{title}"

## Status: PENDING
<!-- Change to: APPROVED when ready to publish -->

## Metadata Check
- [ ] Episode title: "{title}"
- [ ] Description reads well and is accurate
- [ ] Cover art embedded in MP3
- [ ] Chapter markers present
- [ ] ID3 tags complete

## Content Check
- [ ] Show notes are accurate (metadata/show_notes.md)
- [ ] Social posts are appropriate (metadata/social_posts.json)
- [ ] Episode description is compelling (metadata/episode_description.txt)
- [ ] Transcript is readable

## Technical Check
- [ ] MP3 file size: {mp3_size}
- [ ] MP4 file size: {mp4_size}
- [ ] Audio loudness: {lufs_str}

## Social Posts Preview
"""

    social = content.get("social_posts", {})
    for platform, post in social.items():
        review_content += f"\n### {platform.title()}\n{post}\n"

    review_content += """
## Publish Schedule
- Planned publish date: [ENTER DATE]
- Social posts scheduled: [ ]

## Verdict: PENDING
<!-- Change to APPROVED to publish -->
"""

    write_review_file(review_path, review_content)
    console.print(f"    Review file: reviews/04_publish_review.md (Status: PENDING)")


def generate_all(episode_ids: list[str] | None = None) -> None:
    """Generate content for all (or specified) episodes."""
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 9: Generating show notes for {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        generate_episode_content(episode)

    console.print(f"\n[bold green]Content generation complete![/bold green]")
    console.print(f"\n[bold]Next step:[/bold] Review 04_publish_review.md, then run Step 10 to publish.\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 9: Generate show notes & social content")
    parser.add_argument("episodes", nargs="*", help="Episode IDs (e.g. ep01 ep02)")
    args = parser.parse_args()

    generate_all(args.episodes or None)

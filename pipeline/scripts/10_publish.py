"""Step 10: Publish — copy to website, generate RSS feed, build and deploy."""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config
from scripts.helpers.episode_manifest import Manifest
from scripts.helpers.review_gate import check_gate

console = Console()


def get_episode_title(episode) -> str:
    review_path = episode.dir / "reviews" / "02_analysis_review.json"
    if review_path.exists():
        with open(review_path) as f:
            review = json.load(f)
        return review.get("chosen_title") or (review.get("title_options", ["Untitled"])[0])
    return "Untitled"


def copy_to_website(episode, config: dict) -> dict:
    """Copy master files to the website public directory. Returns file info."""
    website_root = Path(config["paths"]["website_root"])
    public_audio = website_root / "public" / "audio"
    public_video = website_root / "public" / "video"
    public_audio.mkdir(parents=True, exist_ok=True)
    public_video.mkdir(parents=True, exist_ok=True)

    info = {}

    # Copy MP3
    master_mp3 = episode.dir / "audio" / "master.mp3"
    if master_mp3.exists():
        dest_mp3 = public_audio / f"{episode.episode_id}.mp3"
        shutil.copy2(master_mp3, dest_mp3)
        info["audio_url"] = f"/audio/{episode.episode_id}.mp3"
        info["audio_size"] = master_mp3.stat().st_size
        console.print(f"    Copied MP3 -> {dest_mp3}")

    # Copy MP4
    master_mp4 = episode.dir / "video" / "master.mp4"
    if master_mp4.exists():
        dest_mp4 = public_video / f"{episode.episode_id}.mp4"
        shutil.copy2(master_mp4, dest_mp4)
        info["video_url"] = f"/video/{episode.episode_id}.mp4"
        info["video_size"] = master_mp4.stat().st_size
        console.print(f"    Copied MP4 -> {dest_mp4}")

    return info


def generate_episode_markdown(episode, config: dict, file_info: dict) -> None:
    """Generate the Astro content collection Markdown for an episode."""
    website_root = Path(config["paths"]["website_root"])
    content_dir = website_root / "src" / "content" / "episodes"
    content_dir.mkdir(parents=True, exist_ok=True)

    title = get_episode_title(episode)

    # Load show notes
    show_notes_path = episode.dir / "metadata" / "show_notes.md"
    show_notes = ""
    if show_notes_path.exists():
        show_notes = show_notes_path.read_text(encoding="utf-8")

    # Load description
    desc_path = episode.dir / "metadata" / "episode_description.txt"
    description = ""
    if desc_path.exists():
        description = desc_path.read_text(encoding="utf-8")

    # Load chapters
    chapters_path = episode.dir / "metadata" / "chapters.json"
    chapters_yaml = ""
    if chapters_path.exists():
        with open(chapters_path) as f:
            ch_data = json.load(f)
        chapters = ch_data.get("chapters", [])
        if chapters:
            chapters_yaml = "chapters:\n"
            for ch in chapters:
                t = ch.get("startTime", 0)
                minutes = int(t // 60)
                seconds = int(t % 60)
                chapters_yaml += f'  - title: "{ch.get("title", "")}"\n'
                chapters_yaml += f'    start: "{minutes:02d}:{seconds:02d}"\n'

    # Duration
    duration_sec = episode.duration_seconds
    duration_str = f"{int(duration_sec // 60)}:{int(duration_sec % 60):02d}"

    # Load SEO
    seo_path = episode.dir / "metadata" / "seo.json"
    keywords = []
    if seo_path.exists():
        with open(seo_path) as f:
            seo = json.load(f)
        keywords = seo.get("keywords", [])

    # Build frontmatter
    # Escape quotes in title and description for YAML
    safe_title = title.replace('"', '\\"')
    safe_desc = description.replace('"', '\\"')[:300]

    md_content = f"""---
title: "{safe_title}"
episode: {episode.episode_number}
date: "{episode.date}"
duration: "{duration_str}"
audioUrl: "{file_info.get('audio_url', '')}"
videoUrl: "{file_info.get('video_url', '')}"
description: "{safe_desc}"
{chapters_yaml}tags: {json.dumps(keywords)}
---

{show_notes}
"""

    output_path = content_dir / f"{episode.episode_id}.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    console.print(f"    Generated episode page: {output_path.name}")


def generate_rss_feed(manifest: Manifest, config: dict) -> None:
    """Generate the podcast RSS feed."""
    from feedgen.feed import FeedGenerator

    podcast_cfg = config["podcast"]
    website_url = podcast_cfg.get("website", "https://twobrooklynguys.com")

    fg = FeedGenerator()
    fg.load_extension("podcast")

    # Channel metadata
    fg.title(podcast_cfg["name"])
    fg.link(href=website_url, rel="alternate")
    fg.description(podcast_cfg.get("description", "Two Brooklyn Guys Podcast"))
    fg.language(podcast_cfg.get("language", "en"))
    fg.copyright(f"© {datetime.now().year} {podcast_cfg['name']}")

    # Podcast-specific
    fg.podcast.itunes_author(podcast_cfg.get("author", podcast_cfg["name"]))
    fg.podcast.itunes_category(podcast_cfg.get("category", "Technology"))
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_owner(
        name=podcast_cfg.get("author", ""),
        email=podcast_cfg.get("email", ""),
    )

    # Cover art
    artwork_url = f"{website_url}/artwork/cover.png"
    fg.podcast.itunes_image(artwork_url)
    fg.image(url=artwork_url, title=podcast_cfg["name"])

    # Add episodes (newest first)
    for episode in sorted(manifest.episodes, key=lambda e: e.date, reverse=True):
        # Only include published episodes (Gate 4 approved)
        if not check_gate(episode.dir, 4):
            continue

        title = get_episode_title(episode)
        full_title = f"EP{episode.episode_number:02d}: {title}"

        fe = fg.add_entry()
        fe.id(f"{website_url}/episodes/{episode.episode_id}/")
        fe.title(full_title)
        fe.link(href=f"{website_url}/episodes/{episode.episode_id}/")
        fe.published(datetime.strptime(episode.date, "%Y-%m-%d").replace(tzinfo=timezone.utc))

        # Load description
        desc_path = episode.dir / "metadata" / "episode_description.txt"
        if desc_path.exists():
            fe.description(desc_path.read_text(encoding="utf-8"))

        # Audio enclosure
        mp3_path = episode.dir / "audio" / "master.mp3"
        if mp3_path.exists():
            audio_url = f"{website_url}/audio/{episode.episode_id}.mp3"
            fe.enclosure(audio_url, str(mp3_path.stat().st_size), "audio/mpeg")

        # Duration
        if episode.duration_seconds:
            minutes = int(episode.duration_seconds // 60)
            seconds = int(episode.duration_seconds % 60)
            fe.podcast.itunes_duration(f"{minutes}:{seconds:02d}")

        fe.podcast.itunes_episode(episode.episode_number)

    # Write RSS
    website_root = Path(config["paths"]["website_root"])
    rss_dir = website_root / "public"
    rss_dir.mkdir(parents=True, exist_ok=True)
    rss_path = rss_dir / "feed.xml"
    fg.rss_file(str(rss_path), pretty=True)
    console.print(f"  RSS feed generated: {rss_path}")


def publish_episode(episode, config: dict) -> None:
    """Publish a single episode to the website."""
    # Check Gate 4
    if not check_gate(episode.dir, 4):
        console.print(
            f"  [yellow]{episode.episode_id}: Gate 4 (pre-publish) not approved. "
            f"Review 04_publish_review.md first.[/yellow]"
        )
        return

    console.print(f"  {episode.episode_id}: Publishing...")
    file_info = copy_to_website(episode, config)
    generate_episode_markdown(episode, config, file_info)
    episode.status = "published"


def publish_all(episode_ids: list[str] | None = None, validate_only: bool = False) -> None:
    """Publish all (or specified) episodes and regenerate RSS."""
    config = get_config()
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 10: Publishing {len(episodes)} episodes...[/bold]\n")

    if not validate_only:
        for episode in episodes:
            publish_episode(episode, config)

    # Regenerate RSS feed (includes all published episodes)
    generate_rss_feed(manifest, config)

    # Save updated manifest
    manifest.save()

    console.print(f"\n[bold green]Publishing complete![/bold green]")
    console.print(f"\nTo deploy: push to GitHub and Vercel will auto-deploy.")
    console.print(f"RSS feed: {config['podcast']['website']}/feed.xml\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 10: Publish to website")
    parser.add_argument("episodes", nargs="*", help="Episode IDs (e.g. ep01 ep02)")
    parser.add_argument("--validate-only", action="store_true", help="Only validate and regenerate RSS")
    args = parser.parse_args()

    publish_all(args.episodes or None, validate_only=args.validate_only)

"""Step 11: Generate social clips — audiograms, short-form vertical video, quote images."""

import json
import subprocess
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config, assets_root
from scripts.helpers.episode_manifest import Manifest

console = Console()


def generate_audiogram(
    audio_path: Path,
    output_path: Path,
    start: float,
    duration: float,
    resolution: str = "1080x1080",
    bg_color: str = "0x1a1a2e",
    wave_color: str = "white",
) -> None:
    """Generate an audiogram (waveform video) from an audio clip."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution.split("x")

    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", str(audio_path),
        "-filter_complex",
        f"color=c={bg_color}:s={resolution}:d={duration}[bg];"
        f"[0:a]showwaves=s={w}x{int(int(h) * 0.3)}:mode=cline:colors={wave_color}:rate=30[waves];"
        f"[bg][waves]overlay=(W-w)/2:(H-h)/2[out]",
        "-map", "[out]",
        "-map", "0:a",
        "-c:v", "libx264", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(output_path),
    ], capture_output=True, check=True)


def generate_vertical_clip(
    video_path: Path,
    output_path: Path,
    start: float,
    duration: float,
) -> None:
    """Extract and crop a video clip to 9:16 vertical format."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Crop from center of 16:9 to 9:16
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", str(video_path),
        "-vf", "crop=ih*9/16:ih,scale=1080:1920",
        "-c:v", "libx264", "-crf", "23", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ], capture_output=True, check=True)


def generate_quote_image(
    quote: str,
    speaker: str,
    output_path: Path,
    resolution: tuple[int, int] = (1080, 1080),
    bg_color: tuple[int, int, int] = (26, 26, 46),
    text_color: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Generate a branded quote card image."""
    from PIL import Image, ImageDraw, ImageFont

    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution

    img = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(img)

    # Try to use a nice font, fall back to default
    try:
        quote_font = ImageFont.truetype("arial.ttf", 42)
        speaker_font = ImageFont.truetype("arial.ttf", 28)
        brand_font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        quote_font = ImageFont.load_default()
        speaker_font = quote_font
        brand_font = quote_font

    # Draw quote text (wrapped)
    margin = 80
    max_width = w - (margin * 2)
    quote_text = f'"{quote}"'

    # Simple word wrap
    words = quote_text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=quote_font)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    # Calculate vertical position (center the text block)
    line_height = 55
    total_text_height = len(lines) * line_height + 80  # +80 for speaker and brand
    y_start = (h - total_text_height) // 2

    # Draw each line
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=quote_font)
        x = (w - (bbox[2] - bbox[0])) // 2
        draw.text((x, y_start + i * line_height), line, fill=text_color, font=quote_font)

    # Draw speaker attribution
    speaker_text = f"— {speaker}"
    bbox = draw.textbbox((0, 0), speaker_text, font=speaker_font)
    x = (w - (bbox[2] - bbox[0])) // 2
    y_speaker = y_start + len(lines) * line_height + 30
    draw.text((x, y_speaker), speaker_text, fill=(180, 180, 200), font=speaker_font)

    # Draw brand
    brand_text = "Two Brooklyn Guys Podcast"
    bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
    x = (w - (bbox[2] - bbox[0])) // 2
    draw.text((x, h - 60), brand_text, fill=(120, 120, 140), font=brand_font)

    img.save(str(output_path), quality=95)


def generate_episode_clips(episode) -> None:
    """Generate all social clips for a single episode."""
    social_dir = episode.dir / "social"
    social_dir.mkdir(parents=True, exist_ok=True)

    # Load approved review for clip selections
    review_path = episode.dir / "reviews" / "02_analysis_review.json"
    if not review_path.exists():
        console.print(f"  [yellow]{episode.episode_id}: No review file, skipping clips[/yellow]")
        return

    with open(review_path) as f:
        review = json.load(f)

    # Load analysis for highlight details
    analysis_path = episode.dir / "analysis" / "content_analysis.json"
    if not analysis_path.exists():
        console.print(f"  [yellow]{episode.episode_id}: No analysis, skipping clips[/yellow]")
        return

    with open(analysis_path) as f:
        analysis = json.load(f)

    # Get approved social clips
    approved_clips = [
        c for c in review.get("social_clips", [])
        if c.get("approved", False)
    ]

    if not approved_clips:
        console.print(f"  {episode.episode_id}: No approved social clips")
        return

    highlights_map = {h["id"]: h for h in analysis.get("highlights", [])}
    raw_wav = episode.dir / "audio" / "raw.wav"
    source_mp4 = episode.dir / "source" / f"{episode.episode_id}.mp4"

    console.print(f"  {episode.episode_id}: Generating {len(approved_clips)} social clips...")

    # Load transcript for timestamp lookup
    transcript_segments = []
    raw_json = episode.dir / "transcript" / "raw.json"
    if raw_json.exists():
        with open(raw_json) as f:
            transcript_segments = json.load(f).get("segments", [])

    for i, clip in enumerate(approved_clips):
        h_id = clip["highlight_id"]
        highlight = highlights_map.get(h_id)
        if not highlight:
            continue

        # Determine clip time range from segment IDs
        seg_ids = highlight.get("segment_ids", [])
        if not seg_ids:
            continue

        start_time = None
        end_time = None
        for seg in transcript_segments:
            if seg.get("id") in seg_ids:
                if start_time is None and seg.get("start") is not None:
                    start_time = seg["start"]
                if seg.get("end") is not None:
                    end_time = seg["end"]

        if start_time is None or end_time is None:
            console.print(f"    [yellow]Clip {h_id}: No timestamps, skipping[/yellow]")
            continue

        duration = min(end_time - start_time, 90)  # Max 90s

        # 1. Audiogram (1:1 square)
        try:
            audiogram_path = social_dir / f"audiogram_{i + 1:02d}.mp4"
            if not audiogram_path.exists() and raw_wav.exists():
                console.print(f"    Generating audiogram for {h_id}...")
                generate_audiogram(raw_wav, audiogram_path, start_time, duration)
        except Exception as e:
            console.print(f"    [yellow]Audiogram failed: {e}[/yellow]")

        # 2. Vertical video clip (9:16)
        try:
            vertical_path = social_dir / f"clip_{i + 1:02d}_vertical.mp4"
            if not vertical_path.exists() and source_mp4.exists():
                console.print(f"    Generating vertical clip for {h_id}...")
                generate_vertical_clip(source_mp4, vertical_path, start_time, duration)
        except Exception as e:
            console.print(f"    [yellow]Vertical clip failed: {e}[/yellow]")

        # 3. Quote image
        try:
            quote = highlight.get("quote", "")
            speaker = highlight.get("speaker", "")
            if quote:
                quote_path = social_dir / f"quote_{i + 1:02d}.png"
                if not quote_path.exists():
                    generate_quote_image(quote, speaker, quote_path)
                    console.print(f"    Generated quote card for {h_id}")
        except Exception as e:
            console.print(f"    [yellow]Quote image failed: {e}[/yellow]")


def generate_all_clips(episode_ids: list[str] | None = None) -> None:
    """Generate social clips for all (or specified) episodes."""
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 11: Generating social clips for {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        generate_episode_clips(episode)

    console.print(f"\n[bold green]Social clips complete![/bold green]\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 11: Generate social clips")
    parser.add_argument("episodes", nargs="*", help="Episode IDs (e.g. ep01 ep02)")
    args = parser.parse_args()

    generate_all_clips(args.episodes or None)

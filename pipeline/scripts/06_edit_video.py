"""Step 6: Assemble curated video episode from approved segments."""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config, assets_root
from scripts.helpers.episode_manifest import Manifest
from scripts.helpers.review_gate import check_gate
# Import ffmpeg_wrapper to auto-discover FFmpeg on Windows
import scripts.helpers.ffmpeg_wrapper as _fw

console = Console()


def load_approved_review(episode) -> dict | None:
    review_path = episode.dir / "reviews" / "02_analysis_review.json"
    if not review_path.exists():
        return None
    with open(review_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_transcript_segments(episode) -> list[dict]:
    raw_json = episode.dir / "transcript" / "raw.json"
    if not raw_json.exists():
        return []
    with open(raw_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("segments", [])


def cut_video_segment(
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> None:
    """Cut a video segment using FFmpeg with re-encoding for precise cuts."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    start_str = f"{start_seconds:.3f}"
    duration_str = f"{end_seconds - start_seconds:.3f}"
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", start_str,
        "-i", str(input_path),
        "-t", duration_str,
        "-c:v", "libx264", "-crf", "23", "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ], capture_output=True, check=True)


def concatenate_video(segment_paths: list[Path], output_path: Path) -> None:
    """Concatenate video segments using FFmpeg concat demuxer."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for path in segment_paths:
            safe_path = str(path).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")
        concat_file = f.name

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ], capture_output=True, check=True)
    finally:
        Path(concat_file).unlink(missing_ok=True)


def parse_embedded_timestamps(segments: list[dict], total_duration: float) -> list[dict]:
    """Parse @MM:SS timestamps from speaker-label segments."""
    if not segments:
        return segments
    if any(s.get("start") is not None for s in segments):
        return segments

    ts_pattern = re.compile(r"@(\d+):(\d+)")
    timestamps: list[tuple[int, float]] = []
    for i, seg in enumerate(segments):
        m = ts_pattern.search(seg.get("text", ""))
        if m:
            seconds = int(m.group(1)) * 60 + int(m.group(2))
            timestamps.append((i, float(seconds)))

    if not timestamps:
        total_chars = sum(len(s.get("text", "")) for s in segments)
        if total_chars == 0:
            return segments
        cur = 0.0
        for seg in segments:
            seg_dur = (len(seg.get("text", "")) / total_chars) * total_duration
            seg["start"] = cur
            seg["end"] = cur + seg_dur
            cur += seg_dur
        return segments

    for idx in range(len(timestamps)):
        ts_idx, ts_sec = timestamps[idx]
        next_sec = timestamps[idx + 1][1] if idx + 1 < len(timestamps) else total_duration
        next_ts_idx = timestamps[idx + 1][0] if idx + 1 < len(timestamps) else len(segments)
        span = max(next_ts_idx - ts_idx, 1)
        seg_dur = (next_sec - ts_sec) / span
        for j in range(ts_idx, next_ts_idx):
            if j < len(segments):
                segments[j]["start"] = ts_sec + (j - ts_idx) * seg_dur
                segments[j]["end"] = ts_sec + (j - ts_idx + 1) * seg_dur

    if timestamps[0][0] > 0:
        first_time = timestamps[0][1]
        pre_count = timestamps[0][0]
        seg_dur = first_time / pre_count if pre_count > 0 else 0
        for j in range(pre_count):
            segments[j]["start"] = j * seg_dur
            segments[j]["end"] = (j + 1) * seg_dur

    return segments


def get_topic_time_range(
    topic: dict,
    transcript_segments: list[dict],
) -> tuple[float, float] | None:
    """Get start/end time for a topic from transcript segments."""
    start_seg = topic.get("start_segment")
    end_seg = topic.get("end_segment")

    if start_seg is None or end_seg is None:
        return None

    start_time = None
    end_time = None

    for seg in transcript_segments:
        seg_id = seg.get("id")
        if seg_id == start_seg and seg.get("start") is not None:
            start_time = seg["start"]
        if seg_id == end_seg and seg.get("end") is not None:
            end_time = seg["end"]

    if start_time is not None and end_time is not None:
        return (start_time, end_time)

    return None


def edit_episode_video(episode) -> None:
    """Assemble the curated video for a single episode."""
    video_dir = episode.dir / "video"
    segments_dir = video_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    source_mp4 = episode.dir / "source" / f"{episode.episode_id}.mp4"
    assembled_mp4 = video_dir / "assembled.mp4"

    # Check Gate 2
    if not check_gate(episode.dir, 2):
        console.print(
            f"  [yellow]{episode.episode_id}: Gate 2 (curation review) not approved.[/yellow]"
        )
        return

    if not source_mp4.exists():
        console.print(f"  [red]{episode.episode_id}: No source MP4 found.[/red]")
        return

    if assembled_mp4.exists():
        console.print(f"  {episode.episode_id}: Assembled video already exists, skipping")
        return

    review = load_approved_review(episode)
    if not review:
        console.print(f"  [red]{episode.episode_id}: No review file found.[/red]")
        return

    transcript_segments = load_transcript_segments(episode)

    # Get source video duration and parse timestamps
    try:
        video_duration = _fw.get_duration(source_mp4)
    except Exception:
        video_duration = episode.duration_seconds or 3600.0
    transcript_segments = parse_embedded_timestamps(transcript_segments, video_duration)

    # Determine segments to include
    arc_order = review.get("arc_order", [])
    segments_map = {s["id"]: s for s in review.get("segments", [])}

    kept_segments = []
    for topic_id in arc_order:
        seg = segments_map.get(topic_id)
        if seg and seg.get("action") != "cut":
            kept_segments.append(seg)

    if not kept_segments:
        kept_segments = [s for s in review.get("segments", []) if s.get("action") != "cut"]

    console.print(f"  {episode.episode_id}: Cutting {len(kept_segments)} video segments...")

    segment_files = []

    for i, seg in enumerate(kept_segments):
        topic_id = seg["id"]
        time_range = get_topic_time_range(seg, transcript_segments)

        if time_range is None:
            console.print(f"    [yellow]Segment {topic_id}: No timestamps, skipping[/yellow]")
            continue

        start, end = time_range
        seg_path = segments_dir / f"{i + 1:02d}_{topic_id}.mp4"

        if seg_path.exists():
            console.print(f"    Segment {topic_id}: already cut, reusing")
            segment_files.append(seg_path)
            continue

        console.print(f"    Cutting {topic_id}: {start:.1f}s - {end:.1f}s ({(end - start) / 60:.1f} min)")
        try:
            cut_video_segment(source_mp4, seg_path, start, end)
            segment_files.append(seg_path)
        except subprocess.CalledProcessError as e:
            console.print(f"    [red]Failed to cut {topic_id}: {e}[/red]")
            continue

    if not segment_files:
        console.print(f"  [red]{episode.episode_id}: No video segments to assemble.[/red]")
        return

    # Concatenate
    console.print(f"    Concatenating {len(segment_files)} video segments...")
    concatenate_video(segment_files, assembled_mp4)
    console.print(f"    [green]Assembled: {assembled_mp4}[/green]")


def edit_all(episode_ids: list[str] | None = None) -> None:
    """Edit video for all (or specified) episodes."""
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found. Run Step 1 first.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 6: Editing video for {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        edit_episode_video(episode)

    console.print(f"\n[bold green]Video editing complete![/bold green]\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 6: Edit video")
    parser.add_argument("episodes", nargs="*", help="Episode IDs (e.g. ep01 ep02)")
    args = parser.parse_args()

    edit_all(args.episodes or None)

"""Step 5: Assemble curated audio episode from approved segments."""

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
    """Load the approved Gate 2 review data."""
    review_path = episode.dir / "reviews" / "02_analysis_review.json"
    if not review_path.exists():
        return None
    with open(review_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_transcript_segments(episode) -> list[dict]:
    """Load transcript segments to map segment IDs to timestamps."""
    raw_json = episode.dir / "transcript" / "raw.json"
    if not raw_json.exists():
        return []
    with open(raw_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("segments", [])


def parse_embedded_timestamps(segments: list[dict], total_duration: float) -> list[dict]:
    """Parse @MM:SS timestamps embedded in speaker-label segments and assign
    start/end times to all segments.  The DOCX transcripts alternate between
    timestamp lines (e.g. '@1:51 - Joe Gonzalez') and speech lines."""
    if not segments:
        return segments

    # Already have real timestamps?
    if any(s.get("start") is not None for s in segments):
        return segments

    # First pass: extract timestamps from @MM:SS lines
    ts_pattern = re.compile(r"@(\d+):(\d+)")
    timestamps: list[tuple[int, float]] = []  # (segment_index, seconds)
    for i, seg in enumerate(segments):
        m = ts_pattern.search(seg.get("text", ""))
        if m:
            seconds = int(m.group(1)) * 60 + int(m.group(2))
            timestamps.append((i, float(seconds)))

    if not timestamps:
        # Fallback: distribute evenly by character count
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

    # Second pass: assign start/end to every segment by interpolating
    # between known timestamps
    for idx in range(len(timestamps)):
        ts_idx, ts_sec = timestamps[idx]
        next_sec = timestamps[idx + 1][1] if idx + 1 < len(timestamps) else total_duration
        next_ts_idx = timestamps[idx + 1][0] if idx + 1 < len(timestamps) else len(segments)

        # All segments from ts_idx to next_ts_idx share this time window
        span = next_ts_idx - ts_idx
        if span <= 0:
            span = 1
        seg_dur = (next_sec - ts_sec) / span

        for j in range(ts_idx, next_ts_idx):
            if j < len(segments):
                segments[j]["start"] = ts_sec + (j - ts_idx) * seg_dur
                segments[j]["end"] = ts_sec + (j - ts_idx + 1) * seg_dur

    # Segments before the first timestamp
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
    """Get the start/end time for a topic based on its segment range."""
    start_seg = topic.get("start_segment")
    end_seg = topic.get("end_segment")

    if start_seg is None or end_seg is None:
        return None

    # Find matching transcript segments
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


def cut_audio_segment(
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> None:
    """Cut an audio segment using FFmpeg."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    start_str = f"{start_seconds:.3f}"
    duration_str = f"{end_seconds - start_seconds:.3f}"
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-ss", start_str,
        "-t", duration_str,
        "-acodec", "pcm_s24le",
        str(output_path),
    ], capture_output=True, check=True)


def concatenate_audio(segment_paths: list[Path], output_path: Path) -> None:
    """Concatenate multiple audio files using FFmpeg concat demuxer."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create concat file list
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for path in segment_paths:
            # Use forward slashes and escape single quotes
            safe_path = str(path).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")
        concat_file = f.name

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-acodec", "pcm_s24le",
            str(output_path),
        ], capture_output=True, check=True)
    finally:
        Path(concat_file).unlink(missing_ok=True)


def generate_silence(output_path: Path, duration: float, sample_rate: int = 48000) -> None:
    """Generate a silence WAV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", str(duration),
        "-acodec", "pcm_s24le",
        str(output_path),
    ], capture_output=True, check=True)


def edit_episode_audio(episode) -> None:
    """Assemble the curated audio for a single episode."""
    audio_dir = episode.dir / "audio"
    segments_dir = audio_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    raw_wav = audio_dir / "raw.wav"
    assembled_wav = audio_dir / "assembled.wav"

    # Check Gate 2
    if not check_gate(episode.dir, 2):
        console.print(
            f"  [yellow]{episode.episode_id}: Gate 2 (curation review) not approved. "
            f"Review and approve reviews/02_analysis_review.json first.[/yellow]"
        )
        return

    if not raw_wav.exists():
        console.print(f"  [red]{episode.episode_id}: No raw audio. Run Step 2 first.[/red]")
        return

    if assembled_wav.exists():
        console.print(f"  {episode.episode_id}: Assembled audio already exists, skipping")
        return

    review = load_approved_review(episode)
    if not review:
        console.print(f"  [red]{episode.episode_id}: No review file found.[/red]")
        return

    # Load transcript segments for timestamp mapping
    transcript_segments = load_transcript_segments(episode)
    # Get actual audio duration from file
    try:
        audio_duration = _fw.get_duration(raw_wav)
    except Exception:
        audio_duration = episode.duration_seconds or 3600.0
    transcript_segments = parse_embedded_timestamps(transcript_segments, audio_duration)

    # Determine which segments to keep and in what order
    arc_order = review.get("arc_order", [])
    segments_map = {s["id"]: s for s in review.get("segments", [])}

    # Filter to only "keep" segments
    kept_segments = []
    for topic_id in arc_order:
        seg = segments_map.get(topic_id)
        if seg and seg.get("action") != "cut":
            kept_segments.append(seg)

    if not kept_segments:
        # If no arc order, keep all non-cut segments in original order
        kept_segments = [s for s in review.get("segments", []) if s.get("action") != "cut"]

    console.print(f"  {episode.episode_id}: Assembling {len(kept_segments)} segments...")

    # Cut individual segments
    segment_files = []
    assets = assets_root()

    # 1. Cold open (if specified)
    cold_open_id = review.get("cold_open", {}).get("chosen_highlight_id")
    if cold_open_id:
        # Find the highlight's segment range in the analysis
        analysis_path = episode.dir / "analysis" / "content_analysis.json"
        if analysis_path.exists():
            with open(analysis_path) as f:
                analysis = json.load(f)
            for h in analysis.get("highlights", []):
                if h["id"] == cold_open_id and h.get("segment_ids"):
                    first_seg = h["segment_ids"][0]
                    last_seg = h["segment_ids"][-1]
                    # Find time range
                    for ts in transcript_segments:
                        if ts.get("id") == first_seg and ts.get("start") is not None:
                            start = ts["start"]
                        if ts.get("id") == last_seg and ts.get("end") is not None:
                            end = min(ts["end"], start + 15)  # Max 15s cold open
                    cold_open_path = segments_dir / "00_cold_open.wav"
                    try:
                        cut_audio_segment(raw_wav, cold_open_path, start, end)
                        segment_files.append(cold_open_path)
                    except Exception:
                        console.print(f"    [yellow]Could not extract cold open, skipping[/yellow]")

    # 2. Brief silence
    silence_path = segments_dir / "silence_0.5s.wav"
    generate_silence(silence_path, 0.5)

    if segment_files:
        segment_files.append(silence_path)

    # 3. Intro jingle (if exists)
    intro_path = assets / "audio" / "intro.wav"
    if intro_path.exists():
        segment_files.append(intro_path)
        segment_files.append(silence_path)

    # 4. Main content segments
    for i, seg in enumerate(kept_segments):
        topic_id = seg["id"]
        time_range = get_topic_time_range(seg, transcript_segments)

        if time_range is None:
            console.print(f"    [yellow]Segment {topic_id}: No timestamps, skipping[/yellow]")
            continue

        start, end = time_range
        seg_path = segments_dir / f"{i + 1:02d}_{topic_id}.wav"

        console.print(f"    Cutting segment {topic_id}: {start:.1f}s - {end:.1f}s ({(end - start) / 60:.1f} min)")
        try:
            cut_audio_segment(raw_wav, seg_path, start, end)
            segment_files.append(seg_path)
        except subprocess.CalledProcessError as e:
            console.print(f"    [red]Failed to cut segment {topic_id}: {e}[/red]")
            continue

        # Add transition silence between segments
        if i < len(kept_segments) - 1:
            segment_files.append(silence_path)

    # 5. Outro (if exists)
    outro_path = assets / "audio" / "outro.wav"
    if outro_path.exists():
        segment_files.append(silence_path)
        segment_files.append(outro_path)

    if not segment_files:
        console.print(f"  [red]{episode.episode_id}: No segments to assemble.[/red]")
        return

    # Concatenate all segments
    console.print(f"    Concatenating {len(segment_files)} parts...")
    concatenate_audio(segment_files, assembled_wav)
    console.print(f"    [green]Assembled: {assembled_wav}[/green]")

    # Generate Review Gate 3
    generate_review_gate_3(episode, review, kept_segments, assembled_wav)


def write_review_file(path: Path, content: str) -> None:
    """Write a review gate file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def generate_review_gate_3(episode, review: dict, kept_segments: list, assembled_path: Path) -> None:
    """Generate the post-edit review file."""
    review_path = episode.dir / "reviews" / "03_edit_review.md"

    # Get assembled duration
    try:
        from scripts.helpers.ffmpeg_wrapper import get_duration
        duration = get_duration(assembled_path)
        duration_str = f"{int(duration // 60)}:{int(duration % 60):02d}"
    except Exception:
        duration_str = "unknown"

    title = review.get("chosen_title") or review.get("title_options", ["Untitled"])[0]

    content = f"""# Edit Review - {episode.episode_id.upper()}: "{title}"

## Instructions
Listen to the assembled audio at: {assembled_path}
Check each item below and note any issues.

## Status: PENDING
<!-- Change to: APPROVED when ready to proceed, or NEEDS_REWORK with notes -->

## Audio Review
- [ ] Cold open is compelling and hooks the listener
- [ ] Intro plays correctly with proper levels
- [ ] Segment transitions are smooth (no jarring cuts)
- [ ] Pacing feels good (not too rushed, no dead air)
- [ ] Speaker levels are balanced
- [ ] No audio artifacts, clicks, or pops
- [ ] Outro and CTA are clear
- [ ] Total runtime is appropriate: {duration_str}

## Segments Included
"""
    for seg in kept_segments:
        content += f"- {seg['id']}: {seg.get('title', 'Untitled')} — {seg.get('action', 'keep')}\n"

    content += """
## Adjustments Needed
<!-- Describe any re-edits needed. Be specific with timestamps. -->

## Verdict: PENDING
<!-- Change to APPROVED to proceed, or NEEDS_REWORK with notes above -->
"""

    write_review_file(review_path, content)
    console.print(f"    Review file: reviews/03_edit_review.md (Status: PENDING)")


def edit_all(episode_ids: list[str] | None = None) -> None:
    """Edit audio for all (or specified) episodes."""
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found. Run Step 1 first.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 5: Editing audio for {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        edit_episode_audio(episode)

    console.print(f"\n[bold green]Audio editing complete![/bold green]")
    console.print(f"\n[bold]Next step:[/bold] Listen to assembled episodes and review 03_edit_review.md\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 5: Edit audio")
    parser.add_argument("episodes", nargs="*", help="Episode IDs (e.g. ep01 ep02)")
    args = parser.parse_args()

    edit_all(args.episodes or None)

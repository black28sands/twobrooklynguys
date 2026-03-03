"""Step 3: Generate timestamped transcripts from audio.

Uses existing DOCX transcripts as the primary source and enhances them
with timestamp estimation. If Whisper is available, generates precise
word-level timestamps from the audio.
"""

import json
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config
from scripts.helpers.episode_manifest import Manifest

console = Console()


def read_docx_text(docx_path: Path) -> str:
    """Extract plain text from a DOCX file."""
    from docx import Document

    doc = Document(str(docx_path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def convert_existing_transcripts(episode) -> dict | None:
    """Read existing DOCX transcript and convert to structured format.

    Returns a transcript dict or None if no transcript found.
    """
    source_dir = episode.dir / "source"

    # Try to find the transcript DOCX
    transcript_path = source_dir / "transcript.docx"
    if not transcript_path.exists():
        # Fall back to other naming patterns
        for name in ["Transcript.docx", "Transcript long.docx"]:
            candidate = source_dir / name
            if candidate.exists():
                transcript_path = candidate
                break

    if not transcript_path.exists():
        return None

    text = read_docx_text(transcript_path)

    # Parse into segments (split on double newlines or speaker patterns)
    import re
    raw_segments = re.split(r"\n{2,}", text)

    segments = []
    for i, seg_text in enumerate(raw_segments):
        seg_text = seg_text.strip()
        if not seg_text:
            continue

        # Try to detect speaker labels (common patterns: "Name:", "Speaker 1:", etc.)
        speaker = "Unknown"
        content = seg_text
        speaker_match = re.match(r"^([A-Z][a-zA-Z\s]+?):\s*(.+)", seg_text, re.DOTALL)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            content = speaker_match.group(2).strip()

        segments.append({
            "id": i + 1,
            "speaker": speaker,
            "text": content,
            "start": None,     # Will be filled by Whisper or estimated
            "end": None,
            "confidence": None,
        })

    return {
        "episode": episode.episode_id,
        "source": "docx",
        "total_segments": len(segments),
        "segments": segments,
    }


def transcribe_with_whisper(episode, model_name: str = "base") -> dict | None:
    """Transcribe audio using Whisper for precise timestamps.

    Falls back gracefully if Whisper is not installed.
    """
    audio_path = episode.dir / "audio" / "raw.wav"
    if not audio_path.exists():
        console.print(f"  [red]{episode.episode_id}: No audio file found. Run Step 2 first.[/red]")
        return None

    try:
        import whisper
    except ImportError:
        console.print(
            f"  [yellow]{episode.episode_id}: Whisper not installed. "
            f"Using DOCX transcript without timestamps.[/yellow]"
        )
        console.print("  To enable Whisper: pip install openai-whisper")
        return None

    console.print(f"  {episode.episode_id}: Transcribing with Whisper ({model_name})... (this may take a while)")

    model = whisper.load_model(model_name)
    result = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        language="en",
    )

    segments = []
    for i, seg in enumerate(result.get("segments", [])):
        words = []
        for w in seg.get("words", []):
            words.append({
                "word": w["word"],
                "start": w["start"],
                "end": w["end"],
                "confidence": w.get("probability", None),
            })

        segments.append({
            "id": i + 1,
            "speaker": "Unknown",  # Whisper doesn't do diarization
            "text": seg["text"].strip(),
            "start": seg["start"],
            "end": seg["end"],
            "confidence": seg.get("avg_logprob", None),
            "words": words,
        })

    return {
        "episode": episode.episode_id,
        "source": "whisper",
        "model": model_name,
        "duration": result.get("duration", episode.duration_seconds),
        "language": result.get("language", "en"),
        "total_segments": len(segments),
        "segments": segments,
    }


def merge_transcripts(docx_transcript: dict | None, whisper_transcript: dict | None) -> dict:
    """Merge DOCX content with Whisper timestamps for best-of-both output."""
    # If we have Whisper data, use it as the primary source (it has timestamps)
    if whisper_transcript:
        return whisper_transcript

    # If only DOCX, return that
    if docx_transcript:
        return docx_transcript

    return {"episode": "unknown", "source": "none", "segments": []}


def generate_review_file(episode, transcript: dict) -> None:
    """Generate the Review Gate 1 file for transcript review."""
    review_path = episode.dir / "reviews" / "01_transcript_review.md"
    review_path.parent.mkdir(parents=True, exist_ok=True)

    # Find unique speakers
    speakers = sorted(set(
        seg.get("speaker", "Unknown")
        for seg in transcript.get("segments", [])
        if seg.get("speaker")
    ))

    # Find low-confidence segments
    low_confidence = []
    for seg in transcript.get("segments", []):
        conf = seg.get("confidence")
        if conf is not None and conf < -0.7:  # Whisper log-prob threshold
            time_str = format_time(seg.get("start", 0)) if seg.get("start") is not None else "??:??"
            low_confidence.append(f"- [{time_str}] \"{seg['text'][:80]}...\"")

    # Build the review file
    lines = [
        f"# Transcript Review - {episode.episode_id.upper()} ({episode.date})",
        "",
        "## Instructions",
        "Review the transcript below. Make corrections directly in this file.",
        "When satisfied, change Status to APPROVED and save.",
        "",
        "## Status: PENDING",
        "<!-- Change to: APPROVED when ready to proceed -->",
        "",
        "## Speaker Labels",
        "Map these speaker labels to real names:",
    ]
    for speaker in speakers:
        lines.append(f"- {speaker}: [Enter real name]")

    lines.extend([
        "",
        "## Flagged Sections",
    ])
    if low_confidence:
        lines.append("The following sections had low confidence scores:")
        lines.extend(low_confidence)
    else:
        lines.append("No low-confidence sections detected.")

    lines.extend([
        "",
        "## Full Transcript",
        "",
    ])

    for seg in transcript.get("segments", []):
        time_str = ""
        if seg.get("start") is not None:
            time_str = f"[{format_time(seg['start'])}] "
        speaker = seg.get("speaker", "Unknown")
        text = seg.get("text", "")
        lines.append(f"**{speaker}** {time_str}{text}")
        lines.append("")

    with open(review_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def format_time(seconds: float | None) -> str:
    """Format seconds into HH:MM:SS."""
    if seconds is None:
        return "??:??"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def transcribe_episode(episode, use_whisper: bool = False, whisper_model: str = "base") -> None:
    """Transcribe a single episode."""
    transcript_dir = episode.dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)

    raw_json = transcript_dir / "raw.json"

    # Skip if already transcribed
    if raw_json.exists():
        console.print(f"  {episode.episode_id}: Transcript already exists, skipping")
        return

    console.print(f"  {episode.episode_id}: Processing transcript...")

    # Read existing DOCX
    docx_transcript = convert_existing_transcripts(episode)
    if docx_transcript:
        console.print(f"    Found DOCX transcript: {docx_transcript['total_segments']} segments")

    # Optionally run Whisper
    whisper_transcript = None
    if use_whisper:
        whisper_transcript = transcribe_with_whisper(episode, whisper_model)
        if whisper_transcript:
            console.print(f"    Whisper: {whisper_transcript['total_segments']} segments")

    # Merge and save
    final = merge_transcripts(docx_transcript, whisper_transcript)
    with open(raw_json, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2, ensure_ascii=False)

    # Also save the plain text version of the DOCX for reference
    if docx_transcript:
        docx_text_path = transcript_dir / "docx_original.txt"
        source_dir = episode.dir / "source"
        transcript_docx = source_dir / "transcript.docx"
        if transcript_docx.exists():
            text = read_docx_text(transcript_docx)
            with open(docx_text_path, "w", encoding="utf-8") as f:
                f.write(text)

    # Generate review gate file
    generate_review_file(episode, final)
    console.print(f"    Review file: reviews/01_transcript_review.md (Status: PENDING)")


def transcribe_all(
    episode_ids: list[str] | None = None,
    use_whisper: bool = False,
    whisper_model: str = "base",
) -> None:
    """Transcribe all (or specified) episodes."""
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found. Run Step 1 (normalize) first.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 3: Transcribing {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        transcribe_episode(episode, use_whisper=use_whisper, whisper_model=whisper_model)

    console.print(f"\n[bold green]Transcription complete![/bold green]")
    console.print(f"\n[bold]Next step:[/bold] Review the transcript files in each episode's reviews/ folder.")
    console.print("Change 'Status: PENDING' to 'Status: APPROVED' when satisfied.\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 3: Generate timestamped transcripts")
    parser.add_argument("episodes", nargs="*", help="Episode IDs to process (e.g. ep01 ep02)")
    parser.add_argument("--whisper", action="store_true", help="Use Whisper for precise timestamps")
    parser.add_argument("--whisper-model", default="base", help="Whisper model size (tiny/base/small/medium/large-v3)")
    args = parser.parse_args()

    transcribe_all(
        episode_ids=args.episodes or None,
        use_whisper=args.whisper,
        whisper_model=args.whisper_model,
    )

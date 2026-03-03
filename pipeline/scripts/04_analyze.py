"""Step 4: AI-powered content analysis — identifies best segments, suggests episode arc."""

import json
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config
from scripts.helpers.episode_manifest import Manifest
from scripts.helpers.review_gate import check_gate, write_review_file
from scripts.helpers.claude_api import analyze_transcript

console = Console()


def load_transcript_text(episode) -> str:
    """Load the transcript as plain text for analysis."""
    # Try the raw JSON transcript first
    raw_json = episode.dir / "transcript" / "raw.json"
    if raw_json.exists():
        with open(raw_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        lines = []
        for seg in data.get("segments", []):
            speaker = seg.get("speaker", "Unknown")
            text = seg.get("text", "")
            time_str = ""
            if seg.get("start") is not None:
                minutes = int(seg["start"] // 60)
                seconds = int(seg["start"] % 60)
                time_str = f"[{minutes:02d}:{seconds:02d}] "
            lines.append(f"[Segment {seg.get('id', '?')}] {time_str}{speaker}: {text}")
        return "\n".join(lines)

    # Fall back to DOCX text
    docx_text = episode.dir / "transcript" / "docx_original.txt"
    if docx_text.exists():
        return docx_text.read_text(encoding="utf-8")

    return ""


def load_summary_text(episode) -> str:
    """Load the summary text if available."""
    source_dir = episode.dir / "source"
    summary_docx = source_dir / "summary.docx"
    if summary_docx.exists():
        from docx import Document
        doc = Document(str(summary_docx))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return ""


def generate_review_gate_2(episode, analysis: dict) -> None:
    """Generate the Review Gate 2 file — curation review JSON."""
    review_path = episode.dir / "reviews" / "02_analysis_review.json"

    # Build the review structure
    review = {
        "status": "PENDING",
        "episode": episode.episode_id,
        "instructions": (
            "Review and edit this file to curate the episode. "
            "Set chosen_title, adjust segment actions (keep/cut/trim), "
            "reorder arc_order, pick cold_open, approve social clips. "
            "Change status to APPROVED when done."
        ),
        "chosen_title": "",
        "title_options": analysis.get("suggested_titles", []),
        "episode_summary": analysis.get("episode_summary", ""),
        "segments": [],
        "arc_order": analysis.get("suggested_arc", []),
        "cold_open": {
            "chosen_highlight_id": "",
            "options": [
                h["id"] for h in analysis.get("highlights", [])
                if h.get("cold_open_candidate")
            ],
        },
        "social_clips": [
            {
                "highlight_id": h["id"],
                "quote": h.get("quote", ""),
                "type": h.get("type", ""),
                "why_compelling": h.get("why_compelling", ""),
                "approved": True,
            }
            for h in analysis.get("highlights", [])
            if h.get("social_clip_candidate")
        ],
        "chapters": analysis.get("chapters", []),
        "key_takeaways": analysis.get("key_takeaways", []),
        "notes_from_reviewer": "",
    }

    # Build segment entries from topics
    for topic in analysis.get("topics", []):
        review["segments"].append({
            "id": topic["id"],
            "title": topic.get("title", ""),
            "start_segment": topic.get("start_segment"),
            "end_segment": topic.get("end_segment"),
            "summary": topic.get("summary", ""),
            "energy_level": topic.get("energy_level", "medium"),
            "action": "keep" if topic.get("recommendation") != "cut" else "cut",
            "trim_notes": "",
        })

    # Add cut suggestions as annotations
    review["suggested_cuts"] = analysis.get("suggested_cuts", [])

    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=2, ensure_ascii=False)


def analyze_episode(episode) -> None:
    """Run AI analysis on a single episode."""
    analysis_dir = episode.dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    analysis_path = analysis_dir / "content_analysis.json"

    # Check Gate 1 (transcript review must be approved)
    if not check_gate(episode.dir, 1):
        console.print(
            f"  [yellow]{episode.episode_id}: Gate 1 (transcript review) not approved. "
            f"Review and approve reviews/01_transcript_review.md first.[/yellow]"
        )
        return

    # Skip if already analyzed
    if analysis_path.exists():
        console.print(f"  {episode.episode_id}: Analysis already exists, skipping")
        # Still generate review file if missing
        review_path = episode.dir / "reviews" / "02_analysis_review.json"
        if not review_path.exists():
            with open(analysis_path, "r", encoding="utf-8") as f:
                analysis = json.load(f)
            generate_review_gate_2(episode, analysis)
        return

    console.print(f"  {episode.episode_id}: Running AI content analysis...")

    # Load transcript and summary
    transcript_text = load_transcript_text(episode)
    if not transcript_text:
        console.print(f"  [red]{episode.episode_id}: No transcript found. Run Step 3 first.[/red]")
        return

    summary_text = load_summary_text(episode)

    # Call Claude API for analysis
    try:
        analysis = analyze_transcript(
            transcript_text=transcript_text,
            summary_text=summary_text,
            episode_id=episode.episode_id.upper(),
        )
    except Exception as e:
        console.print(f"  [red]{episode.episode_id}: Analysis failed: {e}[/red]")
        return

    # Save full analysis
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    console.print(f"    Found {len(analysis.get('topics', []))} topics, "
                  f"{len(analysis.get('highlights', []))} highlights")
    console.print(f"    Suggested titles: {analysis.get('suggested_titles', [])}")

    # Generate Review Gate 2 file
    generate_review_gate_2(episode, analysis)
    console.print(f"    Review file: reviews/02_analysis_review.json (Status: PENDING)")


def analyze_all(episode_ids: list[str] | None = None) -> None:
    """Analyze all (or specified) episodes."""
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found. Run Step 1 (normalize) first.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 4: AI Content Analysis for {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        analyze_episode(episode)

    console.print(f"\n[bold green]Analysis complete![/bold green]")
    console.print(f"\n[bold]Next step:[/bold] Review the analysis files in each episode's reviews/ folder.")
    console.print("Edit 02_analysis_review.json: choose title, keep/cut segments, set arc order.")
    console.print("Change 'status' to 'APPROVED' when satisfied.\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 4: AI content analysis")
    parser.add_argument("episodes", nargs="*", help="Episode IDs to process (e.g. ep01 ep02)")
    args = parser.parse_args()

    analyze_all(args.episodes or None)

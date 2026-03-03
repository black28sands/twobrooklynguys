"""Review gate logic — generates review files and checks approval status."""

import json
import re
from pathlib import Path


def get_review_dir(episode_dir: Path) -> Path:
    """Return the reviews/ subdirectory for an episode, creating if needed."""
    review_dir = episode_dir / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    return review_dir


def write_review_file(filepath: Path, content: str) -> None:
    """Write a review file (Markdown or JSON)."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


def read_review_file(filepath: Path) -> str:
    """Read a review file and return its contents."""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def is_approved_md(filepath: Path) -> bool:
    """Check if a Markdown review file has been approved.

    Looks for 'Status: APPROVED' or 'Verdict: APPROVED' (case-insensitive).
    """
    if not filepath.exists():
        return False
    content = read_review_file(filepath)
    return bool(re.search(r"(?:Status|Verdict):\s*APPROVED", content, re.IGNORECASE))


def is_approved_json(filepath: Path) -> bool:
    """Check if a JSON review file has been approved.

    Looks for "status": "APPROVED" (case-insensitive).
    """
    if not filepath.exists():
        return False
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    status = data.get("status", "").upper()
    return status == "APPROVED"


def is_approved(filepath: Path) -> bool:
    """Check if a review file is approved (auto-detects MD vs JSON)."""
    if filepath.suffix == ".json":
        return is_approved_json(filepath)
    return is_approved_md(filepath)


def check_gate(episode_dir: Path, gate_number: int) -> bool:
    """Check if a specific review gate is approved for an episode.

    Gate numbers: 1=transcript, 2=analysis, 3=edit, 4=publish
    """
    review_dir = episode_dir / "reviews"
    gate_files = {
        1: "01_transcript_review.md",
        2: "02_analysis_review.json",
        3: "03_edit_review.md",
        4: "04_publish_review.md",
    }
    filename = gate_files.get(gate_number)
    if not filename:
        raise ValueError(f"Invalid gate number: {gate_number}")
    return is_approved(review_dir / filename)


def gate_status(episode_dir: Path) -> dict[int, str]:
    """Return the status of all 4 gates for an episode.

    Returns dict like {1: "APPROVED", 2: "PENDING", 3: "NOT_STARTED", 4: "NOT_STARTED"}
    """
    review_dir = episode_dir / "reviews"
    gate_files = {
        1: "01_transcript_review.md",
        2: "02_analysis_review.json",
        3: "03_edit_review.md",
        4: "04_publish_review.md",
    }
    statuses = {}
    for gate_num, filename in gate_files.items():
        filepath = review_dir / filename
        if not filepath.exists():
            statuses[gate_num] = "NOT_STARTED"
        elif is_approved(filepath):
            statuses[gate_num] = "APPROVED"
        else:
            statuses[gate_num] = "PENDING"
    return statuses

"""Episode manifest — data model and I/O for the episode index."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import episodes_root


@dataclass
class EpisodeSource:
    """Tracks the original source files for an episode."""
    original_folder: str
    mp4_filename: str
    mp4_size_bytes: int
    transcript_filename: str
    summary_filename: str | None = None
    short_summary_filename: str | None = None
    extra_files: list[str] = field(default_factory=list)


@dataclass
class Episode:
    """Represents a single podcast episode in the pipeline."""
    episode_number: int
    episode_id: str           # e.g. "ep01"
    date: str                 # YYYY-MM-DD
    folder_name: str          # e.g. "ep01-2025-08-07"
    recording_type: str       # "regular" or "impromptu"
    source: EpisodeSource
    title: str = ""
    duration_seconds: float = 0.0
    speakers: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    summary: str = ""
    status: str = "new"       # new, normalized, transcribed, analyzed, edited, mastered, published

    @property
    def dir(self) -> Path:
        """Return the episode working directory path."""
        return episodes_root() / self.folder_name


@dataclass
class Manifest:
    """The master episode manifest containing all episodes."""
    episodes: list[Episode] = field(default_factory=list)

    def get_episode(self, episode_id: str) -> Episode | None:
        """Look up an episode by its ID (e.g. 'ep01' or 'EP01')."""
        normalized = episode_id.lower().replace("ep", "ep")
        for ep in self.episodes:
            if ep.episode_id == normalized:
                return ep
        return None

    def save(self, path: Path | None = None) -> None:
        """Save the manifest to JSON."""
        if path is None:
            path = episodes_root() / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"episodes": [asdict(ep) for ep in self.episodes]},
                f,
                indent=2,
                ensure_ascii=False,
            )

    @classmethod
    def load(cls, path: Path | None = None) -> "Manifest":
        """Load a manifest from JSON."""
        if path is None:
            path = episodes_root() / "manifest.json"
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        episodes = []
        for ep_data in data.get("episodes", []):
            source_data = ep_data.pop("source")
            source = EpisodeSource(**source_data)
            episodes.append(Episode(source=source, **ep_data))
        return cls(episodes=episodes)

"""Step 7: Master audio & video — loudness normalization and final export."""

import subprocess
import sys
from pathlib import Path

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.helpers.config import get_config
from scripts.helpers.episode_manifest import Manifest
from scripts.helpers.review_gate import check_gate
from scripts.helpers.ffmpeg_wrapper import normalize_loudness, export_mp3

console = Console()


def measure_lufs(audio_path: Path) -> dict:
    """Measure LUFS and true peak of an audio file."""
    try:
        import soundfile as sf
        import pyloudnorm as pyln

        data, rate = sf.read(str(audio_path))
        meter = pyln.Meter(rate)
        loudness = meter.integrated_loudness(data)
        peak = max(abs(data.min()), abs(data.max()))
        # Convert peak to dBFS
        import math
        peak_db = 20 * math.log10(peak) if peak > 0 else -100

        return {
            "lufs": round(loudness, 1),
            "true_peak_db": round(peak_db, 1),
            "sample_rate": rate,
        }
    except Exception as e:
        return {"error": str(e)}


def master_episode(episode) -> None:
    """Master audio and video for a single episode."""
    audio_dir = episode.dir / "audio"
    video_dir = episode.dir / "video"
    config = get_config()
    audio_cfg = config["audio"]

    assembled_wav = audio_dir / "assembled.wav"
    assembled_mp4 = video_dir / "assembled.mp4"

    # Check Gate 3
    if not check_gate(episode.dir, 3):
        console.print(
            f"  [yellow]{episode.episode_id}: Gate 3 (edit review) not approved. "
            f"Review and approve reviews/03_edit_review.md first.[/yellow]"
        )
        return

    # === AUDIO MASTERING ===
    master_wav = audio_dir / "master.wav"
    master_mp3 = audio_dir / "master.mp3"

    if assembled_wav.exists() and not master_wav.exists():
        console.print(f"  {episode.episode_id}: Mastering audio...")

        # Measure before
        before = measure_lufs(assembled_wav)
        if "lufs" in before:
            console.print(f"    Before: {before['lufs']} LUFS, peak {before['true_peak_db']} dB")

        # Normalize loudness
        target_lufs = audio_cfg["target_lufs"]
        true_peak = audio_cfg["true_peak_limit"]
        console.print(f"    Normalizing to {target_lufs} LUFS, peak limit {true_peak} dB...")

        normalize_loudness(assembled_wav, master_wav, target_lufs, true_peak)

        # Measure after
        after = measure_lufs(master_wav)
        if "lufs" in after:
            console.print(f"    After: {after['lufs']} LUFS, peak {after['true_peak_db']} dB")
            # Check compliance
            lufs_ok = abs(after["lufs"] - target_lufs) < 2
            peak_ok = after["true_peak_db"] <= true_peak + 0.5
            status = "[green]PASS[/green]" if (lufs_ok and peak_ok) else "[red]FAIL[/red]"
            console.print(f"    Compliance: {status}")

        # Export MP3
        console.print(f"    Exporting MP3 ({audio_cfg['mp3_bitrate']})...")
        export_mp3(master_wav, master_mp3, audio_cfg["mp3_bitrate"])
        console.print(f"    [green]Master audio: {master_mp3}[/green]")

    elif master_wav.exists():
        console.print(f"  {episode.episode_id}: Master audio already exists, skipping")

    # === VIDEO MASTERING ===
    master_mp4 = video_dir / "master.mp4"

    if assembled_mp4.exists() and not master_mp4.exists():
        console.print(f"  {episode.episode_id}: Mastering video...")

        video_cfg = config["video"]

        # Re-mux with mastered audio if available
        audio_source = master_wav if master_wav.exists() else assembled_wav

        if audio_source.exists():
            # Replace audio track with mastered audio
            subprocess.run([
                "ffmpeg", "-y",
                "-i", str(assembled_mp4),
                "-i", str(audio_source),
                "-c:v", video_cfg["codec"],
                "-crf", str(video_cfg["crf"]),
                "-preset", "medium",
                "-c:a", "aac", "-b:a", "128k",
                "-map", "0:v:0",     # video from assembled mp4
                "-map", "1:a:0",     # audio from mastered wav
                "-movflags", "+faststart",
                str(master_mp4),
            ], capture_output=True, check=True)
        else:
            # Just re-encode video
            subprocess.run([
                "ffmpeg", "-y",
                "-i", str(assembled_mp4),
                "-c:v", video_cfg["codec"],
                "-crf", str(video_cfg["crf"]),
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(master_mp4),
            ], capture_output=True, check=True)

        console.print(f"    [green]Master video: {master_mp4}[/green]")

    elif master_mp4.exists():
        console.print(f"  {episode.episode_id}: Master video already exists, skipping")


def master_all(episode_ids: list[str] | None = None) -> None:
    """Master all (or specified) episodes."""
    manifest = Manifest.load()

    if not manifest.episodes:
        console.print("[red]No episodes found. Run Step 1 first.[/red]")
        return

    episodes = manifest.episodes
    if episode_ids:
        episodes = [ep for ep in episodes if ep.episode_id in [e.lower() for e in episode_ids]]

    console.print(f"[bold]Step 7: Mastering {len(episodes)} episodes...[/bold]\n")

    for episode in episodes:
        master_episode(episode)

    console.print(f"\n[bold green]Mastering complete![/bold green]\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Step 7: Master audio & video")
    parser.add_argument("episodes", nargs="*", help="Episode IDs (e.g. ep01 ep02)")
    args = parser.parse_args()

    master_all(args.episodes or None)

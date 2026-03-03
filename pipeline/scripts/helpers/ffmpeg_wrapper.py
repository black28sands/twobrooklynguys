"""FFmpeg/FFprobe wrapper for common media operations."""

import json
import os
import subprocess
from pathlib import Path

# Auto-discover FFmpeg from WinGet install location if not on PATH
_FFMPEG_DIR = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
for _pkg in sorted(_FFMPEG_DIR.glob("Gyan.FFmpeg*"), reverse=True) if _FFMPEG_DIR.exists() else []:
    _bin = _pkg / "ffmpeg-8.0.1-full_build" / "bin"
    if not _bin.exists():
        # Try any version
        for _sub in _pkg.iterdir():
            _candidate = _sub / "bin"
            if _candidate.exists() and (_candidate / "ffmpeg.exe").exists():
                _bin = _candidate
                break
    if _bin.exists() and (_bin / "ffmpeg.exe").exists():
        os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
        break


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available on PATH."""
    try:
        _run(["ffmpeg", "-version"])
        return True
    except FileNotFoundError:
        return False


def check_ffprobe() -> bool:
    """Return True if ffprobe is available on PATH."""
    try:
        _run(["ffprobe", "-version"])
        return True
    except FileNotFoundError:
        return False


def get_duration(media_path: Path) -> float:
    """Return duration of a media file in seconds."""
    result = _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(media_path),
    ])
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def get_media_info(media_path: Path) -> dict:
    """Return full media info (format + streams) as a dict."""
    result = _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(media_path),
    ])
    return json.loads(result.stdout)


def extract_audio(
    input_path: Path,
    output_path: Path,
    sample_rate: int = 48000,
    channels: int = 1,
) -> None:
    """Extract audio from a video file to WAV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",
        "-acodec", "pcm_s24le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        str(output_path),
    ])


def normalize_loudness(
    input_path: Path,
    output_path: Path,
    target_lufs: float = -16.0,
    true_peak: float = -1.0,
) -> None:
    """Apply EBU R128 loudness normalization."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-af", f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11",
        str(output_path),
    ])


def export_mp3(
    input_path: Path,
    output_path: Path,
    bitrate: str = "128k",
) -> None:
    """Export audio as MP3."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-codec:a", "libmp3lame",
        "-b:a", bitrate,
        str(output_path),
    ])


def cut_segment(
    input_path: Path,
    output_path: Path,
    start_time: str,
    end_time: str,
    copy_codec: bool = True,
) -> None:
    """Extract a segment from a media file by start/end timestamps (HH:MM:SS.ms)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-ss", start_time,
        "-to", end_time,
    ]
    if copy_codec:
        cmd += ["-c", "copy"]
    cmd.append(str(output_path))
    _run(cmd)


def concat_files(file_list_path: Path, output_path: Path, copy_codec: bool = True) -> None:
    """Concatenate media files listed in a text file (ffmpeg concat demuxer format)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(file_list_path),
    ]
    if copy_codec:
        cmd += ["-c", "copy"]
    cmd.append(str(output_path))
    _run(cmd)


def detect_silence(
    input_path: Path,
    noise_db: int = -40,
    min_duration: float = 2.0,
) -> list[dict]:
    """Detect silence regions in an audio file.

    Returns list of dicts with 'start', 'end', and 'duration' keys.
    """
    result = _run([
        "ffmpeg",
        "-i", str(input_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ], check=False)

    # Parse silence detection from stderr
    silences = []
    current: dict = {}
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            parts = line.split("silence_start:")
            current = {"start": float(parts[1].strip())}
        elif "silence_end:" in line:
            parts = line.split("silence_end:")[1].strip().split("|")
            current["end"] = float(parts[0].strip())
            if len(parts) > 1:
                dur_part = parts[1].strip()
                current["duration"] = float(dur_part.split(":")[1].strip())
            silences.append(current)
            current = {}

    return silences

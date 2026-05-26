import shutil
import subprocess


class FFmpegNotFoundError(RuntimeError):
    pass


class FrameExtractionError(RuntimeError):
    pass


def ensure_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise FFmpegNotFoundError(
            "ffmpeg not found on PATH — install via: brew install ffmpeg"
        )
    return path


def extract_frame(media_path: str, timecode_seconds: float, output_path: str) -> None:
    ffmpeg = ensure_ffmpeg()
    seek = max(0.0, float(timecode_seconds))
    cmd = [
        ffmpeg,
        "-y",
        "-ss", f"{seek:.6f}",
        "-i", media_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise FrameExtractionError(
            f"ffmpeg failed extracting {media_path} @ {seek:.3f}s: {stderr.splitlines()[-1] if stderr else 'no stderr'}"
        )

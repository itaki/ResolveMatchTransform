from dataclasses import dataclass
from typing import Optional


class ResolveError(RuntimeError):
    pass


@dataclass
class ClipInfo:
    item: object  # TimelineItem (opaque Resolve handle)
    media_path: str
    width: int
    height: int
    source_frame: int  # frame number inside the source media at the playhead
    fps: float


def connect():
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError as exc:
        raise ResolveError(
            "Could not import DaVinciResolveScript. Set RESOLVE_SCRIPT_API / "
            "RESOLVE_SCRIPT_LIB / PYTHONPATH per CLAUDE.md."
        ) from exc
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ResolveError("Resolve is not running, or scripting is disabled in Preferences.")
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject() if pm else None
    if project is None:
        raise ResolveError("No project is open in Resolve.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise ResolveError("No timeline is open in Resolve.")
    return resolve, project, timeline


def timeline_fps(timeline) -> float:
    return float(timeline.GetSetting("timelineFrameRate"))


def timeline_resolution(timeline) -> tuple[int, int]:
    """(width, height) of the current timeline in pixels."""
    w = timeline.GetSetting("timelineResolutionWidth")
    h = timeline.GetSetting("timelineResolutionHeight")
    if not w or not h:
        raise ResolveError("Could not read timeline resolution from Resolve.")
    return int(w), int(h)


def playhead_frame(timeline) -> int:
    """Convert Resolve's current timecode string to an absolute frame number."""
    tc = timeline.GetCurrentTimecode()  # "HH:MM:SS:FF" or "HH;MM;SS;FF" (drop-frame)
    if not tc:
        raise ResolveError("Could not read playhead timecode from Resolve.")
    fps = timeline_fps(timeline)
    fps_int = int(round(fps))
    parts = tc.replace(";", ":").split(":")
    if len(parts) != 4:
        raise ResolveError(f"Unexpected timecode format: {tc!r}")
    h, m, s, f = (int(p) for p in parts)
    # Non-drop calculation; Resolve handles drop internally for display, so
    # this matches GetStart()/GetEnd() for clips on non-drop-frame timelines.
    # Drop-frame timelines (29.97/59.94 NTSC) may be off by a few frames near
    # 10-minute boundaries; reframe workflows rarely sit on those edges.
    return (h * 3600 + m * 60 + s) * fps_int + f


def clip_at_frame(timeline, track_index: int, frame: int):
    items = timeline.GetItemListInTrack("video", track_index) or []
    for item in items:
        if item.GetStart() <= frame < item.GetEnd():
            return item
    return None


def resolve_clip_info(timeline, item) -> ClipInfo:
    mp = item.GetMediaPoolItem()
    if mp is None:
        raise ResolveError("Selected clip has no media-pool item (compound clip or generator?).")
    media_path = mp.GetClipProperty("File Path")
    if not media_path:
        raise ResolveError("Could not read 'File Path' from media-pool item.")
    width_s = mp.GetClipProperty("Resolution") or ""
    width = height = 0
    if "x" in width_s:
        try:
            w, h = width_s.lower().split("x", 1)
            width = int(w.strip())
            height = int(h.strip())
        except ValueError:
            width = height = 0
    fps = timeline_fps(timeline)
    timeline_offset = playhead_frame(timeline) - item.GetStart()
    source_frame = int(item.GetLeftOffset()) + max(0, timeline_offset)
    return ClipInfo(
        item=item,
        media_path=media_path,
        width=width,
        height=height,
        source_frame=source_frame,
        fps=fps,
    )


def source_seconds(info: ClipInfo) -> float:
    return float(info.source_frame) / float(info.fps)


def write_transform(item, pan: float, tilt: float, zoom: float) -> None:
    # ZoomGang defaults to True; setting both is explicit and safer than
    # relying on the gang to mirror ZoomX into ZoomY.
    ok = True
    ok &= bool(item.SetProperty("ZoomX", float(zoom)))
    ok &= bool(item.SetProperty("ZoomY", float(zoom)))
    ok &= bool(item.SetProperty("Pan", float(pan)))
    ok &= bool(item.SetProperty("Tilt", float(tilt)))
    if not ok:
        raise ResolveError(
            "SetProperty returned False — check Resolve scripting permissions "
            "(Preferences → System → General → External scripting using = Local)."
        )


def read_transform(item) -> dict:
    props = item.GetProperty() or {}
    return {k: props.get(k) for k in ("Pan", "Tilt", "ZoomX", "ZoomY", "RotationAngle")}


def clip_name(item) -> str:
    try:
        return item.GetName() or "<unnamed>"
    except Exception:
        return "<unnamed>"

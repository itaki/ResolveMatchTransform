# ResolveMatchFrame — CLAUDE.md

## Project Overview

A DaVinci Resolve Python script with a minimal PyQt6 floating panel that automatically matches the transform (pan, tilt, zoom) of a **source clip** to a **reference clip** on the timeline using OpenCV image registration. Designed for conform workflows where a colorist's output needs to be reframed to match an editor's approved offline reference.

## Target Environment

- macOS only (Apple Silicon, M2 Studio)
- DaVinci Resolve Studio (paid) — scripting API requires Studio
- Python 3.10+ (Resolve's bundled Python or system Python with env vars set)
- Dependencies: `opencv-python`, `numpy`, `PyQt6`

## Installation Location

The Resolve menu launcher lives in Resolve's Utility Scripts folder:
```
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
```

`setup.sh` creates only `ResolveMatchFrame.py` there, and installs the supporting
Python package outside Resolve's script-scanned folders at
`~/Library/Application Support/ResolveMatchFrame/`. This keeps Resolve from
showing internal files like `core`, `ui`, and `match_frame` in the Scripts menu.
The script launches the PyQt6 panel as a floating window external to Resolve —
Resolve stays in focus.

## Environment Variables (required for external Python)

```bash
export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
```

## Core Workflow

1. User places **source clip** (colored, unframed) and **reference clip** (approved offline/editor cut) on the timeline — on any tracks, in any order
2. User selects exactly **two clips** in the Resolve timeline (one source, one reference)
3. User opens the ResolveMatchFrame panel from Workspace → Scripts
4. Panel auto-detects which clip is which based on user selection in the UI (see UI section)
5. User clicks **Match Transform**
6. Script extracts one frame from each clip as a temporary PNG
7. OpenCV computes the similarity transform (scale + pan + tilt) between the two frames
8. Script writes the computed transform values back to the **source clip** via `TimelineItem.SetProperty()`
9. Temporary frame files are deleted

## Resolve API — Key Methods

```python
import DaVinciResolveScript as dvr
resolve = dvr.scriptapp("Resolve")
project = resolve.GetProjectManager().GetCurrentProject()
timeline = project.GetCurrentTimeline()

# Get selected clips (returns list of TimelineItem)
selected = timeline.GetCurrentVideoItem()  # single selected
# For multi-select, iterate tracks:
items = timeline.GetItemListInTrack("video", track_number)

# Read transform
props = clip.GetProperty()
# props keys: 'Pan', 'Tilt', 'ZoomX', 'ZoomY', 'ZoomGang', 'RotationAngle'

# Write transform
clip.SetProperty("ZoomX", zoom_value)
clip.SetProperty("ZoomY", zoom_value)  # always set both; ZoomGang is True by default
clip.SetProperty("Pan", pan_value)
clip.SetProperty("Tilt", tilt_value)
```

## CRITICAL: Resolve Coordinate System

This is the most important implementation detail — get this wrong and every transform will be off.

### Zoom
- `ZoomX` / `ZoomY` are in **percentage** where `1.0 = 100%` (no zoom), `1.75 = 175%`
- Range: 0.0 to 100.0 (but practically 0.1 to 10.0)
- `ZoomGang` is True by default — setting ZoomX auto-sets ZoomY, but set both explicitly

### Pan / Tilt
- Pan and Tilt are in **pixels relative to the source clip's native resolution**, NOT the timeline resolution
- This differs from Premiere Pro (which uses timeline pixels) — do not port formulas from Premiere
- Pan range: `-4.0 * clip_width` to `4.0 * clip_width`
- Tilt range: `-4.0 * clip_height` to `4.0 * clip_height`
- At 4K (3840×2160): Pan range is approximately -15360 to +15360

### Converting OpenCV output to Resolve values

OpenCV `estimateAffinePartial2D` returns a 2×3 matrix `M`:
```
M = [[scale*cos(θ), -scale*sin(θ), tx],
     [scale*sin(θ),  scale*cos(θ), ty]]
```

Extraction:
```python
import numpy as np

scale = np.sqrt(M[0, 0]**2 + M[1, 0]**2)
tx = M[0, 2]  # translation in reference frame pixels
ty = M[1, 2]

# Convert tx/ty from reference image pixels to source clip pixels
# reference image was extracted at full resolution
# Resolve Pan/Tilt are in source clip pixel space
pan_resolve = tx * (source_width / ref_width)
tilt_resolve = ty * (source_height / ref_height)

zoom_resolve = scale  # already a ratio; 1.0 = 100%
```

**Note:** Resolve's Pan/Tilt sign convention: positive Pan = right, positive Tilt = up (Y-axis inverted vs OpenCV). Negate tilt:
```python
tilt_resolve = -tilt_resolve
```

## Frame Extraction

Use `ffmpeg` (available on macOS via Homebrew) to extract a single frame from each clip at its source timecode. Do NOT use Resolve's export for this — too slow.

```python
import subprocess, tempfile, os

def extract_frame(media_path, timecode_seconds, output_path):
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(timecode_seconds),
        "-i", media_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path
    ], check=True, capture_output=True)
```

Get the media path and timecode from the TimelineItem:
```python
media_pool_item = clip.GetMediaPoolItem()
media_path = media_pool_item.GetClipProperty("File Path")
start_tc = clip.GetStart()  # in frames
fps = timeline.GetSetting("timelineFrameRate")
start_seconds = float(start_tc) / float(fps)
```

Extract the **first frame** of each clip for matching. For static reframes this is sufficient.

## OpenCV Matching

```python
import cv2
import numpy as np

def compute_transform(ref_img_path, src_img_path):
    ref = cv2.imread(ref_img_path, cv2.IMREAD_GRAYSCALE)
    src = cv2.imread(src_img_path, cv2.IMREAD_GRAYSCALE)

    # Downscale for speed — matching at 1/4 res is fine for static transforms
    scale_factor = 0.25
    ref_small = cv2.resize(ref, None, fx=scale_factor, fy=scale_factor)
    src_small = cv2.resize(src, None, fx=scale_factor, fy=scale_factor)

    # ORB feature detector (patent-free, fast, works well for same-content shots)
    orb = cv2.ORB_create(nfeatures=2000)
    kp1, des1 = orb.detectAndCompute(ref_small, None)
    kp2, des2 = orb.detectAndCompute(src_small, None)

    # Match
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    matches = bf.knnMatch(des1, des2, k=2)

    # Lowe ratio test
    good = [m for m, n in matches if m.distance < 0.75 * n.distance]

    if len(good) < 10:
        raise ValueError(f"Not enough feature matches ({len(good)}). Check clips are the same shot.")

    pts1 = np.float32([kp1[m.queryIdx].pt for m in good])
    pts2 = np.float32([kp2[m.trainIdx].pt for m in good])

    # Estimate similarity transform (4 DOF: scale, rotation, tx, ty)
    M, inliers = cv2.estimateAffinePartial2D(pts2, pts1, method=cv2.RANSAC, ransacReprojThreshold=3.0)

    if M is None:
        raise ValueError("Transform estimation failed. RANSAC found no valid solution.")

    # Scale tx/ty back up from downscaled space
    M[0, 2] /= scale_factor
    M[1, 2] /= scale_factor

    return M
```

## UI — PyQt6 Floating Panel

Small, minimal, always-on-top window. Not docked into Resolve.

### Layout
```
┌─────────────────────────────────┐
│  ResolveMatchFrame              │
├─────────────────────────────────┤
│  Source track:  [V1 ▼]         │
│  Reference track: [V2 ▼]       │
├─────────────────────────────────┤
│  Selected clip:  [source ▼]    │  ← which of the two selected clips is source
│                                 │
│  [ Match Transform ]            │
├─────────────────────────────────┤
│  Status: Ready                  │
└─────────────────────────────────┘
```

### Behavior
- Track number dropdowns default to V1 (source) and V2 (reference) — match Michael's workflow
- "Match Transform" button triggers the full pipeline
- Status bar shows: Ready → Extracting frames → Matching… → Done ✓ (or error message)
- Window stays on top of Resolve (`Qt.WindowStaysOnTopHint`)
- No modal dialogs — all feedback goes to the status bar

### Error states to handle in status bar
- "Select exactly 2 clips on the timeline"
- "Not enough feature matches — are these the same shot?"
- "ffmpeg not found — install via: brew install ffmpeg"
- "Transform estimation failed"
- "Could not write to clip — check Resolve scripting permissions"

## File Structure

```
ResolveMatchFrame/
├── CLAUDE.md               ← this file
├── match_frame.py          ← main script (entry point for Resolve Scripts menu)
├── core/
│   matching.py             ← OpenCV logic, compute_transform()
│   resolver.py             ← Resolve API wrapper, get/set clip properties
│   frame_extract.py        ← ffmpeg frame extraction
└── ui/
    panel.py                ← PyQt6 floating panel
```

`match_frame.py` is the only file Resolve sees. It bootstraps the app and launches the PyQt panel.

## Dependencies & Setup

Provide a `setup.sh`:
```bash
#!/bin/bash
pip install opencv-python numpy PyQt6
brew install ffmpeg  # if not already installed
```

## Known Limitations & Notes

- **Static transforms only** — this tool matches a single frame. Animated (keyframed) reframes are out of scope.
- **Same shot required** — ORB matching will fail or produce garbage if the two clips are different content. The "not enough matches" error is the safeguard.
- **Color difference is fine** — ORB works on gradients, not pixel values. Log vs. graded color is not a problem.
- **Burn-ins on reference** — small timecode burns in corners are unlikely to affect matching significantly due to RANSAC outlier rejection. Large watermarks covering most of the frame may degrade accuracy.
- **ZoomGang** — always set ZoomX and ZoomY to the same value and leave ZoomGang True unless rotation is involved.
- **Rotation** — the tool computes rotation but does NOT write it back by default (most reframes are pan/tilt/zoom only). Add a "Apply rotation" checkbox if needed later.
- **SuperScale** — this script does not touch SuperScale. Set that separately via Clip Attributes as discussed.
- **Resolve Studio required** — the scripting API is not available in the free version of Resolve.

# ResolveMatchTransform

[View on GitHub](https://github.com/itaki/ResolveMatchTransform)

ResolveMatchTransform is a macOS Python utility script for DaVinci Resolve Studio. It opens a small floating PyQt6 panel that matches the pan, tilt, and zoom of a source clip to a reference clip on the Resolve timeline.

It is designed for conform workflows where a colored or finishing source needs to be reframed to match an editor-approved offline reference.

## What It Does

ResolveMatchTransform compares one frame from a source clip against one frame from a reference clip, computes the similarity transform between them with OpenCV, and writes the resulting transform back to the source clip in Resolve.

The tool applies:

- `Pan`
- `Tilt`
- `ZoomX`
- `ZoomY`

It computes rotation internally but does not apply rotation to the clip.

## Requirements

- macOS
- DaVinci Resolve Studio
- Python 3.10+
- Homebrew, recommended for installing `ffmpeg`
- Python packages:
  - `opencv-python`
  - `numpy`
  - `PyQt6`
- `ffmpeg`

Resolve scripting must be enabled:

```text
DaVinci Resolve Preferences -> System -> General -> External scripting using: Local
```

## Installation

Run the setup script from this project folder:

```bash
bash setup.sh
```

The installer:

- Installs or upgrades `opencv-python`, `numpy`, and `PyQt6` for the Python interpreter ResolveMatchTransform will use.
- Installs `ffmpeg` with Homebrew if `ffmpeg` is not already available.
- Writes the resolved Python interpreter path to `.python_path`.
- Creates a symlink in Resolve's Utility Scripts folder:

```text
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/ResolveMatchTransform.py
```

After setup, restart Resolve if it was already open.

## Where It Appears in Resolve

Resolve scans its Utility Scripts folder on launch. After installation, open the panel from:

```text
Workspace -> Scripts -> ResolveMatchTransform
```

The panel opens as a floating window outside Resolve and stays on top.

## How to Use

1. Put the source clip and reference clip on the Resolve timeline.
2. Move the playhead to a frame where both clips overlap in time.
3. Open `Workspace -> Scripts -> ResolveMatchTransform`.
4. Choose the source video track and reference video track.
5. Choose a match precision mode.
6. Click `Match Transform`.

When successful, the status line shows the computed pan, tilt, zoom, and feature inlier count. The transform is written to the source clip.

## Panel Controls

`Source track` is the video track that contains the clip you want to change. ResolveMatchTransform finds the clip on this track at the current playhead position and writes the computed `Pan`, `Tilt`, `ZoomX`, and `ZoomY` values to that clip.

`Reference track` is the video track that contains the approved framing. ResolveMatchTransform finds the clip on this track at the current playhead position and uses its frame as the target framing.

Both track dropdowns are playhead-based. You do not need to select clips in the timeline; the playhead just needs to sit over both the source clip and the reference clip on their chosen tracks.

`Match precision` controls how strict the OpenCV feature matching and RANSAC filtering should be:

- `Standard`: Best default for normal conform work where the clips are the same shot but may differ in grade, resolution, or framing.
- `Pixel-perfect (strict)`: Use when the images should be almost identical apart from color or compression. This mode uses tighter matching tolerances and is less forgiving of overlays, VFX changes, or mismatched frames.
- `VFX / partial overlap`: Use when only part of the frame matches, such as shots with localized VFX, comps, graphics, or other changed regions. This mode looks for more features and rejects outliers more aggressively.

Use the simplest mode that works. Start with `Standard`, then try `Pixel-perfect (strict)` or `VFX / partial overlap` when the shot calls for it.

## How It Works

The Resolve menu entry point is `match_frame.py`. Resolve launches that script, and the script spawns the PyQt6 panel in a separate external Python process. This avoids running the PyQt6 UI inside Resolve's own Qt process.

The matching pipeline is:

1. Connect to the current Resolve project and timeline.
2. Find the source and reference clips at the current playhead on the selected tracks.
3. Extract one frame from each clip with `ffmpeg`.
4. Use OpenCV ORB feature matching and RANSAC to estimate a similarity transform.
5. Convert the OpenCV transform into Resolve's `Pan`, `Tilt`, and `Zoom` values.
6. Write the transform to the source clip with Resolve's scripting API.

Resolve's pan and tilt values are in source-clip pixel space, not timeline pixel space. The conversion code accounts for Resolve's source-to-timeline fit behavior and its inverted tilt convention.

## Logs and Debug Frames

Runtime logs are written here:

```text
~/Library/Logs/ResolveMatchTransform.log
```

The most recent extracted frames are kept here for inspection:

```text
~/Library/Logs/ResolveMatchTransform/source.png
~/Library/Logs/ResolveMatchTransform/reference.png
```

Use the panel's `Reveal frames` button to open that folder in Finder.

## Project Structure

```text
ResolveMatchTransform/
├── match_frame.py          # Resolve Scripts menu entry point
├── setup.sh                # dependency installer and Resolve symlink setup
├── core/
│   ├── frame_extract.py    # ffmpeg frame extraction
│   ├── matching.py         # OpenCV feature matching and transform conversion
│   └── resolver.py         # Resolve API wrapper
└── ui/
    ├── icon.py             # panel icon
    └── panel.py            # PyQt6 floating panel
```

## Troubleshooting

- If the script does not appear in Resolve, rerun `bash setup.sh` and restart Resolve.
- If the panel does not open, check `~/Library/Logs/ResolveMatchTransform.log`.
- If matching fails with not enough features or matches, confirm both clips show the same shot at the playhead.
- If `ffmpeg` is missing, install it with `brew install ffmpeg` or rerun `setup.sh`.
- If Resolve refuses to write the transform, confirm external scripting is set to `Local` in Resolve preferences.

## Limitations

- Static transforms only. It matches one frame and does not create animated keyframes.
- The source and reference must share visual content.
- Large burn-ins, heavy overlays, or major VFX differences can reduce matching accuracy.
- Rotation is estimated but not applied.
- SuperScale and other clip attributes are not changed.
- DaVinci Resolve Studio is required; the free version does not expose the required scripting API.

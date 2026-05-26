import atexit
import os
import shutil
import sys
import tempfile
import time
import traceback

# Persist the last extraction for inspection: ~/Library/Logs/ResolveMatchTransform/
DEBUG_DIR = os.path.expanduser("~/Library/Logs/ResolveMatchTransform")
LOG_PATH = os.path.expanduser("~/Library/Logs/ResolveMatchTransform.log")
PID_FILE = os.path.expanduser("~/Library/Logs/ResolveMatchTransform/panel.pid")


def _diag(line: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%H:%M:%S')}] PANEL  {line}\n")
    except OSError:
        pass

# Allow running this file directly: add project root to sys.path so `core` imports work.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import frame_extract, matching, resolver
from ui.icon import make_app_icon


class MatchWorker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, source_track: int, ref_track: int,
                 mode: str = matching.MatchMode.STANDARD,
                 fill_frame: bool = False, parent=None):
        super().__init__(parent)
        self.source_track = source_track
        self.ref_track = ref_track
        self.mode = mode
        self.fill_frame = fill_frame

    def run(self) -> None:
        # Use a stable debug dir so the user can always find the last
        # extracted frames at ~/Library/Logs/ResolveMatchTransform/{source,reference}.png
        os.makedirs(DEBUG_DIR, exist_ok=True)
        src_png = os.path.join(DEBUG_DIR, "source.png")
        ref_png = os.path.join(DEBUG_DIR, "reference.png")
        try:
            _diag(f"=== match run (fill_frame={self.fill_frame}) source=V{self.source_track} ref=V{self.ref_track} ===")
            self.progress.emit("Connecting to Resolve…")
            _resolve, _project, timeline = resolver.connect()

            self.progress.emit("Locating clips at playhead…")
            playhead = resolver.playhead_frame(timeline)
            src_item = resolver.clip_at_frame(timeline, self.source_track, playhead)
            ref_item = resolver.clip_at_frame(timeline, self.ref_track, playhead)
            if src_item is None:
                raise resolver.ResolveError(
                    f"No clip on V{self.source_track} at the playhead — move the playhead over the source clip."
                )
            if ref_item is None:
                raise resolver.ResolveError(
                    f"No clip on V{self.ref_track} at the playhead — move the playhead over the reference clip."
                )

            src_info = resolver.resolve_clip_info(timeline, src_item)
            ref_info = resolver.resolve_clip_info(timeline, ref_item)
            tl_w, tl_h = resolver.timeline_resolution(timeline)

            _diag(f"playhead={playhead}  fps={src_info.fps}  timeline={tl_w}x{tl_h}")
            _diag(f"SRC name={resolver.clip_name(src_item)!r} media={src_info.media_path!r}")
            _diag(f"    reported_res={src_info.width}x{src_info.height} source_frame={src_info.source_frame}")
            _diag(f"REF name={resolver.clip_name(ref_item)!r} media={ref_info.media_path!r}")
            _diag(f"    reported_res={ref_info.width}x{ref_info.height} source_frame={ref_info.source_frame}")

            self.progress.emit("Extracting frames…")
            frame_extract.extract_frame(src_info.media_path, resolver.source_seconds(src_info), src_png)
            frame_extract.extract_frame(ref_info.media_path, resolver.source_seconds(ref_info), ref_png)
            _diag(f"extracted -> {src_png}")
            _diag(f"extracted -> {ref_png}")

            # We need source's pixel dimensions for the final Pan/Tilt math.
            # Prefer the media-pool reported resolution; fall back to reading
            # the extracted PNG.
            src_w, src_h = src_info.width, src_info.height
            if src_w == 0 or src_h == 0:
                import cv2 as _cv2
                im = _cv2.imread(src_png)
                if im is not None:
                    src_h, src_w = im.shape[:2]
                _diag(f"src resolution fallback: src={src_w}x{src_h}")

            self.progress.emit(f"Matching features ({self.mode})…")
            result = matching.compute_transform(
                ref_png, src_png, canvas_size=(tl_w, tl_h), mode=self.mode,
            )
            _diag(
                f"mode={result.mode}  space={result.match_space}  canvas={result.canvas_shape}  "
                f"ref_in={result.ref_input_shape}  src_in={result.src_input_shape}"
            )
            inlier_pct = (100.0 * result.inlier_count / result.match_count) if result.match_count else 0.0
            _diag(
                f"match: tx={result.tx:+.3f} ty={result.ty:+.3f} scale={result.scale:.6f} "
                f"rot={result.rotation_rad:+.6f}rad inliers={result.inlier_count}/{result.match_count} ({inlier_pct:.0f}%)"
            )

            xform = matching.to_resolve_transform(result, src_w, src_h, tl_w, tl_h)
            _diag(
                f"-> Resolve: Pan={xform['Pan']:+.3f} Tilt={xform['Tilt']:+.3f} "
                f"ZoomX={xform['ZoomX']:.6f}"
            )

            if self.fill_frame:
                clamped = matching.clamp_to_fill_frame(
                    xform, src_w, src_h, tl_w, tl_h, match_space=result.match_space,
                )
                if clamped != xform:
                    _diag(
                        f"   clamped to fill ({result.match_space}): "
                        f"Pan={clamped['Pan']:+.3f} Tilt={clamped['Tilt']:+.3f} "
                        f"ZoomX={clamped['ZoomX']:.6f}"
                    )
                xform = clamped

            try:
                import cv2 as _cv2
                import numpy as _np

                _src = _cv2.imread(src_png)
                _ref = _cv2.imread(ref_png)
                if _src is not None and _ref is not None:
                    _M = _np.array(
                        [
                            [result.scale * _np.cos(result.rotation_rad),
                             -result.scale * _np.sin(result.rotation_rad),
                             result.tx],
                            [result.scale * _np.sin(result.rotation_rad),
                             result.scale * _np.cos(result.rotation_rad),
                             result.ty],
                        ],
                        dtype=_np.float64,
                    )
                    _warp = _cv2.warpAffine(_src, _M, (_ref.shape[1], _ref.shape[0]))
                    _overlay = _cv2.addWeighted(_ref, 0.5, _warp, 0.5, 0.0)
                    _overlay_path = os.path.join(DEBUG_DIR, "overlay.png")
                    _cv2.imwrite(_overlay_path, _overlay)
                    _diag(f"wrote verification overlay -> {_overlay_path}")
            except Exception as _exc:
                _diag(f"overlay generation skipped: {_exc}")

            self.progress.emit("Writing transform to clip…")
            resolver.write_transform(src_item, xform["Pan"], xform["Tilt"], xform["ZoomX"])

            summary = (
                f"Applied  Pan {xform['Pan']:+.1f}  Tilt {xform['Tilt']:+.1f}  "
                f"Zoom {xform['ZoomX']:.4f}  ({result.inlier_count}/{result.match_count} inliers)"
            )
            self.done.emit(summary)

        except (resolver.ResolveError, matching.MatchingError,
                frame_extract.FFmpegNotFoundError, frame_extract.FrameExtractionError) as exc:
            _diag(f"FAILED: {exc}")
            self.failed.emit(str(exc))
        except Exception as exc:  # last-resort safety net so the panel never silently dies
            tb = traceback.format_exc()
            _diag(f"UNEXPECTED: {exc}\n{tb}")
            self.failed.emit(f"Unexpected error: {exc}")


GITHUB_URL = "https://github.com/itaki/ResolveMatchTransform"


class ResolveMatchTransformPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ResolveMatchTransform")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedWidth(340)

        self.source_track = QComboBox()
        self.ref_track = QComboBox()
        for n in range(1, 21):
            self.source_track.addItem(f"V{n}", n)
            self.ref_track.addItem(f"V{n}", n)
        self.source_track.setCurrentIndex(0)   # V1
        self.ref_track.setCurrentIndex(1)      # V2

        self.match_mode = QComboBox()
        self.match_mode.addItem("Standard",               matching.MatchMode.STANDARD)
        self.match_mode.addItem("Pixel-perfect (strict)", matching.MatchMode.PIXEL_PERFECT)
        self.match_mode.addItem("VFX / partial overlap",  matching.MatchMode.VFX)
        self.match_mode.setToolTip(
            "Standard: typical conform (same shot, different reframe/grade).\n"
            "Pixel-perfect: content is identical except color/grade — tight tolerance.\n"
            "VFX / partial overlap: localized changes (CG, comp) — rejects mismatched regions aggressively."
        )

        form = QFormLayout()
        form.addRow("Source track:", self.source_track)
        form.addRow("Reference track:", self.ref_track)
        form.addRow("Match precision:", self.match_mode)

        self.fill_frame = QCheckBox("Require image to fill frame")
        self.fill_frame.setChecked(False)
        self.fill_frame.setToolTip(
            "Clamp Zoom to at least 1.0 and constrain Pan/Tilt so the source's "
            "edges never pull inside the timeline canvas.\n"
            "Useful when the editor's reference framing peeks past unpainted "
            "edges of the source."
        )

        self.match_btn = QPushButton("Match Transform")
        self.match_btn.clicked.connect(self._on_match_clicked)

        self.reveal_btn = QPushButton("Reveal frames")
        self.reveal_btn.setToolTip(f"Open {DEBUG_DIR} in Finder")
        self.reveal_btn.clicked.connect(self._on_reveal_clicked)

        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #aaa;")

        github_lbl = QLabel(
            f'<a href="{GITHUB_URL}" '
            f'style="color:#555; text-decoration:none; font-size:10px;">GitHub ↗</a>'
        )
        github_lbl.setOpenExternalLinks(True)
        github_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.match_btn)
        btn_row.addWidget(self.reveal_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.fill_frame)
        layout.addLayout(btn_row)
        layout.addWidget(self.status)
        layout.addWidget(github_lbl)

        self._worker: MatchWorker | None = None

    def _on_match_clicked(self) -> None:
        src = self.source_track.currentData()
        ref = self.ref_track.currentData()
        if src == ref:
            self._set_status("Source and reference tracks must differ.", error=True)
            return
        mode = self.match_mode.currentData()
        fill = self.fill_frame.isChecked()
        self.match_btn.setEnabled(False)
        self._set_status("Starting…")
        self._worker = MatchWorker(src, ref, mode=mode, fill_frame=fill)
        self._worker.progress.connect(lambda m: self._set_status(m))
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(lambda: self.match_btn.setEnabled(True))
        self._worker.start()

    def _on_reveal_clicked(self) -> None:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        # `open <dir>` opens the folder in Finder on macOS.
        try:
            import subprocess
            subprocess.run(["open", DEBUG_DIR], check=False)
        except Exception as exc:
            self._set_status(f"Could not open Finder: {exc}", error=True)

    def _on_done(self, message: str) -> None:
        self._set_status(message, ok=True)

    def _on_failed(self, message: str) -> None:
        self._set_status(message, error=True)

    def _set_status(self, message: str, *, ok: bool = False, error: bool = False) -> None:
        if error:
            self.status.setStyleSheet("color: #ff6b6b;")
        elif ok:
            self.status.setStyleSheet("color: #6bff8a;")
        else:
            self.status.setStyleSheet("color: #aaa;")
        self.status.setText(message)


def _write_pid_file() -> None:
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def _remove_pid_file() -> None:
    # Only remove if it still points at us — another panel may have taken over.
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
        if pid == os.getpid():
            os.remove(PID_FILE)
    except (OSError, ValueError):
        pass


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ResolveMatchTransform")
    app.setApplicationDisplayName("ResolveMatchTransform")
    icon = make_app_icon()
    app.setWindowIcon(icon)

    _write_pid_file()
    atexit.register(_remove_pid_file)
    _diag(f"panel started, pid={os.getpid()}, wrote {PID_FILE}")

    panel = ResolveMatchTransformPanel()
    panel.setWindowIcon(icon)
    panel.show()
    panel.raise_()
    panel.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

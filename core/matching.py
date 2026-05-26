from dataclasses import dataclass

import cv2
import numpy as np


class MatchingError(RuntimeError):
    pass


class MatchMode:
    STANDARD = "standard"
    PIXEL_PERFECT = "pixel_perfect"   # content nearly identical except color/grade
    VFX = "vfx"                       # localized changes; reject outliers aggressively


class MatchSpace:
    CANVAS = "canvas"
    RAW = "raw"


# Per-mode tuning. Each entry is (nfeatures, lowe_ratio, ransac_threshold, max_iters).
# estimateAffinePartial2D only accepts RANSAC / LMEDS, so the variation across
# modes is in the params, not the estimator family.
_MODE_PARAMS = {
    MatchMode.STANDARD:      (2000, 0.75, 3.0, 2000),
    MatchMode.PIXEL_PERFECT: (2000, 0.70, 1.5, 2000),
    MatchMode.VFX:           (4000, 0.65, 1.5, 10000),
}


@dataclass
class MatchResult:
    """Transform that warps the SOURCE frame onto the REFERENCE frame.

    After normalization (see _normalize_ref_to_src_canvas), tx/ty are expressed
    in SOURCE-clip pixel space and scale is a true reframe ratio (1.0 == no
    zoom). Rotation is in radians; the panel currently does not apply rotation
    to the clip.
    """
    scale: float
    rotation_rad: float
    tx: float
    ty: float
    inlier_count: int
    match_count: int
    ref_input_shape: tuple  # (h, w) of the raw reference frame read from disk
    src_input_shape: tuple  # (h, w) of the raw source frame read from disk
    canvas_shape: tuple     # (h, w) of the canvas matching ran in (= src shape)
    mode: str = MatchMode.STANDARD
    match_space: str = MatchSpace.CANVAS


def _fit_onto_canvas(img, canvas_w: int, canvas_h: int):
    """Render img onto a (canvas_h, canvas_w) black canvas with Resolve's
    default "Scale entire image to fit" behavior: preserve aspect, scale to
    fit within the canvas, center, letterbox / pillarbox as needed."""
    h, w = img.shape[:2]
    if (h, w) == (canvas_h, canvas_w):
        return img, 1.0, 0, 0
    fit = min(canvas_w / float(w), canvas_h / float(h))
    new_w = max(1, int(round(w * fit)))
    new_h = max(1, int(round(h * fit)))
    interp = cv2.INTER_AREA if fit < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(img, (new_w, new_h), interpolation=interp)
    canvas = np.zeros((canvas_h, canvas_w), dtype=img.dtype)
    ox = (canvas_w - new_w) // 2
    oy = (canvas_h - new_h) // 2
    canvas[oy:oy + new_h, ox:ox + new_w] = resized
    return canvas, fit, ox, oy


def _aspect_mismatch(ref_shape: tuple[int, int], src_shape: tuple[int, int]) -> bool:
    """Detect portrait-vs-landscape cases where letterboxing hides too much detail."""
    ref_h, ref_w = ref_shape
    src_h, src_w = src_shape
    ref_aspect = ref_w / float(ref_h)
    src_aspect = src_w / float(src_h)
    return max(ref_aspect, src_aspect) / min(ref_aspect, src_aspect) >= 1.7


def compute_transform(
    ref_img_path: str,
    src_img_path: str,
    canvas_size: tuple[int, int],
    mode: str = MatchMode.STANDARD,
    downscale: float = 0.25,
) -> MatchResult:
    """Match a reference frame to a source frame in the timeline's canvas space.

    canvas_size is (canvas_w, canvas_h) — pass the timeline resolution so both
    images are fit-scaled the same way Resolve renders them (preserving aspect,
    centered with black bars when needed). The returned tx/ty/scale are in
    canvas pixel space; convert to Resolve Pan/Tilt/Zoom via
    to_resolve_transform.
    """
    ref = cv2.imread(ref_img_path, cv2.IMREAD_GRAYSCALE)
    src = cv2.imread(src_img_path, cv2.IMREAD_GRAYSCALE)
    if ref is None:
        raise MatchingError(f"Could not read reference frame: {ref_img_path}")
    if src is None:
        raise MatchingError(f"Could not read source frame: {src_img_path}")

    ref_input_shape = ref.shape[:2]
    src_input_shape = src.shape[:2]
    canvas_w, canvas_h = canvas_size

    canvas_shape = (canvas_h, canvas_w)
    if _aspect_mismatch(ref_input_shape, src_input_shape):
        # For vertical references cut from wide source media, matching the raw
        # frames avoids Resolve's letterboxed source canvas, where the useful
        # image area can shrink to a narrow strip.
        ref_match = ref
        src_match = src
        match_space = MatchSpace.RAW
    else:
        src_canvas, _src_fit, _src_ox, _src_oy = _fit_onto_canvas(src, canvas_w, canvas_h)
        ref_canvas, _ref_fit, _ref_ox, _ref_oy = _fit_onto_canvas(ref, canvas_w, canvas_h)
        ref_match = ref_canvas
        src_match = src_canvas
        match_space = MatchSpace.CANVAS

    if mode not in _MODE_PARAMS:
        raise MatchingError(f"Unknown match mode: {mode!r}")
    nfeatures, lowe_ratio, ransac_thresh, ransac_iters = _MODE_PARAMS[mode]
    if match_space == MatchSpace.RAW:
        nfeatures = max(nfeatures, 5000)
        ransac_iters = max(ransac_iters, 10000)

    ref_small = cv2.resize(ref_match, None, fx=downscale, fy=downscale, interpolation=cv2.INTER_AREA)
    src_small = cv2.resize(src_match, None, fx=downscale, fy=downscale, interpolation=cv2.INTER_AREA)

    orb = cv2.ORB_create(nfeatures=nfeatures)
    kp_ref, des_ref = orb.detectAndCompute(ref_small, None)
    kp_src, des_src = orb.detectAndCompute(src_small, None)
    if des_ref is None or des_src is None or len(kp_ref) < 10 or len(kp_src) < 10:
        raise MatchingError("Not enough features detected — check that the clips share content.")

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = bf.knnMatch(des_ref, des_src, k=2)
    good = [m for pair in knn if len(pair) == 2 for m, n in [pair] if m.distance < lowe_ratio * n.distance]
    if len(good) < 10:
        raise MatchingError(f"Not enough feature matches ({len(good)}). Are these the same shot?")

    pts_ref = np.float32([kp_ref[m.queryIdx].pt for m in good])
    pts_src = np.float32([kp_src[m.trainIdx].pt for m in good])

    # Estimate the transform that maps SOURCE points onto REFERENCE points.
    M, inliers = cv2.estimateAffinePartial2D(
        pts_src, pts_ref,
        method=cv2.RANSAC,
        ransacReprojThreshold=ransac_thresh,
        maxIters=ransac_iters,
    )
    if M is None:
        raise MatchingError("Transform estimation failed — RANSAC found no valid solution.")

    # Lift translation back to full-resolution reference pixel space.
    M[0, 2] /= downscale
    M[1, 2] /= downscale

    scale = float(np.sqrt(M[0, 0] ** 2 + M[1, 0] ** 2))
    rotation_rad = float(np.arctan2(M[1, 0], M[0, 0]))
    tx = float(M[0, 2])
    ty = float(M[1, 2])
    inlier_count = int(inliers.sum()) if inliers is not None else 0

    return MatchResult(
        scale=scale,
        rotation_rad=rotation_rad,
        tx=tx,
        ty=ty,
        inlier_count=inlier_count,
        match_count=len(good),
        ref_input_shape=ref_input_shape,
        src_input_shape=src_input_shape,
        canvas_shape=canvas_shape,
        mode=mode,
        match_space=match_space,
    )


def to_resolve_transform(
    result: MatchResult,
    source_width: int,
    source_height: int,
    timeline_width: int,
    timeline_height: int,
) -> dict:
    """Convert a canvas-space MatchResult into Resolve Pan/Tilt/Zoom.

    Resolve's transform pipeline:
      source pixel (x,y)
        --[Resolve transform around anchor=center, in source px]-->
        --[fit-scale onto timeline canvas, centered]--> canvas pixel

    Given the matcher's (tx_c, ty_c, scale_c) in canvas pixel space, we solve
    for the (Pan, Tilt, Zoom) that lands the transformed source at the matched
    position on the canvas. For the common case where source == timeline
    resolution this reduces to Pan = tx + cx*(zoom-1), Tilt = cy*(1-zoom) - ty.
    """
    sW, sH = float(source_width), float(source_height)
    tW, tH = float(timeline_width), float(timeline_height)
    fit = min(tW / sW, tH / sH)
    ox = (tW - sW * fit) / 2.0
    oy = (tH - sH * fit) / 2.0

    zoom = result.scale
    tx = result.tx
    ty = result.ty

    if result.match_space == MatchSpace.RAW:
        # Aspect-mismatched cases (most commonly landscape source on a vertical
        # timeline). Resolve's "Mismatched Resolution = fill frame with crop"
        # uniformly scales the source by max(tW/sW, tH/sH) and centers it,
        # cropping along the binding-fit axis's perpendicular.
        s_fit_x = tW / sW
        s_fit_y = tH / sH
        fit = max(s_fit_x, s_fit_y)
        ox = (tW - sW * fit) / 2.0
        oy = (tH - sH * fit) / 2.0
        ref_h, ref_w = result.ref_input_shape
        ref_fit = min(tW / float(ref_w), tH / float(ref_h))
        ref_ox = (tW - float(ref_w) * ref_fit) / 2.0
        ref_oy = (tH - float(ref_h) * ref_fit) / 2.0
        zoom = result.scale * ref_fit / fit
        tx = result.tx * ref_fit + ref_ox
        ty = result.ty * ref_fit + ref_oy
        pan_src = (tx - ox) / fit + (sW / 2.0) * (zoom - 1.0)
        tilt_src = (sH / 2.0) * (1.0 - zoom) - (ty - oy) / fit
        # Empirically: Resolve interprets Pan/Tilt in source pixels along the
        # FIT axis (where source dimension scales naturally), but along the
        # CROPPED axis it expresses the value at the timeline-to-source ratio
        # for that axis. Calibrated against pixel-perfect ground-truth cases.
        if s_fit_y >= s_fit_x:
            pan = pan_src * s_fit_x
            tilt = tilt_src
        else:
            pan = pan_src
            tilt = tilt_src * s_fit_y
        return {"Pan": pan, "Tilt": tilt, "ZoomX": zoom, "ZoomY": zoom}

    pan = (ox * (zoom - 1.0) + tx) / fit + (sW / 2.0) * (zoom - 1.0)
    tilt = (sH / 2.0) * (1.0 - zoom) - oy * (zoom - 1.0) / fit - ty / fit
    return {"Pan": pan, "Tilt": tilt, "ZoomX": zoom, "ZoomY": zoom}


def clamp_to_fill_frame(
    xform: dict,
    source_width: int,
    source_height: int,
    timeline_width: int,
    timeline_height: int,
    match_space: str = MatchSpace.CANVAS,
) -> dict:
    """Clamp Pan/Tilt/Zoom so the source fully covers the timeline canvas.

    Two cases that mirror to_resolve_transform's two pipelines:

    CANVAS (Resolve letterbox / "Scale entire image to fit"):
      The source is scaled to fit inside the canvas with `min(fit_x, fit_y)`,
      so it starts letterboxed and must zoom in to cover both axes.
        zoom_min = max(fit_x, fit_y) / min(fit_x, fit_y)
        |Pan|  <= (sW*zoom - tW/fit) / 2     in source pixels
        |Tilt| <= (sH*zoom - tH/fit) / 2     in source pixels

    RAW (Resolve crop-fill / "Scale full frame with crop"):
      The source is scaled with `max(fit_x, fit_y)` and already covers the
      canvas at zoom = 1; one axis is bound to the canvas and the other has
      cropped slack. Pan/Tilt along the cropped axis are expressed in
      timeline-scaled units to match to_resolve_transform's RAW convention.

    In both cases, at zoom_min both Pan and Tilt are forced to 0 along the
    binding axis — Pan/Tilt can only move once there's slack to move within.
    """
    sW, sH = float(source_width), float(source_height)
    tW, tH = float(timeline_width), float(timeline_height)
    fit_x = tW / sW
    fit_y = tH / sH

    if match_space == MatchSpace.RAW:
        fit = max(fit_x, fit_y)
        zoom_min = 1.0
        zoom = max(float(xform["ZoomX"]), zoom_min)
        pan_src_limit = max(0.0, (sW * zoom - tW / fit) / 2.0)
        tilt_src_limit = max(0.0, (sH * zoom - tH / fit) / 2.0)
        # Match to_resolve_transform's axis-dependent Pan/Tilt scaling.
        if fit_y >= fit_x:
            pan_limit = pan_src_limit * fit_x
            tilt_limit = tilt_src_limit
        else:
            pan_limit = pan_src_limit
            tilt_limit = tilt_src_limit * fit_y
    else:
        fit = min(fit_x, fit_y)
        zoom_min = max(fit_x, fit_y) / fit
        zoom = max(float(xform["ZoomX"]), zoom_min)
        pan_limit = max(0.0, (sW * zoom - tW / fit) / 2.0)
        tilt_limit = max(0.0, (sH * zoom - tH / fit) / 2.0)

    pan = max(-pan_limit, min(pan_limit, float(xform["Pan"])))
    tilt = max(-tilt_limit, min(tilt_limit, float(xform["Tilt"])))

    return {"Pan": pan, "Tilt": tilt, "ZoomX": zoom, "ZoomY": zoom}

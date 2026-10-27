#!/usr/bin/env python3
"""
QuickBlink - lightweight astronomical image sequence triage tool.

Design goals:
- Read FITS and XISF sequences.
- Generate medium-resolution, 8-bit JPEG previews using a robust
  PixInsight-inspired auto-stretch.
- Provide a fast keyboard-driven blink/triage GUI.
- Save a portable .qblink ZIP containing:
      QuickBlink.py
      session.json
      previews/*.jpg
  The original full-resolution science files are deliberately NOT copied
  into the session unless --include-originals is requested.
- Export the non-discarded filenames to a text/JSON file suitable for
  registration and stacking.
- Apply keep/discard decisions back to the original files on disk, once
  reviewed (see `apply_session` / the `apply` CLI command).

Dependencies:
    numpy
    astropy
    pillow
    tkinter (normally included with Python on Windows)
    xisf              # only required for XISF input

Example:
    python QuickBlink.py create "C:/data/calibrated" --output session.qblink
    python QuickBlink.py open session.qblink
    python QuickBlink.py export session.qblink selected.txt
    python QuickBlink.py apply session.qblink --keep-dir "C:/data/BLINKED" --discard-dir "C:/data/REJECTED"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageTk, ImageOps
from astropy.io import fits

try:
    from xisf import XISF
except ImportError:
    XISF = None

SUPPORTED = {".fits", ".fit", ".fts", ".xisf"}
SCRIPT_NAME = "QuickBlink.py"


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def load_fits(path: Path) -> np.ndarray:
    with fits.open(path, memmap=False) as hdul:
        # Use the first HDU containing image data.
        for hdu in hdul:
            if isinstance(hdu.data, np.ndarray):
                data = np.asarray(hdu.data)
                break
        else:
            raise ValueError(f"No image HDU found in {path}")

    return normalize_image_shape(data)


def load_xisf(path: Path) -> np.ndarray:
    if XISF is None:
        raise RuntimeError(
            "XISF support requires the 'xisf' package. "
            "Install it with: pip install xisf"
        )

    data = XISF.read(str(path), n=0)
    return normalize_image_shape(np.asarray(data))


def normalize_image_shape(data: np.ndarray) -> np.ndarray:
    """Return an image as float32 HxW or HxWxC."""
    data = np.squeeze(np.asarray(data))

    if data.ndim == 2:
        return data.astype(np.float32, copy=False)

    if data.ndim == 3:
        # Common RGB layouts:
        # H,W,C or C,H,W
        if data.shape[-1] in (3, 4):
            return data[..., :3].astype(np.float32, copy=False)
        if data.shape[0] in (3, 4):
            return np.moveaxis(data[:3], 0, -1).astype(np.float32, copy=False)

        # For unusual cubes, use the first plane.
        return data[0].astype(np.float32, copy=False)

    raise ValueError(f"Unsupported image dimensions: {data.shape}")


def load_image(path: Path) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix in {".fits", ".fit", ".fts"}:
        return load_fits(path)
    if suffix == ".xisf":
        return load_xisf(path)
    raise ValueError(f"Unsupported image type: {path}")


# ---------------------------------------------------------------------------
# Auto-stretch
# ---------------------------------------------------------------------------

def finite_sample(data: np.ndarray, max_samples: int = 1_000_000) -> np.ndarray:
    """
    Return a representative finite sample.

    Sampling is deterministic so two previews of the same frame receive
    exactly the same stretch.
    """
    if data.ndim == 3:
        data = np.mean(data, axis=2)

    flat = np.asarray(data, dtype=np.float32).ravel()
    flat = flat[np.isfinite(flat)]

    if flat.size <= max_samples:
        return flat

    rng = np.random.default_rng(0)
    idx = rng.choice(flat.size, size=max_samples, replace=False)
    return flat[idx]


def mtf(m: float, x: np.ndarray) -> np.ndarray:
    """
    PixInsight's Midtones Transfer Function:
        mtf(m, x) = (m - 1) x / ((2m - 1) x - m)

    x=0 -> 0, x=1 -> 1, x=m -> 0.5 are handled as exact special cases to
    avoid the 0/0 indeterminate form the general formula hits there.

    Useful self-inverse property this relies on: mtf(a, b) == c implies
    mtf(c, b) == a. That's what lets _stretch_params() below compute the
    midtones balance directly (as mtf(target_background, normalized_median))
    instead of numerically solving for it.
    """
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
    out = np.empty_like(x)

    zeros = x <= 0.0
    ones = x >= 1.0
    halfs = np.isclose(x, m)
    others = ~(zeros | ones | halfs)

    out[zeros] = 0.0
    out[ones] = 1.0
    out[halfs] = 0.5
    denom = (2.0 * m - 1.0) * x[others] - m
    out[others] = (m - 1.0) * x[others] / denom
    return out


def _stretch_params(
    luminance: np.ndarray, shadows_clip: float = -1.25, target_background: float = 0.25
) -> tuple[float, float, float]:
    """
    Shadows clipping point (c0) and midtones balance (m) for a PixInsight
    AutoSTF-style stretch, plus the max value luminance was normalized by.

    c0 is a hard clip at `shadows_clip` mean-absolute-deviations below the
    median (PixInsight's default -1.25); m is chosen so that the median
    lands at `target_background` (PixInsight's default 0.25) after the
    stretch. Both are computed from a deterministic finite sample for speed
    on large frames; max_val is computed from the full array since a single
    max() pass is cheap even at full resolution and, unlike the median,
    benefits from seeing every pixel (a subsample can miss the true peak).
    """
    finite = luminance[np.isfinite(luminance)]
    if finite.size == 0:
        return 1.0, 0.5, 1.0

    max_val = float(np.max(finite))
    if max_val <= 0:
        return 1.0, 0.5, 1.0

    sample = finite_sample(luminance) / max_val
    median = float(np.median(sample))
    avg_dev = float(np.mean(np.abs(sample - median)))

    c0 = float(np.clip(median + shadows_clip * avg_dev, 0.0, 1.0))
    if c0 >= 1.0:
        return c0, 0.5, max_val

    m = float(mtf(target_background, np.array([(median - c0) / (1.0 - c0)]))[0])
    return c0, m, max_val


def auto_stretch(data: np.ndarray) -> np.ndarray:
    """
    PixInsight AutoSTF-style stretch: a hard shadows clip followed by a
    midtones transfer function, matching PixInsight's default
    ScreenTransferFunction auto-stretch with "RGB linked" statistics --
    computed once from luminance, then applied identically to every
    channel so color balance isn't distorted.
    """
    data = np.asarray(data, dtype=np.float32)

    if data.ndim == 3:
        luminance = (
            0.2126 * data[..., 0]
            + 0.7152 * data[..., 1]
            + 0.0722 * data[..., 2]
        )
    else:
        luminance = data

    c0, m, max_val = _stretch_params(luminance)
    if max_val <= 0:
        return np.zeros_like(data, dtype=np.float32)

    x = np.clip(data.astype(np.float64) / max_val, 0.0, 1.0)
    if c0 >= 1.0:
        return np.zeros_like(data, dtype=np.float32)

    below = x < c0
    result = np.zeros_like(x)
    result[~below] = mtf(m, (x[~below] - c0) / (1.0 - c0))

    return np.clip(result, 0.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Preview generation
# ---------------------------------------------------------------------------

def compute_crop_box(
    size: tuple[int, int], center: tuple[float, float], crop_fraction: float
) -> tuple[int, int, int, int]:
    """
    A (left, top, right, bottom) box covering `crop_fraction` of `size`,
    centered on the normalized (fx, fy) point `center`, clamped to stay
    fully within the image -- used by the GUI's zoom panel to crop around
    wherever the main image was last clicked.
    """
    w, h = size
    crop_w = max(1, int(w * crop_fraction))
    crop_h = max(1, int(h * crop_fraction))
    cx = int(center[0] * w)
    cy = int(center[1] * h)
    left = max(0, min(w - crop_w, cx - crop_w // 2))
    top = max(0, min(h - crop_h, cy - crop_h // 2))
    return left, top, left + crop_w, top + crop_h


def downsample_linear(data: np.ndarray, max_size: int) -> np.ndarray:
    """
    Downsample still-linear (unstretched) pixel data to roughly max_size on
    its longest side, using a fast bilinear resize.

    This is the dominant speed lever for large frames (6000x4000+ is common):
    auto_stretch's per-pixel work, and the final resize in
    downsample_preview, both cost roughly (longest_side)^2. Shrinking first
    means that cost is paid on ~max_size^2 pixels instead of the full
    sensor resolution -- a 6+x reduction is typical. Order (downsample then
    stretch, vs. the reverse) makes a barely-perceptible visual difference
    for a quick triage preview, which is the trade this function makes.
    """
    h, w = data.shape[:2]
    if max(h, w) <= max_size:
        return data

    scale = max_size / max(h, w)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))

    if data.ndim == 2:
        img = Image.fromarray(np.ascontiguousarray(data, dtype=np.float32))
        return np.asarray(img.resize((new_w, new_h), Image.Resampling.BILINEAR), dtype=np.float32)

    channels = [
        np.asarray(
            Image.fromarray(np.ascontiguousarray(data[..., c], dtype=np.float32))
            .resize((new_w, new_h), Image.Resampling.BILINEAR),
            dtype=np.float32,
        )
        for c in range(data.shape[2])
    ]
    return np.stack(channels, axis=-1)


def downsample_preview(stretched: np.ndarray, max_size: int) -> Image.Image:
    if stretched.ndim == 2:
        arr = np.clip(stretched * 255.0 + 0.5, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)
    else:
        arr = np.clip(stretched * 255.0 + 0.5, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return img


def make_preview(source: Path, destination: Path, max_size: int = 2000) -> dict:
    data = load_image(source)
    # Downsample before stretching, with a little headroom (1.2x) so the
    # final LANCZOS resize in downsample_preview still has room to produce
    # a clean result rather than being a pure pass-through.
    small = downsample_linear(data, round(max_size * 1.2))
    stretched = auto_stretch(small)
    img = downsample_preview(stretched, max_size)

    destination.parent.mkdir(parents=True, exist_ok=True)

    # JPEG, not WebP: measured ~5x faster to *load* than WebP at a similar
    # file size (Pillow decodes WebP through its animation-capable path
    # even for a single still frame, which is surprisingly slow). Since
    # every blink-through navigation re-opens this file, load speed matters
    # far more here than the marginal size difference. The sensor noise
    # already present in every real frame masks JPEG's blocking artifacts
    # at quality=90 -- confirmed visually, not just assumed. The scientific
    # data is never represented by the preview; it is only for visual
    # triage.
    img.save(destination, "JPEG", quality=90)

    return {
        "width": int(data.shape[1]),
        "height": int(data.shape[0]),
        "channels": int(data.shape[2]) if data.ndim == 3 else 1,
        "preview_width": img.width,
        "preview_height": img.height,
    }


# ---------------------------------------------------------------------------
# Session creation
# ---------------------------------------------------------------------------

# Subfolders holding frames already through a prior review/rejection stage
# (PixInsight's own SubframeSelector conventions, plus this tool's own
# BLINKED/REJECTED). Excluded by default so repeatedly pointing QuickBlink
# at a whole CALIBRATED/<target>/ directory only ever reviews new frames.
DEFAULT_EXCLUDE_DIR_NAMES = {"blinked", "rejected", "measurementdata", "rejected_measured"}


def discover_images(
    root: Path, exclude_dir_names: set[str] | None = None
) -> list[Path]:
    exclude = {n.lower() for n in (exclude_dir_names if exclude_dir_names is not None else DEFAULT_EXCLUDE_DIR_NAMES)}
    return sorted(
        p for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED
        and not any(part.lower() in exclude for part in p.relative_to(root).parts[:-1])
    )


def relative_name(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def create_session(
    input_dir: Path,
    output_zip: Path,
    preview_size: int = 2000,
    include_originals: bool = False,
    exclude_dir_names: set[str] | None = None,
) -> None:
    input_dir = input_dir.resolve()
    output_zip = output_zip.resolve()

    files = discover_images(input_dir, exclude_dir_names=exclude_dir_names)
    if not files:
        raise RuntimeError(f"No FITS/XISF images found in {input_dir}")

    with tempfile.TemporaryDirectory(prefix="quickblink_") as tmp:
        tmpdir = Path(tmp)
        preview_dir = tmpdir / "previews"
        originals_dir = tmpdir / "originals"

        records = []

        for i, source in enumerate(files):
            print(f"[{i + 1}/{len(files)}] Previewing {source.name}")
            preview_name = f"{i:06d}.jpg"

            try:
                stats = make_preview(
                    source,
                    preview_dir / preview_name,
                    max_size=preview_size,
                )
                error = None
            except Exception as exc:
                print(f"  ERROR: {exc}")
                stats = {}
                error = repr(exc)

            record = {
                "index": i,
                "filename": source.name,
                "relative_path": relative_name(source, input_dir),
                "preview": f"previews/{preview_name}",
                "discarded": False,
                "error": error,
                **stats,
            }

            # Optional portable mode: include the original image. This can
            # make the session enormous, so it is off by default.
            if include_originals:
                dest = originals_dir / relative_name(source, input_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
                record["original"] = f"originals/{dest.relative_to(originals_dir).as_posix()}"

            records.append(record)

        session = {
            "format": "QuickBlink",
            "version": 1,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_directory": str(input_dir),
            "image_count": len(records),
            "preview_size": preview_size,
            "includes_originals": include_originals,
            "images": records,
        }

        (tmpdir / "session.json").write_text(
            json.dumps(session, indent=2),
            encoding="utf-8",
        )

        # Embed this launcher in the portable session.
        shutil.copy2(Path(__file__).resolve(), tmpdir / SCRIPT_NAME)

        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(
            output_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as zf:
            for file in tmpdir.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(tmpdir).as_posix())

    print(f"\nCreated QuickBlink session: {output_zip}")
    print(f"Images: {len(files)}")


# ---------------------------------------------------------------------------
# Session extraction / loading
# ---------------------------------------------------------------------------

def is_qblink(path: Path) -> bool:
    return path.suffix.lower() == ".qblink"


def extract_session(qblink: Path) -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory(prefix="quickblink_session_")
    root = Path(tmp.name)

    with zipfile.ZipFile(qblink, "r") as zf:
        zf.extractall(root)

    session_path = root / "session.json"
    if not session_path.exists():
        tmp.cleanup()
        raise RuntimeError("Invalid QuickBlink session: session.json missing")

    return tmp, root


def load_session(path: Path) -> tuple[tempfile.TemporaryDirectory | None, Path, dict]:
    path = path.resolve()

    if path.is_dir():
        root = path
        tmp = None
    elif is_qblink(path):
        tmp, root = extract_session(path)
    elif path.name == "session.json":
        tmp = None
        root = path.parent
    else:
        raise ValueError(
            "Open a .qblink session, a QuickBlink session directory, "
            "or a session.json file."
        )

    session = json.loads((root / "session.json").read_text(encoding="utf-8"))
    return tmp, root, session


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class QuickBlinkGUI:
    def __init__(
        self,
        root,
        session_root: Path,
        session: dict,
        session_path: Path | None,
    ):
        import tkinter as tk
        from tkinter import messagebox

        self.tk = tk
        self.messagebox = messagebox

        self.root = root
        self.session_root = session_root
        self.session = session
        self.session_path = session_path
        self.images = session["images"]

        self.index = 0
        self.photo = None
        self.zoom_photo = None
        self.zoom_center = (0.5, 0.5)  # normalized (fx, fy) into the full preview
        self.thumbnail_photos = []

        self._build_ui()
        self._bind_keys()
        self._refresh()

    def _build_ui(self):
        tk = self.tk
        self.root.title("QuickBlink")
        self.root.geometry("1250x850")
        self.root.minsize(900, 650)

        main_frame = tk.Frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        # Main image. Click anywhere on it to move the zoom panel's crosshair
        # there -- e.g. onto a star, to check focus/roundness/trailing.
        self.image_label = tk.Label(
            main_frame,
            text="Loading...",
            bg="#111111",
            fg="white",
            anchor="center",
        )
        self.image_label.pack(side="left", fill="both", expand=True)
        self.image_label.bind("<Button-1>", self._on_image_click)

        # Zoom panel: a magnified crop of the full-resolution preview
        # (not the downscaled image shown on the left), centered on
        # zoom_center. Defaults to the frame center.
        zoom_frame = tk.Frame(main_frame, width=340)
        zoom_frame.pack(side="left", fill="y", padx=(8, 0))
        zoom_frame.pack_propagate(False)

        tk.Label(
            zoom_frame, text="Zoom (click image to move)", font=("Segoe UI", 9),
        ).pack(side="top", pady=(0, 4))

        self.zoom_label = tk.Label(
            zoom_frame,
            bg="#111111",
            fg="white",
            anchor="center",
        )
        self.zoom_label.pack(side="top", fill="both", expand=True)

        zoom_controls = tk.Frame(zoom_frame)
        zoom_controls.pack(side="bottom", fill="x", pady=(4, 0))
        tk.Button(
            zoom_controls, text="Reset", command=self._reset_zoom, width=10, takefocus=0,
        ).pack(side="left")

        # Status / controls.
        bottom = tk.Frame(self.root)
        bottom.pack(fill="x", padx=8, pady=4)

        self.status = tk.Label(
            bottom,
            text="",
            anchor="w",
            font=("Segoe UI", 10),
        )
        self.status.pack(side="left", fill="x", expand=True)

        # takefocus=0 on every button here: otherwise a mouse click leaves
        # that button holding keyboard focus, and a focused Button widget
        # intercepts Space/Return itself (triggering its OWN command)
        # before the global bindings below ever see the key -- which would
        # silently break the Space=discard / Enter=undo shortcuts as soon
        # as any button had been clicked.
        tk.Button(
            bottom,
            text="Previous",
            command=self.previous,
            width=10,
            takefocus=0,
        ).pack(side="left", padx=2)

        tk.Button(
            bottom,
            text="Next",
            command=self.next,
            width=10,
            takefocus=0,
        ).pack(side="left", padx=2)

        self.discard_button = tk.Button(
            bottom,
            text="Discard",
            command=self.toggle_discard,
            width=10,
            takefocus=0,
        )
        self.discard_button.pack(side="left", padx=2)

        tk.Button(
            bottom,
            text="Save",
            command=self.save_session,
            width=10,
            takefocus=0,
        ).pack(side="left", padx=2)

        tk.Button(
            bottom,
            text="Export",
            command=self.export_selected,
            width=10,
            takefocus=0,
        ).pack(side="left", padx=2)

        # Filmstrip.
        self.filmstrip = tk.Frame(self.root)
        self.filmstrip.pack(fill="x", padx=8, pady=(4, 8))

        self.thumb_canvas = tk.Canvas(
            self.filmstrip,
            height=105,
            highlightthickness=0,
        )
        self.thumb_scroll = tk.Scrollbar(
            self.filmstrip,
            orient="horizontal",
            command=self.thumb_canvas.xview,
        )
        self.thumb_canvas.configure(xscrollcommand=self.thumb_scroll.set)

        self.thumb_canvas.pack(fill="x", side="top")
        self.thumb_scroll.pack(fill="x", side="bottom")

        self.thumb_frame = tk.Frame(self.thumb_canvas)
        self.thumb_canvas.create_window(
            (0, 0),
            window=self.thumb_frame,
            anchor="nw",
        )
        self.thumb_frame.bind(
            "<Configure>",
            lambda e: self.thumb_canvas.configure(
                scrollregion=self.thumb_canvas.bbox("all")
            ),
        )

        self._build_thumbnails()

    def _bind_keys(self):
        # Every frame starts KEPT; Space is the one action that changes that
        # (Enter undoes it). Left/Right/Up/Down only move -- they never
        # touch discard state -- so blinking is a one-handed, eyes-on-image
        # rhythm: arrow past good frames, tap Space the instant something
        # looks off, keep going.
        self.root.bind("<Right>", lambda e: self.next())
        self.root.bind("<Up>", lambda e: self.next())
        self.root.bind("<Left>", lambda e: self.previous())
        self.root.bind("<Down>", lambda e: self.previous())
        self.root.bind("<space>", lambda e: self.mark_discard())
        self.root.bind("<Return>", lambda e: self.unmark_discard())
        self.root.bind("<d>", lambda e: self.mark_discard())
        self.root.bind("<u>", lambda e: self.unmark_discard())
        self.root.bind("<Control-s>", lambda e: self.save_session())
        self.root.bind("<Control-e>", lambda e: self.export_selected())
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def _build_thumbnails(self):
        tk = self.tk

        self.thumbnail_photos.clear()

        for i, record in enumerate(self.images):
            path = self.session_root / record["preview"]

            try:
                img = Image.open(path)
                img.thumbnail((120, 80), Image.Resampling.LANCZOS)
                img = ImageOps.contain(img.convert("RGB"), (120, 80))
                photo = ImageTk.PhotoImage(img)
                self.thumbnail_photos.append(photo)

                button = tk.Button(
                    self.thumb_frame,
                    image=photo,
                    relief="flat",
                    bd=2,
                    command=lambda n=i: self.goto(n),
                    takefocus=0,
                )
                button.grid(row=0, column=i, padx=2, pady=2)

                label = tk.Label(
                    self.thumb_frame,
                    text=str(i + 1),
                    font=("Segoe UI", 8),
                )
                label.grid(row=1, column=i)

                record["_thumb_button"] = button
                record["_thumb_label"] = label
            except Exception:
                pass

    ZOOM_CROP_FRACTION = 0.12  # fraction of the full preview's width/height shown zoomed

    def _load_photo(self, record):
        path = self.session_root / record["preview"]
        img = Image.open(path).convert("RGB")
        # Kept at full stored-preview resolution for the zoom panel to crop
        # from -- the panel to the left only ever shows a downscaled copy.
        self._current_preview_img = img

        w = max(self.image_label.winfo_width(), 800)
        h = max(self.image_label.winfo_height(), 600)

        display_img = ImageOps.contain(img, (w - 20, h - 20), Image.Resampling.LANCZOS)
        self._current_display_size = display_img.size
        return ImageTk.PhotoImage(display_img)

    def _on_image_click(self, event):
        """Move the zoom panel's crosshair to wherever the main image was
        clicked -- e.g. onto a star, to inspect it closely."""
        display_size = getattr(self, "_current_display_size", None)
        if self.photo is None or display_size is None:
            return

        disp_w, disp_h = display_size
        offset_x = (self.image_label.winfo_width() - disp_w) / 2
        offset_y = (self.image_label.winfo_height() - disp_h) / 2
        x = event.x - offset_x
        y = event.y - offset_y

        if 0 <= x <= disp_w and 0 <= y <= disp_h:
            self.zoom_center = (x / disp_w, y / disp_h)
            self._refresh_zoom()

    def _reset_zoom(self):
        self.zoom_center = (0.5, 0.5)
        self._refresh_zoom()

    def _load_zoom_photo(self):
        img = getattr(self, "_current_preview_img", None)
        if img is None:
            return None

        box = compute_crop_box(img.size, self.zoom_center, self.ZOOM_CROP_FRACTION)
        crop = img.crop(box)

        zoom_w = max(self.zoom_label.winfo_width(), 300)
        zoom_h = max(self.zoom_label.winfo_height(), 300)
        crop = ImageOps.contain(crop, (zoom_w - 10, zoom_h - 10), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(crop)

    def _refresh_zoom(self):
        self.zoom_photo = self._load_zoom_photo()
        if self.zoom_photo is not None:
            self.zoom_label.configure(image=self.zoom_photo, text="")

    def _refresh(self):
        if not self.images:
            return

        record = self.images[self.index]

        if record.get("error"):
            self.image_label.configure(
                image="",
                text=f"Could not generate preview:\n{record['error']}",
            )
            self._current_preview_img = None
            self.zoom_label.configure(image="", text="(no preview)")
        else:
            self.photo = self._load_photo(record)
            self.image_label.configure(image=self.photo, text="")
            # Deferred via after(): Tkinter doesn't repaint until the current
            # handler returns to the event loop, so scheduling the zoom
            # panel's crop/resize for the *next* idle tick -- instead of
            # doing it inline, right here -- lets the main image actually
            # appear on screen first, then the zoom panel fills in a moment
            # later, rather than both updating only once all the work is done.
            # zoom_center itself is deliberately NOT reset when navigating --
            # keeping the crosshair on the same star across frames is exactly
            # how you check that star for trailing/focus drift through a
            # sequence.
            self.root.after(1, self._refresh_zoom)

        discarded = record.get("discarded", False)
        state = "DISCARD" if discarded else "KEEP"

        kept = sum(not r.get("discarded", False) for r in self.images)
        discarded_n = len(self.images) - kept

        self.status.configure(
            text=(
                f"{self.index + 1}/{len(self.images)}   "
                f"{state}   |   "
                f"Keep: {kept}   Discard: {discarded_n}   |   "
                f"{record['filename']}"
            )
        )

        self.discard_button.configure(
            text="Keep" if discarded else "Discard"
        )

        # Visual state of thumbnails.
        for i, r in enumerate(self.images):
            button = r.get("_thumb_button")
            label = r.get("_thumb_label")
            if button is None:
                continue

            if i == self.index:
                button.configure(relief="solid", bd=3)
            else:
                button.configure(relief="flat", bd=2)

            label.configure(
                text=f"{i + 1}" + ("  X" if r.get("discarded") else "")
            )

    def goto(self, index: int):
        if 0 <= index < len(self.images):
            self.index = index
            self._refresh()

            # Keep selected thumbnail visible.
            self.root.after(
                10,
                lambda: self.thumb_canvas.xview_moveto(
                    max(
                        0,
                        min(
                            1,
                            (index / max(1, len(self.images) - 1)) - 0.05,
                        ),
                    )
                ),
            )

    def next(self):
        if self.index < len(self.images) - 1:
            self.index += 1
            self._refresh()

    def previous(self):
        if self.index > 0:
            self.index -= 1
            self._refresh()

    def mark_discard(self):
        self.images[self.index]["discarded"] = True
        self._refresh()

    def unmark_discard(self):
        self.images[self.index]["discarded"] = False
        self._refresh()

    def toggle_discard(self):
        self.images[self.index]["discarded"] = not self.images[
            self.index
        ].get("discarded", False)
        self._refresh()

    def save_session(self):
        if self.session_path is None:
            self.messagebox.showwarning(
                "Cannot save",
                "This session was opened from a directory/session.json.",
            )
            return

        self._write_qblink(self.session_path)
        self.status.configure(text="Saved QuickBlink session.")

    def _write_qblink(self, destination: Path):
        self.session["images"] = [
            {k: v for k, v in r.items() if not k.startswith("_")}
            for r in self.images
        ]

        with tempfile.TemporaryDirectory(prefix="quickblink_save_") as tmp:
            tmpdir = Path(tmp)

            # Copy the current extracted session to a fresh directory.
            for src in self.session_root.rglob("*"):
                if src.is_file():
                    dst = tmpdir / src.relative_to(self.session_root)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

            (tmpdir / "session.json").write_text(
                json.dumps(self.session, indent=2),
                encoding="utf-8",
            )

            with zipfile.ZipFile(
                destination,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as zf:
                for file in tmpdir.rglob("*"):
                    if file.is_file():
                        zf.write(
                            file,
                            file.relative_to(tmpdir).as_posix(),
                        )

    def export_selected(self):
        from tkinter import filedialog

        selected = [
            r["relative_path"]
            for r in self.images
            if not r.get("discarded", False)
        ]

        destination = filedialog.asksaveasfilename(
            title="Export selected images",
            defaultextension=".txt",
            filetypes=[
                ("Text file", "*.txt"),
                ("JSON file", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if not destination:
            return

        destination = Path(destination)

        if destination.suffix.lower() == ".json":
            payload = {
                "format": "QuickBlink selection",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "count": len(selected),
                "files": selected,
            }
            destination.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
        else:
            destination.write_text(
                "\n".join(selected) + "\n",
                encoding="utf-8",
            )

        self.messagebox.showinfo(
            "Export complete",
            f"Exported {len(selected)} selected images.",
        )


def open_gui(session_path: Path):
    import tkinter as tk

    tmp, root_dir, session = load_session(session_path)

    root = tk.Tk()

    gui = QuickBlinkGUI(
        root,
        root_dir,
        session,
        session_path if is_qblink(session_path) else None,
    )

    # Resize image when the window changes.
    def on_resize(_event):
        if gui.images:
            gui._refresh()

    root.bind("<Configure>", on_resize)

    try:
        root.mainloop()
    finally:
        if tmp is not None:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# Export without opening GUI
# ---------------------------------------------------------------------------

def export_session(session_path: Path, destination: Path):
    tmp, root_dir, session = load_session(session_path)

    try:
        selected = [
            r["relative_path"]
            for r in session["images"]
            if not r.get("discarded", False)
        ]

        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.suffix.lower() == ".json":
            destination.write_text(
                json.dumps(
                    {
                        "format": "QuickBlink selection",
                        "count": len(selected),
                        "files": selected,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        else:
            destination.write_text(
                "\n".join(selected) + "\n",
                encoding="utf-8",
            )

        print(f"Exported {len(selected)} selected images to {destination}")
    finally:
        if tmp is not None:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# Apply keep/discard decisions back to the original files
# ---------------------------------------------------------------------------

def _safe_move(src: Path, dest: Path) -> Path:
    """Move src to dest, appending _copyN on name collision. Self-contained
    (no SkyStack imports) so this file stays usable standalone."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        base, suffix, parent = dest.stem, dest.suffix, dest.parent
        i = 1
        while True:
            candidate = parent / f"{base}_copy{i}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    shutil.move(str(src), str(dest))
    return dest


def apply_session(
    session_path: Path,
    keep_dir: Path | None = None,
    discard_dir: Path | None = None,
    delete_discarded: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Apply a reviewed session's keep/discard decisions back to the original
    files on disk.

    Deliberately does NOT guess a destination convention -- the right place
    for kept/discarded files depends on which pipeline stage this session
    reviewed (e.g. raw LIGHT frames vs. a second post-calibration pass), and
    only the caller knows that. If neither keep_dir nor discard_dir/
    delete_discarded is given for a bucket, those files are left untouched
    (only the session.json decision exists).

    Returns a summary dict with counts of what was found and what moved.
    """
    if discard_dir is not None and delete_discarded:
        raise ValueError("Specify either discard_dir or delete_discarded, not both.")

    tmp, root_dir, session = load_session(session_path)
    try:
        source_directory = Path(session["source_directory"])
        summary = {
            "kept": 0,
            "discarded": 0,
            "missing": 0,
            "moved_kept": 0,
            "moved_discarded": 0,
            "deleted": 0,
        }

        for record in session["images"]:
            original = source_directory / record["relative_path"]
            discarded = record.get("discarded", False)
            summary["discarded" if discarded else "kept"] += 1

            if not original.exists():
                summary["missing"] += 1
                continue

            if not discarded:
                if keep_dir is not None:
                    if not dry_run:
                        _safe_move(original, Path(keep_dir) / original.name)
                    summary["moved_kept"] += 1
            else:
                if delete_discarded:
                    if not dry_run:
                        original.unlink()
                    summary["deleted"] += 1
                elif discard_dir is not None:
                    if not dry_run:
                        _safe_move(original, Path(discard_dir) / original.name)
                    summary["moved_discarded"] += 1

        return summary
    finally:
        if tmp is not None:
            tmp.cleanup()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="QuickBlink astronomical image sequence triage tool."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser(
        "create",
        help="Create a portable .qblink session from a directory.",
    )
    create.add_argument("input_dir", type=Path)
    create.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Output .qblink file.",
    )
    create.add_argument(
        "--preview-size",
        type=int,
        default=2000,
        help="Maximum preview dimension in pixels (default: 2000).",
    )
    create.add_argument(
        "--include-originals",
        action="store_true",
        help="Include original FITS/XISF files in the session ZIP.",
    )
    create.add_argument(
        "--exclude-dir",
        action="append",
        dest="exclude_dirs",
        default=None,
        metavar="NAME",
        help=(
            "Subfolder name to skip when discovering images (repeatable). "
            f"Default: {sorted(DEFAULT_EXCLUDE_DIR_NAMES)}."
        ),
    )

    open_cmd = sub.add_parser(
        "open",
        help="Open a QuickBlink session.",
    )
    open_cmd.add_argument("session", type=Path)

    export = sub.add_parser(
        "export",
        help="Export non-discarded filenames.",
    )
    export.add_argument("session", type=Path)
    export.add_argument("output", type=Path)

    apply_cmd = sub.add_parser(
        "apply",
        help="Apply keep/discard decisions back to the original files.",
    )
    apply_cmd.add_argument("session", type=Path)
    apply_cmd.add_argument(
        "--keep-dir", type=Path, default=None,
        help="Move kept (non-discarded) files here. Left in place if omitted.",
    )
    apply_cmd.add_argument(
        "--discard-dir", type=Path, default=None,
        help="Move discarded files here (recoverable). Left in place if omitted.",
    )
    apply_cmd.add_argument(
        "--delete-discarded", action="store_true",
        help="Permanently delete discarded files instead of moving them.",
    )
    apply_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Report what would happen without moving or deleting anything.",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "create":
        create_session(
            args.input_dir,
            args.output,
            preview_size=args.preview_size,
            include_originals=args.include_originals,
            exclude_dir_names=set(args.exclude_dirs) if args.exclude_dirs else None,
        )
    elif args.command == "open":
        open_gui(args.session)
    elif args.command == "export":
        export_session(args.session, args.output)
    elif args.command == "apply":
        summary = apply_session(
            args.session,
            keep_dir=args.keep_dir,
            discard_dir=args.discard_dir,
            delete_discarded=args.delete_discarded,
            dry_run=args.dry_run,
        )
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

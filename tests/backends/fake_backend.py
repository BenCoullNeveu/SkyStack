"""In-memory test double implementing ProcessingBackend.

Used to validate the ProcessingBackend contract independent of any real
stacking engine. Deliberately simple (mean-stack, naive dark/flat
correction) -- it exists to prove the interface shape works end-to-end, not
to produce astronomically correct results.
"""
from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

import numpy as np
from astropy.io import fits


class FakeBackend:
    def __init__(self):
        self.started = False
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        self.started = True

    def stop(self) -> None:
        self.stop_calls += 1
        self.started = False

    def stack(self, files: List[Path], output_path: Path, **params) -> Path:
        arrays = [self._read(f) for f in files]
        stacked = np.mean(np.stack(arrays), axis=0).astype(np.float32)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fits.PrimaryHDU(data=stacked).writeto(output_path, overwrite=True)
        return output_path

    def calibrate(
        self,
        lights: List[Path],
        flat: Optional[Path],
        dark: Optional[Path],
        output_dir: Path,
        **params,
    ) -> List[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        flat_data = self._read(flat) if flat else None
        dark_data = self._read(dark) if dark else None

        outputs = []
        for light in lights:
            data = self._read(light)
            if dark_data is not None:
                data = data - dark_data
            if flat_data is not None:
                norm = flat_data / np.mean(flat_data)
                data = data / norm
            out_path = output_dir / f"{light.stem}_c.fits"
            fits.PrimaryHDU(data=data.astype(np.float32)).writeto(
                out_path, overwrite=True
            )
            outputs.append(out_path)
        return outputs

    def register(
        self,
        lights: List[Path],
        reference: Path,
        output_dir: Path,
        *,
        postfix: str = "_r",
        **params,
    ) -> Dict[Path, Path]:
        # Identity "registration": a copy under the registered name.
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = {}
        for light in lights:
            out = output_dir / f"{Path(light).stem}{postfix}{Path(light).suffix}"
            shutil.copy2(light, out)
            outputs[Path(light)] = out
        return outputs

    def integrate(
        self,
        groups: List[Dict[str, Any]],
        work_dir: Path,
        **params,
    ) -> List[Dict[str, Any]]:
        results = []
        for g in groups:
            try:
                out = self.stack([Path(f) for f in g["files"]], Path(g["output_path"]))
                results.append({"name": g["name"], "status": "ok",
                                "output_path": str(out), "n_frames": len(g["files"])})
            except Exception as e:  # mirror the real backend's per-group isolation
                results.append({"name": g["name"], "status": "error", "error": str(e)})
        return results

    def postprocess(
        self,
        items: List[Dict[str, Any]],
        steps: List[Dict[str, Any]],
        **params,
    ) -> List[Dict[str, Any]]:
        # Copies each input, recording the applied steps in the header.
        results = []
        for item in items:
            src, out = Path(item["input"]), Path(item["output"])
            try:
                with fits.open(src, memmap=False) as hdul:
                    hdr, data = hdul[0].header.copy(), np.asarray(hdul[0].data)
                hdr["POSTPROC"] = ",".join(s["name"] for s in steps)
                out.parent.mkdir(parents=True, exist_ok=True)
                fits.PrimaryHDU(data=data, header=hdr).writeto(out, overwrite=True)
                results.append({"input": str(src), "status": "ok", "output": str(out)})
            except Exception as e:
                results.append({"input": str(src), "status": "error", "error": str(e)})
        return results

    def measure(self, frames: List[Path], **params) -> Dict[Path, Dict[str, Any]]:
        # Image statistics are real; star metrics come from FAKE* header
        # keywords so tests can dictate them (defaults: an unremarkable frame).
        out = {}
        for f in frames:
            with fits.open(f, memmap=False) as hdul:
                hdr, data = hdul[0].header, np.asarray(hdul[0].data, dtype=np.float64)
            out[Path(f)] = {
                "fwhm": float(hdr.get("FAKEFWHM", 2.5)),
                "eccentricity": float(hdr.get("FAKEECC", 0.4)),
                "stars": int(hdr.get("FAKESTAR", 500)),
                "psf_signal_weight": float(hdr.get("FAKEPSFW", 1.0)),
                "psf_snr": float(hdr.get("FAKEPSNR", 1.0)),
                "median": float(np.median(data)),
                "noise": float(np.std(data)),
            }
        return out

    @staticmethod
    def _read(path: Path) -> np.ndarray:
        with fits.open(path, memmap=False) as hdul:
            return np.array(hdul[0].data, dtype=np.float64)

"""Tests for skystack/platesolve.py. ASTAP itself is never run: subprocess
is monkeypatched to write the .ini sidecar ASTAP would have written."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import numpy as np
from astropy.io import fits

import skystack.platesolve as ps

SOLVED_INI = """PLTSOLVD=T
CRPIX1=200.5
CRPIX2=150.5
CRVAL1=67.6958
CRVAL2=54.7747
CD1_1=-0.000389
CD1_2=0.0
CD2_1=0.0
CD2_2=0.000389
DIMENSIONS=400 x 300
"""


def _fits(tmp_path, name="frame.fits", **header):
    path = tmp_path / name
    hdu = fits.PrimaryHDU(np.zeros((300, 400), dtype=np.float32))
    for k, v in header.items():
        hdu.header[k] = v
    hdu.writeto(path)
    return path


def test_parse_and_build_wcs_from_solved_ini():
    w = ps.wcs_from_astap_ini(ps.parse_astap_ini(SOLVED_INI))
    ra, dec = w.pixel_to_world_values(199.5, 149.5)  # crpix is 1-based
    assert abs(ra - 67.6958) < 1e-6 and abs(dec - 54.7747) < 1e-6


def test_unsolved_ini_gives_no_wcs():
    assert ps.wcs_from_astap_ini(ps.parse_astap_ini("PLTSOLVD=F\nERROR=no solution")) is None


def test_hint_args_convert_ra_to_hours_and_dec_to_south_pole_distance():
    args = ps._hint_args({"RA": 150.0, "DEC": 20.0, "FOCALLEN": 554.0, "YPIXSZ": 3.76, "NAXIS2": 4176})
    assert args[args.index("-ra") + 1] == "10.000000"
    assert args[args.index("-spd") + 1] == "110.000000"
    assert abs(float(args[args.index("-fov") + 1]) - 1.623) < 0.01


def test_hint_args_without_optics_let_astap_work_out_fov():
    args = ps._hint_args({})
    assert args == ["-fov", "0"]


def test_cached_solution_skips_astap(tmp_path, monkeypatch):
    img = _fits(tmp_path)
    ps.ini_path_for(img).write_text(SOLVED_INI)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran ASTAP")))

    solved = ps.solve_frame(img)
    assert solved.width == 400 and solved.height == 300
    assert abs(solved.pixel_scale_deg - 0.000389) < 1e-9


def test_solve_runs_astap_with_hints_and_reads_its_ini(tmp_path, monkeypatch):
    img = _fits(tmp_path, RA=67.7, DEC=54.8)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        ps.ini_path_for(img).write_text(SOLVED_INI)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ps.subprocess, "run", fake_run)
    solved = ps.solve_frame(img, astap_path="astap_cli.exe")

    assert solved is not None
    cmd = calls[0]
    assert cmd[:3] == ["astap_cli.exe", "-f", str(img)]
    assert "-ra" in cmd and "-update" not in cmd  # never modifies the image


def test_failed_solve_returns_none(tmp_path, monkeypatch):
    img = _fits(tmp_path)
    monkeypatch.setattr(ps.subprocess, "run", lambda cmd, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert ps.solve_frame(img) is None


def test_solve_frames_maps_every_input(tmp_path, monkeypatch):
    good, bad = _fits(tmp_path, "good.fits"), _fits(tmp_path, "bad.fits")
    ps.ini_path_for(good).write_text(SOLVED_INI)
    monkeypatch.setattr(ps.subprocess, "run", lambda cmd, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))

    results = ps.solve_frames([good, bad], {"platesolve": {"workers": 2}})
    assert results[good] is not None and results[bad] is None


def test_xisf_is_solved_from_a_temporary_fits_and_cached_next_to_the_original(tmp_path, monkeypatch):
    """ASTAP can't read PixInsight's XISF; it gets a FITS copy instead, and
    the solution (.ini) is kept beside the original XISF."""
    from xisf import XISF

    img = tmp_path / "frame_c.xisf"
    XISF.write(str(img), np.zeros((300, 400, 1), dtype=np.float32), creator_app="test",
               image_metadata={"FITSKeywords": {"RA": [{"value": "67.7", "comment": ""}],
                                                "DEC": [{"value": "54.8", "comment": ""}]}},
               xisf_metadata={})
    seen = {}

    def fake_run(cmd, **kwargs):
        handed = __import__("pathlib").Path(cmd[2])
        seen["suffix"] = handed.suffix
        seen["shape"] = fits.getdata(handed).shape
        # this ASTAP version writes no DIMENSIONS line -> size comes from XISF geometry
        handed.with_suffix(".ini").write_text(SOLVED_INI.replace("DIMENSIONS=400 x 300\n", ""))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ps.subprocess, "run", fake_run)
    solved = ps.solve_frame(img)

    assert seen == {"suffix": ".fits", "shape": (300, 400)}
    assert ps.ini_path_for(img).exists()
    assert (solved.width, solved.height) == (400, 300)

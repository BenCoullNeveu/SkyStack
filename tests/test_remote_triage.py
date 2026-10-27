"""Tests for skystack/triage.py (HTML package + applying returned decisions)
and skystack/delivery.py (outbox + Discord)."""
from __future__ import annotations

import json
import re

import numpy as np
import pytest
from astropy.io import fits

import skystack.delivery as delivery
from skystack.triage import DECISIONS_FORMAT, apply_decisions, build_triage_package


def _frames(tmp_path, n=4, filt="L"):
    root = tmp_path / "registered"
    root.mkdir()
    rng = np.random.default_rng(0)
    paths = []
    for i in range(n):
        p = root / f"LIGHT_{filt}_{i:04d}__01012025_c_r.fits"
        hdu = fits.PrimaryHDU((rng.random((60, 80)) * 1000).astype(np.float32))
        hdu.header["FILTER"] = filt
        hdu.header["EXPTIME"] = 300.0
        hdu.writeto(p)
        paths.append(p)
    return root, paths


def _build(tmp_path, n=4):
    root, paths = _frames(tmp_path, n)
    manifest = build_triage_package(
        paths, root, tmp_path / "out" / "pkg.html", tmp_path / "out" / "pkg.manifest.json",
        target="M 31", session_id="sid-1", preview_size=64, workers=2,
    )
    return root, paths, manifest


def _decisions(tmp_path, discarded, session_id="sid-1", name="d.json", reviewer="Ana"):
    p = tmp_path / name
    p.write_text(json.dumps({"format": DECISIONS_FORMAT, "session_id": session_id,
                             "reviewer": reviewer, "discarded": discarded}))
    return p


def test_package_is_one_html_file_with_every_frame_embedded(tmp_path):
    root, paths, manifest = _build(tmp_path)
    html = (tmp_path / "out" / "pkg.html").read_text(encoding="utf-8")

    payload = json.loads(re.search(r"const DATA = (\{.*?\});\n", html, re.S).group(1))
    assert payload["session_id"] == "sid-1"
    assert [f["key"] for f in payload["frames"]] == [p.name for p in paths]
    assert all(f["img"] and f["thumb"] and f["filter"] == "L" for f in payload["frames"])
    assert "__DATA__" not in html and "<title>Triage · M 31</title>" in html
    # nothing outside the page: no external scripts/styles to fetch
    assert "src=\"http" not in html and "href=\"http" not in html


def test_manifest_maps_keys_to_full_resolution_files(tmp_path):
    root, paths, manifest = _build(tmp_path)
    assert manifest["frames"] == {p.name: str(p) for p in paths}


def test_apply_moves_kept_and_rejected(tmp_path):
    root, paths, _ = _build(tmp_path)
    keep, rej = tmp_path / "BLINKED", tmp_path / "REJECTED"
    summary = apply_decisions(tmp_path / "out" / "pkg.manifest.json",
                              [_decisions(tmp_path, [paths[1].name])], keep, rej)

    assert summary["kept"] == 3 and summary["rejected"] == 1
    assert sorted(p.name for p in keep.iterdir()) == sorted(p.name for p in paths if p != paths[1])
    assert [p.name for p in rej.iterdir()] == [paths[1].name]
    assert not any(p.exists() for p in paths)


def test_any_reviewer_rejecting_a_frame_rejects_it(tmp_path):
    root, paths, _ = _build(tmp_path)
    d1 = _decisions(tmp_path, [paths[0].name], name="a.json", reviewer="Ana")
    d2 = _decisions(tmp_path, [paths[2].name], name="b.json", reviewer="Bo")
    summary = apply_decisions(tmp_path / "out" / "pkg.manifest.json", [d1, d2],
                              tmp_path / "BLINKED", tmp_path / "REJECTED")
    assert summary["rejected"] == 2 and summary["reviewers"] == ["Ana", "Bo"]


def test_decisions_for_another_package_are_refused_before_moving_anything(tmp_path):
    root, paths, _ = _build(tmp_path)
    with pytest.raises(ValueError, match="sid-OTHER"):
        apply_decisions(tmp_path / "out" / "pkg.manifest.json",
                        [_decisions(tmp_path, [], session_id="sid-OTHER")],
                        tmp_path / "BLINKED", tmp_path / "REJECTED")
    assert all(p.exists() for p in paths)


def test_delete_rejected_and_dry_run(tmp_path):
    root, paths, _ = _build(tmp_path)
    manifest = tmp_path / "out" / "pkg.manifest.json"
    dec = _decisions(tmp_path, [paths[0].name])

    apply_decisions(manifest, [dec], tmp_path / "BLINKED", delete_rejected=True, dry_run=True)
    assert all(p.exists() for p in paths)

    apply_decisions(manifest, [dec], tmp_path / "BLINKED", delete_rejected=True)
    assert not paths[0].exists() and not (tmp_path / "REJECTED").exists()


# --- delivery ---------------------------------------------------------------

def test_deliver_copies_to_outbox_without_webhook(tmp_path, monkeypatch):
    monkeypatch.delenv(delivery.WEBHOOK_ENV_VAR, raising=False)
    pkg = tmp_path / "pkg.html"
    pkg.write_text("x")
    res = delivery.deliver_package(pkg, {"outbox_dir": str(tmp_path / "outbox")}, "M 31", 4, "sid")
    assert (tmp_path / "outbox" / "pkg.html").exists()
    assert res["discord"] == "not configured"


def test_deliver_attaches_small_packages_and_announces_large_ones(tmp_path, monkeypatch):
    posts = []
    monkeypatch.setattr(delivery, "post_to_discord",
                        lambda url, msg, attachment=None: posts.append((url, attachment)))
    monkeypatch.setenv(delivery.WEBHOOK_ENV_VAR, "https://env-hook")
    pkg = tmp_path / "pkg.html"
    pkg.write_bytes(b"x" * 2000)

    res = delivery.deliver_package(pkg, {"discord_webhook_url": "https://cfg-hook"}, "M 31", 4, "sid")
    assert posts[-1] == ("https://env-hook", pkg)  # env var wins over config
    assert res["discord"] == "posted with attachment"

    res = delivery.deliver_package(pkg, {"discord_max_attachment_mb": 0.001}, "M 31", 4, "sid")
    assert posts[-1][1] is None and "too large" in res["discord"]


def test_discord_failure_does_not_raise(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("offline")
    monkeypatch.setattr(delivery, "post_to_discord", boom)
    monkeypatch.setenv(delivery.WEBHOOK_ENV_VAR, "https://hook")
    pkg = tmp_path / "pkg.html"
    pkg.write_text("x")
    assert delivery.deliver_package(pkg, {}, "M 31", 1, "sid")["discord"].startswith("failed")

"""Hand a triage package to reviewers.

The package is copied into an outbox folder (typically inside Dropbox, so
it syncs and can be shared from there). If a Discord webhook is set up, a
message is posted to the channel, with the file attached when it fits
under Discord's upload limit; otherwise the message says where to find it.

The webhook URL is a secret (anyone holding it can post to the channel), so
it is read from the SKYSTACK_DISCORD_WEBHOOK environment variable first and
only falls back to config.yaml.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

WEBHOOK_ENV_VAR = "SKYSTACK_DISCORD_WEBHOOK"


def webhook_url(triage_cfg: Mapping[str, Any]) -> Optional[str]:
    return os.environ.get(WEBHOOK_ENV_VAR) or triage_cfg.get("discord_webhook_url") or None


def post_to_discord(
    url: str,
    message: str,
    attachment: Optional[Path] = None,
    timeout_s: float = 300,
) -> None:
    import requests

    data = {"content": message[:2000]}  # Discord's message length cap
    if attachment is None:
        resp = requests.post(url, json=data, timeout=timeout_s)
    else:
        with open(attachment, "rb") as fh:
            resp = requests.post(
                url,
                data=data,
                files={"file": (Path(attachment).name, fh, "text/html")},
                timeout=timeout_s,
            )
    resp.raise_for_status()


def deliver_package(
    package: Path,
    triage_cfg: Mapping[str, Any],
    target: str,
    n_frames: int,
    session_id: str,
) -> Dict[str, Any]:
    """Copy the package to the outbox and (optionally) announce it on Discord."""
    package = Path(package)
    result: Dict[str, Any] = {"outbox_path": None, "discord": "not configured"}

    outbox = triage_cfg.get("outbox_dir")
    if outbox:
        dest = Path(outbox) / package.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package, dest)
        result["outbox_path"] = str(dest)
        logger.info("Triage package copied to %s", dest)

    url = webhook_url(triage_cfg)
    if not url:
        return result

    size_mb = package.stat().st_size / 1e6
    limit_mb = float(triage_cfg.get("discord_max_attachment_mb", 10))
    where = result["outbox_path"] or str(package)
    message = (
        f"**Triage needed: {target}** ({n_frames} frames, package `{session_id}`)\n"
        "Open the HTML file in a browser, reject bad frames with X, then click "
        "*Download decisions* and send the .json file back here."
    )
    try:
        if size_mb <= limit_mb:
            post_to_discord(url, message, attachment=package)
            result["discord"] = "posted with attachment"
        else:
            post_to_discord(url, message + f"\nThe file is {size_mb:.0f} MB, too big to attach "
                                           f"here. It is in the shared folder: `{Path(where).name}`")
            result["discord"] = "posted without attachment (too large)"
    except Exception as e:
        # Delivery is a convenience; the package is already on disk.
        logger.error("Discord post failed: %s", e)
        result["discord"] = f"failed: {e}"
    return result

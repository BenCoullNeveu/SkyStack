"""Post-stack processing presets, applied in order to integrated masterLights
(stage_postprocess, pixscripts/postprocess_script.js).

Each preset is one PixInsight process with its parameters. Outputs go to
REGISTERED/<target>/processed/ named <master stem>_<suffix>[_<suffix>...].xisf;
the masters themselves are never modified.

config.yaml's `postprocess.presets` can override a preset's parameters or add
new presets of the same shape; `postprocess.default_steps` picks the steps
used when none are given.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence

PRESETS: Dict[str, Dict[str, Any]] = {
    "bxt_correct": {
        "label": "BlurXTerminator (correct only)",
        "process": "BlurXTerminator",
        "suffix": "bxtc",
        "linear": True,   # meant for linear data, i.e. straight after stacking
        "params": {
            "ai_file": "BlurXTerminator.4.pb",
            "correct_only": True,
            "correct_first": False,
            "nonstellar_then_stellar": False,
            "lum_only": False,
            "sharpen_stars": 0.20,
            "adjust_halos": 0.00,
            "nonstellar_psf_diameter": 0.00,
            "auto_nonstellar_psf": True,
            "sharpen_nonstellar": 0.80,
        },
    },
}

DEFAULT_STEPS = ["bxt_correct"]


def available_presets(config: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Built-in presets merged with config overrides (params merge key by key)."""
    presets = copy.deepcopy(PRESETS)
    for name, override in ((config.get("postprocess", {}) or {}).get("presets") or {}).items():
        base = presets.setdefault(name, {"label": name, "params": {}})
        for key, value in (override or {}).items():
            if key == "params":
                base["params"] = {**base.get("params", {}), **(value or {})}
            else:
                base[key] = value
    for name, p in presets.items():
        if not p.get("process") or not p.get("suffix"):
            raise ValueError(f"Post-processing preset {name!r} needs a 'process' and a 'suffix'")
    return presets


def default_steps(config: Mapping[str, Any]) -> List[str]:
    return list((config.get("postprocess", {}) or {}).get("default_steps") or DEFAULT_STEPS)


def resolve_steps(config: Mapping[str, Any], names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """[{name, process, suffix, params}] for the given preset names, in order."""
    presets = available_presets(config)
    names = list(names) if names else default_steps(config)
    unknown = [n for n in names if n not in presets]
    if unknown:
        raise ValueError(f"Unknown post-processing step(s) {unknown}. Available: {sorted(presets)}")
    return [{"name": n, "process": presets[n]["process"], "suffix": presets[n]["suffix"],
             "params": dict(presets[n].get("params") or {})} for n in names]


def output_name(master_stem: str, steps: Sequence[Mapping[str, Any]]) -> str:
    return "_".join([master_stem, *(s["suffix"] for s in steps)]) + ".xisf"

import os
from pathlib import Path
from pyexpat.errors import messages
from time import monotonic, sleep
import subprocess
import logging
import json
from datetime import datetime

logger = logging.getLogger(__name__)

PIXINSIGHT_PATH = r"C:\Program Files\PixInsight\bin\PixInsight.exe"

# JSON paths
CONFIG_DIR = Path("C:/Temp/PixStack")
INPUT_FILES_JSON = CONFIG_DIR / "input_files.json"
PARAMS_JSON = CONFIG_DIR / "params.json"


def _wait_for_sentinel(path: Path, timeout_s: float, poll_interval: float = 1.0):
    """Poll for `path` to appear, raising TimeoutError if it doesn't within
    timeout_s seconds.

    A non-zero PixInsight.exe exit code is not the only failure mode: a
    script can throw partway through (e.g. ImageIntegration rejecting a
    request with fewer than 3 source images) and never reach the line that
    writes its completion sentinel, while the outer process still exits 0.
    Without a bound here, that leaves the caller waiting forever.
    """
    deadline = monotonic() + timeout_s
    while not path.exists():
        if monotonic() > deadline:
            raise TimeoutError(
                f"Timed out after {timeout_s}s waiting for PixInsight to signal "
                f"completion via {path}. Check the PixInsight console/log for a "
                f"script error."
            )
        sleep(poll_interval)


def _run_cmd(cmd_list):
    """Run the command and log output/errors."""
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("Error running PixInsight:\n%s", result.stderr)
        else:
            logger.info("PixInsight output:\n%s", result.stdout)
        return result
    except Exception as e:
        logger.exception("Failed to run PixInsight.")
        raise e
    
def save_json(data, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def gpu_free_mb():
    """Free memory on the first NVIDIA GPU in MiB, or None if it can't be read."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        return int(out.stdout.strip().splitlines()[0]) if out.returncode == 0 else None
    except Exception:
        return None


def other_pixinsight_pids():
    """PIDs of PixInsight processes already running (e.g. the interactive one)."""
    try:
        import psutil
    except ImportError:
        return []
    pids = []
    for proc in psutil.process_iter(["name"]):
        if (proc.info.get("name") or "").lower() == "pixinsight.exe":
            pids.append(proc.pid)
    return pids


def decide_gpu(mode: str = "auto", min_free_mb: int = 2048):
    """Whether a newly launched PixInsight should see the GPU, and why.

    TensorFlow (BlurXTerminator, NoiseXTerminator, StarXTerminator) grabs
    nearly all GPU memory in the first PixInsight process that uses it. A
    second instance then stalls while initializing on the GPU. In "auto" mode
    the new instance therefore gets the GPU only if no other PixInsight is
    running, or there's still min_free_mb free.
    """
    mode = str(mode).lower()
    if mode in ("on", "true", "yes"):
        return True, "GPU enabled (pixinsight_gpu: on)"
    if mode in ("off", "false", "no"):
        return False, "GPU disabled (pixinsight_gpu: off)"
    others = other_pixinsight_pids()
    if not others:
        return True, "no other PixInsight running"
    free = gpu_free_mb()
    if free is None:
        return True, "GPU free memory unknown"
    if free >= min_free_mb:
        return True, f"{free} MiB GPU memory free"
    return False, (f"another PixInsight (pid {', '.join(map(str, others))}) holds the GPU "
                   f"({free} MiB free < {min_free_mb}); running on CPU -- close it to use the GPU")


def launch_pixinsight(instance=None,
                      script_path: Path = None,
                      timeout_s: float = 180,
                      use_gpu: bool = True,
                      ):

    if script_path is None:
        filedir = Path(__file__).parent.parent
        script_path = filedir / "pixscripts" / "launch_pix_helper.js"

    n = f"-n={instance}" if instance is not None else "-n"
    run_arg = f'--run="{script_path}"'
    cmd = [PIXINSIGHT_PATH,
           n,
           "--automation-mode",
           run_arg
           ]

    # wait for the launch_done.tmp file to appear
    done_path = Path("C:/Temp/PixStack/launch_done.tmp")
    done_path.unlink(missing_ok=True)  # clear a stale sentinel left by a crashed prior launch

    env = None
    if not use_gpu:
        # hides CUDA devices from TensorFlow, so AI processes run on the CPU
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": "-1"}

    # run using Popen for background running
    try:
        process = subprocess.Popen(cmd,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,
                         text=True,
                         env=env)
        logger.info(f"Launched PixInsight in automation mode: {process.pid}")

        _wait_for_sentinel(done_path, timeout_s=timeout_s)
        logger.info("PixInsight is ready.")
        # remove the temporary file
        while done_path.exists():
            done_path.unlink(missing_ok=True)

    except Exception as e:
        logger.exception("Failed to launch PixInsight.")
        raise e

    return process

def close_pixinsight(process):
    """Close the PixInsight instance."""
    # if instance is not None:
    #     cmd = [PIXINSIGHT_PATH, 
    #            f"-n={instance}", 
    #            "--force-exit"]
    #     _run_cmd(cmd)
    try:
        logger.info(f"Terminating PixInsight process {process.pid}")
        process.terminate()
    except Exception as e:
        logger.exception("Failed to close PixInsight.")
        raise e

def stack_with_pixinsight(
    file_list,
    output_path: Path,
    instance=None,
    script_path: Path = None,
    timeout_s: float = 1800):
    """
    Run a PixInsight JavaScript script for stacking FITS files.
    """
        
    if script_path is None:
        filedir = Path(__file__).parent.parent
        script_path = filedir / "pixscripts" / "basic_stack_script.js"
        # script_path = "pixscripts/basic_stack_script.js"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save input files to JSON
    input_files = [str(Path(f).absolute()) for f in file_list]
    save_json(input_files, INPUT_FILES_JSON)
    
    # Save output path to JSON
    save_json({"output_path": str(Path(output_path).absolute())}, PARAMS_JSON)
    
    # Build the --run argument as a single string
    # run_arg = f'--run="{script_path}"'

    # Build command list
    # cmd = [
    #     PIXINSIGHT_PATH,
    #     "--new", # new instance in first free slot
    #     "--automation-mode",
    #     run_arg,
    #    "--force-exit"
    # ]
    
    # NEW -> Build command list but use existing instance!
    execute_arg = f'-x={instance}:{script_path}' if instance is not None else f'-x={script_path}'
    cmd = [
        PIXINSIGHT_PATH,
        "--automation-mode",
        execute_arg
    ]

    donepath = Path("C:/Temp/PixStack/basic_stack_complete.tmp")
    donepath.unlink(missing_ok=True)  # clear a stale sentinel left by a crashed prior run

    result = _run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"PixInsight stacking failed (exit {result.returncode}): {result.stderr}"
        )

    _wait_for_sentinel(donepath, timeout_s=timeout_s)
    # remove the temporary file
    while donepath.exists():
        donepath.unlink(missing_ok=True)

    return



def calibrate_with_pixinsight(
    light_list: Path,
    master_flat: Path,
    master_dark: Path,
    output_dir: Path,
    date_obs: datetime.date,
    instance: int = None,
    script_name: str = None,
    timeout_s: float = 1800
    ):
    """Run a PixInsight JavaScript for calibration."""

    if script_name is None:
        # absolute, so this works whatever the caller's working directory is
        script_name = Path(__file__).parent.parent / "pixscripts" / "calibration_script.js"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save input light files to JSON
    input_files = [str(Path(f).absolute()) for f in light_list]
    save_json(input_files, INPUT_FILES_JSON)
    
    # Save master calibration files and output directory to JSON
    save_json({
        "master_flat": str(Path(master_flat).absolute()) if master_flat is not None else "",
        "master_dark": str(Path(master_dark).absolute()) if master_dark is not None else "",
        "output_dir": str(Path(output_dir).absolute()),
        "prefix": f'__{date_obs.strftime("%d%m%Y")}_c'
    }, PARAMS_JSON)
    
    # Build the --run argument as a single string
    # run_arg = f'--run="{script_name}"'
    run_arg = f'-x={instance}:{script_name}' if instance is not None else f'-x={script_name}'

    # Build command list
    # cmd = [
    #     PIXINSIGHT_PATH,
    #     "--new",  # new instance in first free slot
    #     "--automation-mode",
    #     run_arg,
    #     "--force-exit"
    # ]
    # NEW COMMAND!
    cmd = [
        PIXINSIGHT_PATH,
        "--automation-mode",
        run_arg,
    ]

    donepath = Path("C:/Temp/PixStack/calibration_complete.tmp")
    donepath.unlink(missing_ok=True)  # clear a stale sentinel left by a crashed prior run

    result = _run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"PixInsight calibration failed (exit {result.returncode}): {result.stderr}"
        )

    _wait_for_sentinel(donepath, timeout_s=timeout_s)
    # remove the temporary file
    while donepath.exists():
        donepath.unlink(missing_ok=True)

    return

def _read_signal(donepath: Path) -> str:
    """Read and remove a sentinel written by a script that reports errors
    through it ("done" or "error: <message>")."""
    text = ""
    for _ in range(20):
        try:
            text = donepath.read_text().strip()
            break
        except OSError:
            sleep(0.25)  # PixInsight may still hold the file open
    while donepath.exists():
        donepath.unlink(missing_ok=True)
    return text


def _run_reporting_script(script_path: Path, donepath: Path, instance, timeout_s: float, what: str):
    execute_arg = f'-x={instance}:{script_path}' if instance is not None else f'-x={script_path}'
    cmd = [PIXINSIGHT_PATH, "--automation-mode", execute_arg]

    donepath.unlink(missing_ok=True)  # clear a stale sentinel left by a crashed prior run
    result = _run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"PixInsight {what} failed (exit {result.returncode}): {result.stderr}")

    _wait_for_sentinel(donepath, timeout_s=timeout_s)
    signal = _read_signal(donepath)
    if signal.startswith("error"):
        raise RuntimeError(f"PixInsight {what} script reported: {signal}")


def register_with_pixinsight(
    light_list,
    reference: Path,
    output_dir: Path,
    postfix: str = "_r",
    distortion_correction: bool = True,
    instance: int = None,
    script_path: Path = None,
    timeout_s: float = 7200,
):
    """Register lights to `reference` with StarAlignment."""
    if script_path is None:
        script_path = Path(__file__).parent.parent / "pixscripts" / "registration_script.js"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    save_json([str(Path(f).absolute()) for f in light_list], INPUT_FILES_JSON)
    save_json({
        "reference": str(Path(reference).absolute()),
        "output_dir": str(output_dir.absolute()),
        "postfix": postfix,
        "distortion_correction": bool(distortion_correction),
    }, PARAMS_JSON)

    _run_reporting_script(script_path, CONFIG_DIR / "registration_complete.tmp",
                          instance, timeout_s, "registration")


def integrate_with_pixinsight(
    groups,
    ln_dir: Path,
    ln_scale: int = 1024,
    instance: int = None,
    script_path: Path = None,
    timeout_s: float = 4 * 3600,
):
    """Local-normalize and integrate registered lights, one master per group.

    groups: list of {"name", "files", "output_path", "rejection"} dicts.
    Returns the per-group result list the script writes
    ({"name", "status", "output_path"/"error", ...}).
    """
    if script_path is None:
        script_path = Path(__file__).parent.parent / "pixscripts" / "integration_script.js"

    result_path = CONFIG_DIR / "integration_result.json"
    result_path.unlink(missing_ok=True)
    save_json({
        "ln_dir": Path(ln_dir).absolute().as_posix(),
        "ln_scale": int(ln_scale),
        "groups": [
            {
                "name": g["name"],
                "files": [Path(f).absolute().as_posix() for f in g["files"]],
                "output_path": Path(g["output_path"]).absolute().as_posix(),
                "rejection": g["rejection"],
            }
            for g in groups
        ],
    }, PARAMS_JSON)

    _run_reporting_script(script_path, CONFIG_DIR / "integration_complete.tmp",
                          instance, timeout_s, "integration")
    if not result_path.exists():
        raise RuntimeError("PixInsight integration finished without writing integration_result.json")
    return json.loads(result_path.read_text())


def measure_with_pixinsight(
    files,
    subframe_scale: float = 1.0,
    camera_gain: float = 1.0,
    instance: int = None,
    script_path: Path = None,
    timeout_s: float = 3600,
):
    """Measure frames with SubframeSelector (FWHM, eccentricity, stars, PSF
    signal weight, PSF SNR, noise, ...). Returns {input path: metrics dict}
    for every frame PixInsight measured; frames it couldn't are left out."""
    if script_path is None:
        script_path = Path(__file__).parent.parent / "pixscripts" / "measure_script.js"

    files = [Path(f).absolute() for f in files]
    result_path = CONFIG_DIR / "measure_result.json"
    result_path.unlink(missing_ok=True)
    save_json([f.as_posix() for f in files], INPUT_FILES_JSON)
    save_json({
        "result_path": result_path.as_posix(),
        "subframe_scale": float(subframe_scale),
        "camera_gain": float(camera_gain),
    }, PARAMS_JSON)

    _run_reporting_script(script_path, CONFIG_DIR / "measure_complete.tmp",
                          instance, timeout_s, "measurement")
    if not result_path.exists():
        raise RuntimeError("PixInsight measurement finished without writing measure_result.json")

    by_key = {f.as_posix().lower(): f for f in files}
    out = {}
    for entry in json.loads(result_path.read_text()):
        src = by_key.get(Path(entry.pop("path")).as_posix().lower())
        if src is not None:
            out[src] = entry
    return out


def postprocess_with_pixinsight(
    items,
    steps,
    instance: int = None,
    script_path: Path = None,
    timeout_s: float = 2 * 3600,
):
    """Apply `steps` ([{name, process, params}]) in order to each item
    ({"input", "output"}) and save the result. Returns the per-image result
    list the script writes ({"input", "status": "ok"|"error", "output"|"error"})."""
    if script_path is None:
        script_path = Path(__file__).parent.parent / "pixscripts" / "postprocess_script.js"

    result_path = CONFIG_DIR / "postprocess_result.json"
    result_path.unlink(missing_ok=True)
    save_json({
        "items": [{"input": Path(i["input"]).absolute().as_posix(),
                   "output": Path(i["output"]).absolute().as_posix()} for i in items],
        "steps": [{"name": s["name"], "process": s["process"], "params": s.get("params") or {}}
                  for s in steps],
    }, PARAMS_JSON)

    _run_reporting_script(script_path, CONFIG_DIR / "postprocess_complete.tmp",
                          instance, timeout_s, "post-processing")
    if not result_path.exists():
        raise RuntimeError("PixInsight post-processing finished without writing postprocess_result.json")
    return json.loads(result_path.read_text())

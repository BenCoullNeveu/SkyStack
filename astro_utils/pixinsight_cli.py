from pathlib import Path
from pyexpat.errors import messages
from time import sleep
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

def launch_pixinsight(instance=None,
                      script_path: Path = None
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

    # run using Popen for background running
    try:
        process = subprocess.Popen(cmd,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,
                         text=True)
        logger.info(f"Launched PixInsight in automation mode: {process.pid}")
        
        # wait for the launch_done.tmp file to appear
        done_path = Path("C:/Temp/PixStack/launch_done.tmp")
        while not done_path.exists():
            sleep(1)
        logger.info("PixInsight is ready.")
        # remove the temporary file
        while done_path.exists():
            done_path.unlink(missing_ok=True)

    except Exception as e:
        logger.exception("Failed to launch PixInsight.")
        raise e

    return process

def stack_with_pixinsight(
    file_list,
    output_path: Path,
    instance=None,
    script_path: Path = None):
    """
    Run a PixInsight JavaScript script for stacking FITS files.
    """
        
    if script_path is None:
        # filedir = Path(__file__).parent.parent
        # script_path = filedir / "pixscripts" / "basic_stack_script.js"
        script_path = "pixscripts/basic_stack_script.js"

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
    _run_cmd(cmd)

    donepath = Path("C:/Temp/PixStack/basic_stack_complete.tmp")
    while not donepath.exists():
        sleep(1)
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
    script_name: str = None
    ):
    """Run a PixInsight JavaScript for calibration."""

    if script_name is None:
        # filedir = Path(__file__).parent.parent
        # script_name = filedir / "pixscripts" / "calibration_script.js"
        script_name = "pixscripts/calibration_script.js"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save input light files to JSON
    input_files = [str(Path(f).absolute()) for f in light_list]
    save_json(input_files, INPUT_FILES_JSON)
    
    # Save master calibration files and output directory to JSON
    save_json({
        "master_flat": str(Path(master_flat).absolute()),
        "master_dark": str(Path(master_dark).absolute()),
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

    _run_cmd(cmd)

    donepath = Path("C:/Temp/PixStack/calibration_complete.tmp")
    while not donepath.exists():
        sleep(1)
    # remove the temporary file
    while donepath.exists():
        donepath.unlink(missing_ok=True)

    return
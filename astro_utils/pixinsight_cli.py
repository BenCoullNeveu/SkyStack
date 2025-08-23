from pathlib import Path
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
        
def stack_with_pixinsight(
    file_list,
    output_path: Path,
    script_path: Path = Path(r"D:\Dropbox\SkyShare Automation Scripts\Auto Calibration\pixscripts\basic_stack_script.js")
    ):
    """
    Run a PixInsight JavaScript script for stacking FITS files.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save input files to JSON]
    input_files = [str(Path(f).absolute()) for f in file_list]
    save_json(input_files, INPUT_FILES_JSON)
    
    # Save output path to JSON
    save_json({"output_path": str(Path(output_path).absolute())}, PARAMS_JSON)
    
    # Build the --run argument as a single string
    run_arg = f'--run="{script_path}"'


    # Build command list
    cmd = [
        PIXINSIGHT_PATH,
        "--new", # new instance in first free slot
        "--automation-mode",
        run_arg,
       "--force-exit"
    ]

    return _run_cmd(cmd)



def calibrate_with_pixinsight(
    light_list: Path, 
    master_flat: Path, 
    master_dark: Path, 
    output_dir: Path, 
    date_obs: datetime.date,
    script_name: str = Path(r"D:\Dropbox\SkyShare Automation Scripts\Auto Calibration\pixscripts\calibration_script.js")
    ):
    """Run a PixInsight JavaScript for calibration."""
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
    run_arg = f'--run="{script_name}"'
    
    # Build command list
    cmd = [
        PIXINSIGHT_PATH,
        "--new",  # new instance in first free slot
        "--automation-mode",
        run_arg,
        "--force-exit"
    ]

    _run_cmd(cmd)

# shares the master lights of a target in the shared directory

from pathlib import Path
import yaml
from astro_utils.fits_helpers import xisf2fits, fits2xisf
from astro_utils.file_ops import safe_copy

# --- Configuration loading ---
config_path = Path(__file__).parent / "config.yaml"
with config_path.open("r") as f:
    config = yaml.safe_load(f)
    
SHARED_DIR = Path(config.get("shared_dir"))
MASTERS_DIR = Path(config.get("masters_dir"))
OUTPUT_FORMAT = config.get("output_format", "fits")
OVERWRITE_SHARED = config.get("overwrite_shared", False)
TARGETS = config.get("targets_to_share", [])
EXCLUDE_DRIZZLED = config.get("exclude_drizzled", True)
COMBINE_LOGS = config.get("combine_logs", False)

# note that masters are found in REGISTERED/<TargetName>/master/masterLight*.xisf (or .fits)

def share_master_lights(target_name: str, shared_dir: Path, masters_dir: Path, output_format='fits', exclude_drizzled=False, overwrite=False):
    """
    Share the master lights of a target by copying them to the shared directory.

    Parameters:
    - target_name: Name of the target whose master lights are to be shared.
    - shared_dir: Path to the shared directory where master lights will be copied.
    - masters_dir: Path to the directory containing master light files.
    - output_format: Desired output format for shared master lights ('fits' or 'xisf').
    - overwrite: Whether to overwrite existing files in the shared directory.
    """
    target_shared_dir = shared_dir / target_name
    target_shared_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Sharing master lights for target: {target_name}")
    for format in ['xisf', 'fits']:
        for master_light_file in masters_dir.glob(f"masterLight*.{format}"):
            if exclude_drizzled and "drizzle" in master_light_file.stem.lower():
                continue
            # continue if path contains superceded
            if "superceded" in master_light_file.stem.lower():
                print(f"Skipping superceded file: {master_light_file}")
                continue
            dest_file = target_shared_dir / master_light_file.name
            if format == output_format:
                safe_copy(master_light_file, dest_file, overwrite=overwrite)
            elif output_format == 'fits':
                fitsfile = dest_file.with_suffix(".fits")
                xisf2fits(master_light_file, fitsfile, delete_after_convert=False)
            elif output_format == 'xisf':
                xisffile = dest_file.with_suffix(".xisf")
                fits2xisf(master_light_file, xisffile, overwrite=overwrite)
            else:
                raise ValueError(f"Unsupported output format: {output_format}")
            print(f"Copied {master_light_file} to {dest_file}")
            
            
def create_summary(target_name: str, shared_dir: Path, log_dir: Path, combine_logs=False):
    """
    Create a summary text file listing all master light files for a target using the log file.
    > REGISTERED/<TargetName>/logs/*.log (filename given by timestamp)
    The created file is a CSV containing the following columns:
    FILTER | EXPOSURE | BINNING | REJECTION METHOD | NUM FRAMES | INT TIME (hrs) | DATE CREATED
    
    example of relevant log file content:
[...]
******************** IMAGE INTEGRATION ********************
[2025-12-19 14:07:21] Group of 172 Light frames (172 active)
[2025-12-19 14:07:21] SIZE  : 6248x4176
[2025-12-19 14:07:21] BINNING  : 1
[2025-12-19 14:07:21] Filter   : O
[2025-12-19 14:07:21] Exposure : 600.00s
[2025-12-19 14:07:21] Keywords : []
[2025-12-19 14:07:21] Mode     : post-calibration
[2025-12-19 14:07:21] Color    : mono
[2025-12-19 14:07:21] 
[2025-12-19 14:07:21] Rejection method auto-selected: Linear Fit Clipping
[2025-12-19 14:07:21] Integration completed: master Light saved at path D:/Astrophotography/SkyShare Data/REGISTERED/Barnard 13/master/masterLight_BIN-1_6248x4176_EXPOSURE-600.00s_FILTER-O_mono.xisf
[2025-12-19 14:07:21] ***********************************************************
[2025-12-19 14:07:21] 
[...]

    Parameters:
    - target_name: Name of the target.
    - master_dir: Path to the directory containing master light files.
    - log_dir: Path to the directory containing log files.
    - combine_logs: Whether to combine multiple log files or just use the latest one.
    """
    # log file is in master_dir / logs / *.log
    logs = list(log_dir.glob("*.log"))
    print(f"Found {len(logs)} log files in {log_dir}")
    if not logs:
        print(f"No log files found in {log_dir}")
        return
    if not combine_logs:
        latest_log = max(logs, key=lambda f: f.stat().st_mtime)
        print(f"Using latest log file: {latest_log}")
        logs = [latest_log]
    summary_file = shared_dir / f"{target_name}_integration_summary.csv"
    with summary_file.open("w") as f:
        # write header
        f.write("FILTER,EXPOSURE,BINNING,REJECTION METHOD,NUM FRAMES,INT TIME (HRS),DATE CREATED\n")
        total_integration = 0 # hrs
        for logfile in logs:
            with logfile.open("r") as log:
                lines = log.readlines()
                img_int = False
                for i, line in enumerate(lines):
                    if "IMAGE INTEGRATION" in line:
                        # start processing from here
                        # print("Found Image Integration section.")
                        img_int = True
                    elif "FAST INTEGRATION" in line:
                        # print("Found Fast Integration section")
                        img_int = True
                    elif "DRIZZLE INTEGRATION" in line:
                        # print("Found Drizzle Integration section, skipping filters.")
                        img_int = False
                    if img_int:
                        if "Group of" in line and "Light frames" in line:
                            # extract num frames
                            parts = line.split("Light frames (")[1].split(" active")[0].strip().split()
                            num_frames = parts[0]
                            date_created = line.split("]")[0].strip("[").split()[0]
                            # look ahead for other details
                            binning = ""
                            filter = ""
                            exposure = ""
                            rejection_method = ""
                            for j in range(i+1, min(i+10, len(lines))):
                                # print(lines[j])
                                if "BINNING" in lines[j]:
                                    binning = lines[j].split("BINNING")[1].strip().split(":")[1].strip()
                                elif "Filter" in lines[j]:
                                    filter = lines[j].split("Filter")[1].strip().split(":")[1].strip()
                                elif "Exposure" in lines[j]:
                                    exposure = lines[j].split("Exposure")[1].strip().split(":")[1].strip()
                                elif 'Color' in lines[j]:
                                    print("Found Color line, ending search for details.")
                                    # print(lines[j+1])
                                    # print(lines[j+2])
                                elif "Rejection method" in lines[j]:
                                    # print("Found rejection method.")
                                    rejection_method = lines[j].split("Rejection method")[1].strip().split(":")[1].strip()
                            # write to summary file
                            int_time = float(exposure[:-1]) * int(num_frames) / 3600 if exposure else 0
                            total_integration += int_time
                            f.write(f"{filter},{exposure},{binning},{rejection_method},{num_frames},{int_time:.2f},{date_created}\n")
        f.write(f"\n# TOTAL INTEGRATION TIME: {total_integration:.2f} hrs\n")

if __name__ == "__main__":
    for target in TARGETS:
        print("--------------------------------")
        print(f"Processing target: {target}")
        share_master_lights(target, SHARED_DIR, MASTERS_DIR / target / "master", output_format=OUTPUT_FORMAT, exclude_drizzled=EXCLUDE_DRIZZLED, overwrite=OVERWRITE_SHARED)
        create_summary(target, SHARED_DIR / target, MASTERS_DIR / target / "logs", combine_logs=COMBINE_LOGS)
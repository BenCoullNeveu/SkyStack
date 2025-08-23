#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict
from astro_utils.config_loader import load_config
from astro_utils.fits_helpers import read_fits_header_info, parse_date_from_path, parse_date_from_master_path
from astro_utils.pixinsight_cli import calibrate_with_pixinsight
from astro_utils.file_ops import ensure_dir, get_target_name
import logging
import json
import matplotlib.pyplot as plt
import numpy as np

# --- Logging ---
logger = logging.getLogger(__name__)

# --- Load config ---
config = load_config(Path(__file__).parent / "config.yaml")

LIGHT_BASE_DIR = Path(config.get("light_base_dir", "D:/Astrophotography/SkyShare Data/LIGHT"))
CALIBRATED_OUTPUT_DIR = Path(config.get("calibrated_output_dir", "D:/Astrophotography/SkyShare Data/CALIBRATED"))
FLATS_DIR = Path(config.get("flat_master_output", "D:/Astrophotography/SkyShare Data/MASTERS/FLAT"))
DARKS_DIR = Path(config.get("dark_master_output", "D:/Astrophotography/SkyShare Data/MASTERS/DARK"))

MAX_FLAT_DAYS_DIFF = config.get("calibration_search_days_flat", 4)
MAX_DARK_MONTHS_DIFF = config.get("calibration_search_months_dark", 3)
ROT_TOLERANCE = config.get("flat_rotation_tolerance", 0.5)
OVERWRITE_EXISTING = config.get("overwrite_existing", False)

# FITS keywords
HEADER_FILTER_KEY = config.get("header_filter_key")
HEADER_ROTATION_KEY = config.get("header_rotation_key")
HEADER_GAIN_KEY = config.get("header_gain_key") 
HEADER_OFFSET_KEY = config.get("header_offset_key")
HEADER_EXPTIME_KEY = config.get("header_exptime_key")
HEADER_TEMPERATURE_KEY = config.get("header_temperature_key")


def find_best_master(master_dir: Path, target_meta:dict, date_obs, max_days=None, max_months=None, 
                     ignore_expt=False, ignore_rot=False, ignore_temp=False, ignore_filter=False):
    """
    Selects the best master calibration frame (flat/dark) based on metadata and timing.
    """
    best_file = None
    for master_file in master_dir.glob("*.fits"):
        try:
            fits_dict = read_fits_header_info(master_file)
            m_date = parse_date_from_master_path(master_file)
        except Exception as e:
            logger.error(f"Error reading master header {master_file}: {e}")
            continue
        
        # Metadata extraction   
        filt = fits_dict.get(HEADER_FILTER_KEY)
        expt = fits_dict.get(HEADER_EXPTIME_KEY)
        rot = fits_dict.get(HEADER_ROTATION_KEY)
        gain = fits_dict.get(HEADER_GAIN_KEY)
        offset = fits_dict.get(HEADER_OFFSET_KEY)
        temp = fits_dict.get(HEADER_TEMPERATURE_KEY)

        # Metadata checks
        # logger.debug(f"Checking master {master_file} with metadata: {fits_dict} vs target {target_meta}")
        if not ignore_expt and expt != target_meta[HEADER_EXPTIME_KEY]:
            continue
        if not ignore_filter and filt != target_meta[HEADER_FILTER_KEY]:
            continue
        if gain != target_meta[HEADER_GAIN_KEY] or offset != target_meta[HEADER_OFFSET_KEY]:
            continue
        if not ignore_temp and temp is not None and temp != target_meta[HEADER_TEMPERATURE_KEY]:
            continue
        if not ignore_rot and rot is not None and abs(rot - target_meta[HEADER_ROTATION_KEY]) > ROT_TOLERANCE:
            continue

        # Timing checks
        # logger.debug(f"Checking master {master_file} with date {m_date} against target date {date_obs}")
        # logger.debug(f"Time diff: {abs((m_date - date_obs).days)} days. Max= {max_days} days. Max months= {max_months} months.")
        if max_days is not None and abs((m_date - date_obs).days) <= max_days:
            best_file = master_file
        if max_months is not None:
            month_diff = abs((m_date.year - date_obs.year) * 12 + (m_date.month - date_obs.month))
            if month_diff <= max_months:
                best_file = master_file

    return best_file


def calibrate_lights(light_dir: Path, flats_dir: Path, darks_dir: Path, output_dir: Path):
    """
    Calibrates light frames using the best-matching master flats and darks.
    
    Stores a log file with the format:
    {
        "Targets name": "Target1",
        "DD-MM-YYYY": {
            "Filter1": {
                "Expt1": {
                    "flat_master": "path/to/masterFlat.fits",
                    "dark_master": "path/to/masterDark.fits",
                    "num lights": 10,
                    "num calibrated": 5,
                    "failed lights": ["path/to/failed1.fits", ...]
                },
                ...
            },
            ...
        },
    }
    """
    calibration_groups = defaultdict(list)

    for fits_file in light_dir.rglob("*.fits"):
        try:
            fits_dict = read_fits_header_info(fits_file)
            date_obs = parse_date_from_path(fits_file) or None
            # logger.debug(f"Processing light frame: {fits_file} with date {date_obs}")
        except Exception as e:
            logger.error(f"Error reading light header {fits_file}: {e}")
            continue
        
        # Metadata extraction
        filt = fits_dict.get(HEADER_FILTER_KEY)
        rot = fits_dict.get(HEADER_ROTATION_KEY)
        gain = fits_dict.get(HEADER_GAIN_KEY)
        offset = fits_dict.get(HEADER_OFFSET_KEY)
        expt = fits_dict.get(HEADER_EXPTIME_KEY)
        temp = fits_dict.get(HEADER_TEMPERATURE_KEY)

        # Build output path
        out_path = output_dir / get_target_name(fits_file)

        ensure_dir(out_path)

        flat_meta = {
            HEADER_FILTER_KEY: filt,
            HEADER_GAIN_KEY: gain,
            HEADER_OFFSET_KEY: offset,
            HEADER_ROTATION_KEY: rot
        }
        flat_master = find_best_master(
            flats_dir, flat_meta, date_obs,
            max_days=MAX_FLAT_DAYS_DIFF, ignore_expt=True, ignore_temp=True
        )
        
        dark_meta = {
            HEADER_GAIN_KEY: gain,
            HEADER_OFFSET_KEY: offset,
            HEADER_EXPTIME_KEY: expt,
            HEADER_TEMPERATURE_KEY: temp
        }
        dark_master = find_best_master(
            darks_dir, dark_meta, date_obs,
            max_months=MAX_DARK_MONTHS_DIFF, ignore_rot=True, ignore_filter=True
        )

        if not flat_master or not dark_master:
            logger.debug(f"Skipping calibration for {fits_file}: missing master (flat={flat_master}, dark={dark_master})")
            continue
        
        # grouping by (flat, dark, outpath)
        calibration_groups[(flat_master, dark_master, out_path, expt, filt, date_obs)].append((fits_file))
        
        
    # Get the total number of files for a given out_path
    targets = {out_path:  out_path / "calibration_log.json"  for (_, _, out_path, _, _, _), files in calibration_groups.items()}
    # Create a log file in the output directory for each target: out_path/calibration_log.json
    for target_path, calib_path in targets.items():
        if not calib_path.exists():
            with open(calib_path, 'w') as f:
                json.dump({"Target name": out_path.name}, f, indent=2)
    
    # Process each group
    for (flat_master, dark_master, out_path, expt, filt, date_obs), light_files in calibration_groups.items():
        logger.info(f"Calibrating {len(light_files)} {expt}s {filt} lights with flat={flat_master}, dark={dark_master}")
        
        # update calibration log
        calib_log_path = out_path / "calibration_log.json"
        with open(calib_log_path, 'r+') as f:
            log_data = json.load(f)
        
        # Initialize nested structure, if needed
        light_date = date_obs.strftime("%d-%m-%Y")
        if light_date not in log_data.keys():
            log_data[light_date] = {}
        log_date = log_data[light_date]
        if filt not in log_date:
            log_data[light_date][filt] = {}
        log_filter = log_data[light_date][filt]
        if expt not in log_filter:
            log_data[light_date][filt][expt] = {
                "flat_master": str(flat_master),
                "dark_master": str(dark_master),
                "num lights": 0,
                "num calibrated": 0,
                "failed lights": []
            }
        log_expt = log_data[light_date][filt][expt]
            
        # running calibration on light group
        calibrate_with_pixinsight(light_files, flat_master, dark_master, out_path, date_obs)

        for fits_file in light_files:
            calibrated = True
            calibrated_file_fits = out_path / fits_file.name.replace(".fits", f'__{date_obs.strftime("%d%m%Y")}_c.fits')
            calibrated_file_xisf = out_path / fits_file.name.replace(".fits", f'__{date_obs.strftime("%d%m%Y")}_c.xisf')
            if calibrated_file_fits.exists():
                logger.debug(f"Calibrated: {fits_file} -> {calibrated_file_fits}")
            elif calibrated_file_xisf.exists():
                logger.debug(f"Calibrated: {fits_file} -> {calibrated_file_xisf}")
            else:
                logger.error(f"Calibration failed for {fits_file}")
                calibrated = False
                log_expt["failed lights"].append(str(fits_file))
                continue
            log_expt["num calibrated"] += 1 if calibrated else 0
    
        # save updated calibration log
        with open(calib_log_path, 'w') as f:
            json.dump(log_data, f, indent=4)
    
    # Return paths to directories with new/updated calibration logs
    return [out_path for (_, _, out_path, _, _, _), _ in calibration_groups.items() if out_path.exists()]
            
            
def plot_summary(calibration_log_path: Path):
    """Creates several summary plots from the calibration log file.
    
    1.  The first is simply a table for each date with the number of calibrated lights per filter and exposure, and the number of failed lights.
        It will also indicate whether it's missing calibration frames (flat or dark).
        
    2.  The second is a bar chart showing the number of calibrated lights per filter and exposure for each date vs the number of total lights.
        It will also have a line indicating the cumulative hours of calibrated lights per date, per filter. 
        For the latter, it uses the exposure time and number of calibrated lights to compute the total available integration time. 
    
    3.  The third is a pie chart showing the distribution of calibrated lights by filter for the entire target.
        It will also indicate the total number of calibrated lights and total integration time in hours.
        
    Saves the plots in the same directory as the calibration log file in a folder titled "summary_plots".
    All are save as PNG files at 300 dpi.
    """
    # load calibration log
    with open(calibration_log_path, 'r') as f:
        log_data = json.load(f)
    
    # Prepare output directory
    output_dir = calibration_log_path.parent / "summary_plots"
    ensure_dir(output_dir)
    
    # === Plot 1: Table ===
    fig, ax = plt.subplots(figsize=(15, 6))
    ax.axis('off')
    table_data = []
    headers = ["Date", "Filter", "Exposure (s)", "Num Calibrated", "Num Failed", "Flat Master", "Dark Master"]
    for date, date_data in log_data.items():
        if date == "Target name":
            continue
        
        for filt, filt_data in date_data.items():
            for expt, expt_data in filt_data.items():
                num_cal = expt_data.get("num calibrated", 0)
                num_fail = len(expt_data.get("failed lights", []))
                flat_master = expt_data.get("flat_master", "Missing")
                if flat_master != "Missing":
                    flat_master = Path(flat_master).name
                dark_master = expt_data.get("dark_master", "Missing")
                if dark_master != "Missing":
                    dark_master = Path(dark_master).name
                table_data.append([date, filt, expt, num_cal, num_fail, flat_master, dark_master])
    
    # finding appropriate column widths
    col_widths = [max(len(str(row[i])) for row in table_data + [headers]) for i in range(len(headers))]

    table = ax.table(cellText=table_data, colLabels=headers, cellLoc='center', loc='center')
    table.auto_set_font_size(True)
    # table.set_fontsize(10)
    table.auto_set_column_width(col=list(range(len(headers))))
    table.scale(1.2, 1.2)
    
    # --- Resize figure to fit table ---
    fig.canvas.draw()  # need a draw before bbox is available
    bbox = table.get_window_extent(fig.canvas.get_renderer())

    # Convert from pixels to inches
    fig_width, fig_height = bbox.width / fig.dpi, bbox.height / fig.dpi

    # Add margins for title/axes
    margin_w, margin_h = 2, 2
    fig.set_size_inches(fig_width + margin_w, fig_height + margin_h)
    
    
    table.axes.set_title(f"Calibration Summary for {log_data['Target name']}", fontsize=15, y=0.85)
    plt.tight_layout()
    plt.savefig(output_dir / f"calibration_summary_table.png", bbox_inches='tight', dpi=300)
    plt.close()
        
    # === Plot 2: Bar chart across all dates ===
    records = []
    for date, date_data in log_data.items():
        if date == "Target name":
            continue
        for filt, filt_data in date_data.items():
            for expt, expt_data in filt_data.items():
                expt_int = int(float(expt))
                num_cal = expt_data.get("num calibrated", 0)
                num_fail = len(expt_data.get("failed lights", []))
                records.append({
                    "date": date,
                    "filter": filt,
                    "exposure": expt_int,
                    "num_calibrated": num_cal,
                    "num_total": num_cal + num_fail,
                    "seconds": num_cal * expt_int
                })

    if records:
        import pandas as pd
        df = pd.DataFrame(records)
        df = df.sort_values("date")

        # Compute cumulative hours across dates
        df["cumulative_hours"] = df["seconds"].cumsum() / 3600

        # Build x labels: date + filter + exposure
        df["label"] = df.apply(lambda r: f"{r['date']}\n{r['filter']} {r['exposure']}s", axis=1)
        x = range(len(df))

        fig, ax1 = plt.subplots(figsize=(max(12, len(df) * 0.6), 6))

        ax1.bar(x, df["num_total"], color="lightgray", label="Total Lights")
        ax1.bar(x, df["num_calibrated"], color="skyblue", label="Calibrated Lights")

        ax1.set_xticks(x)
        ax1.set_xticklabels(df["label"], rotation=45, ha="right")
        ax1.set_ylabel("Number of Lights")
        ax1.legend(loc="upper left", framealpha=0.95)
        # ax1.grid(axis="y")

        ax2 = ax1.twinx()
        ax2.plot(x, df["cumulative_hours"], color="orange", marker="o", label="Cumulative Hours")
        ax2.set_ylabel("Cumulative Hours of Calibrated Lights")
        # change color of y-axis to match line
        ax2.yaxis.label.set_color("orange")
        ax2.tick_params(axis='y', colors="orange")
        ax2.spines['right'].set_color("orange")

        plt.title(f"Calibrated vs Total Lights Over Time for {log_data['Target name']}", fontsize=14)
        plt.tight_layout()
        plt.savefig(output_dir / f"calibration_barchart.png", dpi=300)
        plt.close()
        
    # === Plot 3: Pie chart for entire target ===
    filter_seconds = defaultdict(int)
    total_seconds = 0
    for date, date_data in log_data.items():
        if date == "Target name":
            continue
        for filt, filt_data in date_data.items():
            for expt, expt_data in filt_data.items():
                expt_int = int(float(expt))
                num_cal = expt_data.get("num calibrated", 0)
                filter_seconds[filt] += num_cal * expt_int
                total_seconds += num_cal * expt_int

    if not filter_seconds:
        logger.warning(f"No calibrated lights found for target {log_data['Target name']}. Skipping pie chart.")
        return

    fig, ax = plt.subplots(figsize=(8, 8))
    labels = list(filter_seconds.keys())
    sizes = list(filter_seconds.values())

    # Make the pie slices
    wedges, _ = ax.pie(sizes, startangle=140)

    # Custom labels inside wedges
    for i, (wedge, filt) in enumerate(zip(wedges, labels)):
        pct = sizes[i] / sum(sizes) * 100
        hours = sizes[i] / 3600

        # Position label at the wedge center (closer to inside)
        ang = (wedge.theta2 + wedge.theta1) / 2.
        x = 0.7 * np.cos(np.deg2rad(ang))
        y = 0.7 * np.sin(np.deg2rad(ang))

        # Filter name (bold, bigger)
        ax.text(
            x, y + 0.08, filt,
            ha='center', va='center',
            fontsize=12, fontweight='bold', color="white"
        )

        # % and hours below
        ax.text(
            x, y - 0.05, f"{pct:.1f}%\n{hours:.1f}h",
            ha='center', va='center',
            fontsize=10, color="white"
        )

    ax.axis('equal')
    plt.title(
        f"Distribution of Calibrated Lights by Filter for {log_data['Target name']}\n"
        f"Total Integration Time: {total_seconds/3600:.2f} hours",
        fontsize=14
    )
    plt.tight_layout()
    plt.savefig(output_dir / "calibration_chart.png", dpi=300)
    plt.close()
    

def main():
    logger.info("Starting calibration process...")
    calibrate_lights(LIGHT_BASE_DIR, FLATS_DIR, DARKS_DIR, CALIBRATED_OUTPUT_DIR)
    logger.info("Calibration process finished.")

    # plot summary for each output directory
    for output_dir in CALIBRATED_OUTPUT_DIR.glob("*"):
        if output_dir.is_dir():
            calib_log_path = output_dir / "calibration_log.json"
            if calib_log_path.exists():
                plot_summary(calib_log_path)
                logger.info(f"Summary plot saved for {output_dir}")


if __name__ == "__main__":
    main()
import matplotlib.pyplot as plt
import numpy as np
import json
import logging
from pathlib import Path
from collections import defaultdict
from astro_utils.file_ops import ensure_dir

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


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
    
    fig, ax = plt.subplots(figsize=(15, 6))
    ax.axis('off')
    table = ax.table(cellText=table_data, colLabels=headers, cellLoc='center', loc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
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
    
    # table.axes.set_title(f"Calibration Summary for {log_data['Target name']}", fontsize=15, y=0.85)
    plt.tight_layout()
    plt.savefig(output_dir / f"calibration_summary_table_{log_data['Target name']}.png", bbox_inches='tight', dpi=300)
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
        plt.savefig(output_dir / f"calibration_barchart_{log_data['Target name']}.png", dpi=300)
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
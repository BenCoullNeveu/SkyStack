#!/usr/bin/env python3
"""
SubframeSelector automation for SkyStack
Automated subframe selection using PixInsight with GUI interface

Usage:
    python subframe_selector.py
    python subframe_selector.py --target /path/to/lights --filter Ha
    python subframe_selector.py --batch-mode
"""

import os
import sys
import json
import glob
import argparse
import logging
from pathlib import Path
from datetime import datetime

# SkyStack imports
from astro_utils.config_loader import load_config
from astro_utils.file_ops import ensure_dir, get_target_name
from astro_utils.fits_helpers import read_fits_header_info
from astro_utils.pixinsight_cli import run_pixinsight_cli
from astro_utils.subframe_utils import SubframeProcessor
from astro_utils.filter_config import get_filter_restrictions

# GUI imports (optional)
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    print("Warning: tkinter not available. GUI mode disabled.")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SubframeSelectorRunner:
    """Main SubframeSelector runner following SkyStack architecture"""
    
    def __init__(self, config_path="config.yaml"):
        """Initialize with configuration"""
        self.config = load_config(config_path)
        self.temp_dir = Path("C:/Temp/PixStack")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Equipment configuration
        self.camera_config = {
            "gain": 100,
            "pixel_size": 3.76,
            "sampling": 1.22,
            "resolution": "16bit"
        }
        
        # Telescope configuration  
        self.telescope_config = {
            "name": "Skywatcher Esprit 100 ED",
            "focal_length": 550,
            "aperture": 100
        }
        
    def find_fits_files(self, directory):
        """Find all FITS files in directory using reliable Python glob"""
        patterns = ["*.fit", "*.fits", "*.FIT", "*.FITS"]
        files = []
        
        logger.info(f"🔍 Searching for FITS files in: {directory}")
        
        for pattern in patterns:
            search_path = os.path.join(directory, pattern)
            found_files = glob.glob(search_path)
            files.extend(found_files)
            
        # Remove duplicates and sort
        files = sorted(list(set(files)))
        
        logger.info(f"📊 Found {len(files)} FITS files")
        for i, file in enumerate(files[:5]):  # Show first 5
            logger.info(f"  {i+1}. {os.path.basename(file)}")
        if len(files) > 5:
            logger.info(f"  ... and {len(files)-5} more")
            
        return files
        
    def create_pixinsight_config(self, files, filter_type, source_dir):
        """Create configuration JSON for PixInsight script"""
        
        restrictions = get_filter_restrictions(filter_type)
        output_dir = os.path.join(source_dir, "SFS")
        ensure_dir(output_dir)
        
        config = {
            # Files to process
            "input_files": files,
            "file_count": len(files),
            
            # Filter configuration
            "filter": {
                "type": filter_type,
                "name": restrictions["name"],
                "restrictions": restrictions
            },
            
            # Equipment configuration
            "camera": self.camera_config,
            "telescope": self.telescope_config,
            
            # Output configuration
            "output": {
                "directory": output_dir,
                "approved_dir": os.path.join(output_dir, "Approved"),
                "rejected_dir": os.path.join(output_dir, "Rejected"), 
                "reports_dir": os.path.join(output_dir, "Reports"),
                "prefix": "SFS_",
                "suffix": f"_{filter_type}",
                "extension": ".fit"
            },
            
            # Processing parameters
            "processing": {
                "approval_expression": self._build_approval_expression(restrictions),
                "weighting_expression": self._build_weighting_expression(),
                "generate_csv": True,
                "csv_filename": f"subframe_analysis_{filter_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            },
            
            # Metadata
            "metadata": {
                "created": datetime.now().isoformat(),
                "source_directory": source_dir,
                "skystack_version": "1.0.0"
            }
        }
        
        # Create output directories
        for dir_path in [config["output"]["approved_dir"], 
                        config["output"]["rejected_dir"],
                        config["output"]["reports_dir"]]:
            ensure_dir(dir_path)
        
        # Write configuration to JSON
        config_path = self.temp_dir / "subframe_config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
            
        logger.info(f"✅ Configuration written to: {config_path}")
        return config_path
        
    def _build_approval_expression(self, restrictions):
        """Build PixInsight approval expression from filter restrictions"""
        return (f"FWHM <= {restrictions['fwhm']} && "
                f"Eccentricity <= {restrictions['eccentricity']} && "
                f"SNRWeight >= {restrictions['snr']} && "
                f"Stars >= {restrictions['stars']}")
                
    def _build_weighting_expression(self):
        """Build PixInsight weighting expression"""
        return ("(15*(1-(FWHM-FWHMMin)/(FWHMMax-FWHMMin)))*"
                "(1-(Eccentricity-EccentricityMin)/(EccentricityMax-EccentricityMin))*"
                "(1-(MedianDev-MedianDevMin)/(MedianDevMax-MedianDevMin))")
    
    def run_subframe_selection(self, source_directory, filter_type):
        """Main subframe selection process"""
        
        logger.info("="*60)
        logger.info("🚀 SKYSTACK SUBFRAME SELECTOR")
        logger.info("="*60)
        logger.info(f"Filter: {filter_type}")
        logger.info(f"Source: {source_directory}")
        logger.info("")
        
        try:
            # Step 1: Find FITS files
            logger.info("STEP 1: Finding FITS files...")
            fits_files = self.find_fits_files(source_directory)
            
            if not fits_files:
                raise ValueError(f"No FITS files found in {source_directory}")
                
            # Step 2: Create PixInsight configuration
            logger.info("STEP 2: Creating PixInsight configuration...")
            config_path = self.create_pixinsight_config(fits_files, filter_type, source_directory)
            
            # Step 3: Run PixInsight CLI
            logger.info("STEP 3: Running PixInsight SubframeSelector...")
            script_path = "pixscripts/subframe_selector_script.js"
            
            success = run_pixinsight_cli(script_path)
            
            if success:
                logger.info("✅ SubframeSelector completed successfully!")
                
                # Step 4: Generate summary report
                self._generate_summary_report(source_directory, filter_type)
                
                return True
            else:
                logger.error("❌ PixInsight execution failed")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error: {str(e)}")
            return False
            
    def _generate_summary_report(self, source_dir, filter_type):
        """Generate summary report after processing"""
        
        sfs_dir = os.path.join(source_dir, "SFS")
        approved_dir = os.path.join(sfs_dir, "Approved")
        rejected_dir = os.path.join(sfs_dir, "Rejected")
        
        # Count files
        approved_count = len(glob.glob(os.path.join(approved_dir, "*.fit*")))
        rejected_count = len(glob.glob(os.path.join(rejected_dir, "*.fit*")))
        total_count = approved_count + rejected_count
        
        if total_count > 0:
            approval_rate = (approved_count / total_count) * 100
        else:
            approval_rate = 0
            
        logger.info("")
        logger.info("📊 SUMMARY REPORT")
        logger.info("-" * 40)
        logger.info(f"Filter: {filter_type}")
        logger.info(f"Total processed: {total_count}")
        logger.info(f"Approved: {approved_count}")
        logger.info(f"Rejected: {rejected_count}")
        logger.info(f"Approval rate: {approval_rate:.1f}%")
        logger.info(f"Output directory: {sfs_dir}")
        logger.info("")
        
    def run_gui_mode(self):
        """Run GUI interface for SubframeSelector"""
        
        if not GUI_AVAILABLE:
            print("GUI mode not available. Run with command line arguments instead.")
            return False
            
        from gui.subframe_selector_gui import SubframeSelectorGUI
        
        root = tk.Tk()
        app = SubframeSelectorGUI(root, self)
        root.mainloop()
        
        return True


def main():
    """Main entry point"""
    
    parser = argparse.ArgumentParser(description="SkyStack SubframeSelector")
    parser.add_argument("--target", type=str, help="Target directory containing FITS files")
    parser.add_argument("--filter", type=str, choices=["L", "R", "G", "B", "Ha", "OIII", "SII"], 
                       help="Filter type")
    parser.add_argument("--batch-mode", action="store_true", 
                       help="Process all targets in batch mode")
    parser.add_argument("--gui", action="store_true", 
                       help="Run GUI interface")
    parser.add_argument("--config", type=str, default="config.yaml", 
                       help="Configuration file path")
    
    args = parser.parse_args()
    
    # Initialize runner
    runner = SubframeSelectorRunner(args.config)
    
    if args.gui or (not args.target and not args.batch_mode):
        # Run GUI mode
        return runner.run_gui_mode()
        
    elif args.batch_mode:
        # Batch mode - process all targets
        logger.info("🔄 Running in batch mode...")
        # Implementation for batch processing
        return True
        
    elif args.target and args.filter:
        # Command line mode
        return runner.run_subframe_selection(args.target, args.filter)
        
    else:
        parser.print_help()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

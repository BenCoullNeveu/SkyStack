# Transfers files -> creates master calibrations

import logging
import subprocess
from pathlib import Path

skystack_dir = Path(__file__).parent

# set up logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

# run transfer routine
logger.info("Starting transfer routine...")
cmd = ["python", str(skystack_dir / "auto_transfer.py")]
subprocess.run(cmd, check=True)

# -> frame selection

# run create master calibrations routine
# FLATS
logger.info("Creating master FLATS...")
cmd = ["python", str(skystack_dir / "stack_flats.py")]
subprocess.run(cmd, check=True)
# DARKS
logger.info("Creating master DARKS...")
cmd = ["python", str(skystack_dir / "stack_darks.py")]
subprocess.run(cmd, check=True)

# create master index parquets
logger.info("Creating master index parquets...")
cmd = ["python", str(skystack_dir / "create_master_index_parquets.py")] 
subprocess.run(cmd, check=True)
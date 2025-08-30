#!/usr/bin/env python3
# import argparse
import os
from pathlib import Path
import shutil
import sys
import logging
import subprocess
import time
from astro_utils.config_loader import load_config

config_path = Path(__file__).parent / "config.yaml"
config = load_config(config_path)

# extract config values
DROPBOX_SOURCE = Path(config.get("dropbox_source"))
LOCAL_TARGET = Path(config.get("local_target"))
SUBDIRS = config.get("subdirs")
DELETE_AFTER_TRANSFER = config.get("delete_after_transfer")
REMOVE_EMPTY_DIRS = config.get("remove_empty_dirs")
REVERSE_TRANSFER = config.get("reverse_transfer")
SPECIFIC_DIR_KEYWORDS = config.get("specific_dir_keywords", [])

if REVERSE_TRANSFER:
    temp = DROPBOX_SOURCE
    DROPBOX_SOURCE = LOCAL_TARGET 
    LOCAL_TARGET = temp

print(f"Source: {DROPBOX_SOURCE} >>> EXISTS: {DROPBOX_SOURCE.exists()}")
print(f"Destination: {LOCAL_TARGET} >>> EXISTS: {LOCAL_TARGET.exists()}")
print(f"Delete after transfer: {DELETE_AFTER_TRANSFER}\n")
                
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)



def _ensure_local(file: Path):
    """Force Dropbox to download a file if it's cloud-only."""
    try:
        if file.exists():
            # This will force Dropbox to download the file
            subprocess.run(["attrib", "-P", str(file)], shell=True)
    except Exception as e:
        logger.warning(f"Could not ensure local copy for {file}: {e}")


def _remove_empty_dirs(path: Path, protect_root: bool = False):
    """
    Recursively remove empty directories starting from `path`.
    Treats folders that only contain 'desktop.ini' as empty.
    """
    if not path.exists():
        return

    for child in list(path.iterdir()):
        if child.is_dir():
            _remove_empty_dirs(child, protect_root=False)

    # Collect contents excluding desktop.ini
    items = list(path.iterdir())
    non_desktop_items = [item for item in items if item.name.lower() != "desktop.ini"]

    if not non_desktop_items:
        try:
            for item in items:
                if item.name.lower() == "desktop.ini":
                    item.unlink(missing_ok=True)
            if not protect_root:
                path.rmdir()
                logger.info(f"Removed empty directory: {path}")
        except Exception as e:
            logger.warning(f"Could not remove {path}: {e}")


def _cleanup_subdirs(source: Path, subdirs):
    """Remove all empty folders inside specific subdirectories."""
    for sub in subdirs:
        target_dir = source / sub
        if target_dir.exists() and target_dir.is_dir():
            _remove_empty_dirs(target_dir, protect_root=True)


def _move_with_retry(src: Path, dst: Path, delete_after_transfer: bool, retries=3):
    for attempt in range(retries):
        try:
            _ensure_local(src)
            shutil.copy2(src, dst)
            if delete_after_transfer:
                os.remove(src)
            return
        except OSError as e:
            if attempt < retries - 1:
                logger.warning(f"Retrying move due to {e}...")
                time.sleep(5)
            else:
                raise

def transfer_files(source: Path, 
                   dest: Path, 
                   subdirs: list|str, 
                   delete_after_transfer: bool,
                   cleanup_sources: bool,
                   specific_dir_keywords: list[str]):
    """Move files from source to dest. Skips duplicates."""
    if not source.exists():
        logger.error(f"Source directory does not exist: {source}")
        sys.exit(1)
    dest.mkdir(parents=True, exist_ok=True)

    count = 0
    for subdir in subdirs:
        subdir_path = source / subdir
        if not subdir_path.exists() or not subdir_path.is_dir():
            logger.info(f"Skipping missing or invalid subdirectory: {subdir}")
            continue
        logger.info(f"{subdir}/")
        last_parent = None
        for file in sorted(subdir_path.rglob("*")):
            # check for specific keyword
            if all(keyword in file.parts for keyword in specific_dir_keywords):
                if file.is_file():
                    rel_path = file.relative_to(source)
                    dest_file = dest / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)

                    # Print directory header if we’re in a new first-level directory
                    if rel_path.parts[0] != last_parent:
                        last_parent = rel_path.parts[0]

                    if dest_file.exists():
                        logger.info(f"    ├── {rel_path.name} (skipped; exists)")
                        continue

                    _move_with_retry(file, dest_file, delete_after_transfer, retries=3)
                    logger.info(f"    ├── {rel_path.name}")
                    count += 1
                
        # After moving files, remove the subdirectory if empty
        if cleanup_sources:
            _cleanup_subdirs(source, subdirs)
                
    logger.info(f"\n  >>> Transfer complete. {count} files moved.\n")


def main():
    # parser = argparse.ArgumentParser(description="Transfer files from Dropbox to local C: drive")
    # parser.add_argument("source", type=Path,
    #                     default=DROPBOX_SOURCE, 
    #                     help="Source Dropbox directory")
    # parser.add_argument("dest", type=Path, 
    #                     default=LOCAL_TARGET,
    #                     help="Destination directory on C: drive")
    # parser.add_argument("--keep-source", 
    #                     action="store_true", 
    #                     help="Do not delete source after transfer")
    # args = parser.parse_args()

    transfer_files(DROPBOX_SOURCE, 
                   LOCAL_TARGET, 
                   subdirs=SUBDIRS, 
                   delete_after_transfer=DELETE_AFTER_TRANSFER,
                   cleanup_sources=REMOVE_EMPTY_DIRS,
                   specific_dir_keywords=SPECIFIC_DIR_KEYWORDS)

if __name__ == "__main__":
    main()
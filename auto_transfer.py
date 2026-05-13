#!/usr/bin/env python3
# import argparse
from pathlib import Path
import sys
import logging
from astro_utils.config_loader import load_config
from astro_utils.file_ops import safe_move, ensure_local, cleanup_subdirs, move_with_retry

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
            if specific_dir_keywords is None or all(keyword in file.parts for keyword in specific_dir_keywords):
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

                    move_with_retry(file, dest_file, delete_after_transfer, retries=3)
                    logger.info(f"    ├── {rel_path.name}")
                    count += 1
                
        # After moving files, remove the subdirectory if empty
        if cleanup_sources:
            cleanup_subdirs(source, subdirs)
                
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
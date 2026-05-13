from pathlib import Path
import shutil
import logging
import subprocess
import os
import time

logger = logging.getLogger(__name__)

logging.basicConfig(level="DEBUG",
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

def safe_move(src: Path, dest: Path, overwrite: bool = False):
    """Move a file from src to dest safely.

    - Creates parent directories as needed.
    - If dest exists and overwrite=False, appends `_copyN` to the filename.
    - Returns the actual destination path used.
    """
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not overwrite:
        base = dest.stem
        suf = dest.suffix
        parent = dest.parent
        i = 1
        while True:
            candidate = parent / f"{base}_copy{i}{suf}"
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    shutil.move(str(src), str(dest))
    logger.debug("Moved %s -> %s", src, dest)
    return dest


def safe_copy(src: Path, dest: Path, overwrite: bool = False):
    """Copy src -> dest, similar semantics to safe_move."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not overwrite:
        base = dest.stem
        suf = dest.suffix
        parent = dest.parent
        i = 1
        while True:
            candidate = parent / f"{base}_copy{i}{suf}"
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    shutil.copy2(str(src), str(dest))
    logger.debug("Copied %s -> %s", src, dest)
    return dest

def ensure_dir(path: Path):
    """Ensure the directory exists, creating it if necessary."""
    path = Path(path)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        logger.debug("Created directory: %s", path)
    else:
        logger.debug("Directory already exists: %s", path)
    return path

def get_target_name(path:Path):
    """get the target name from the path. 
    Assume the target name is the child directory name of `LIGHT`."""
    path = Path(path)
    if "LIGHT" in path.parts:
        light_index = path.parts.index("LIGHT")
        if light_index + 1 < len(path.parts):
            return path.parts[light_index + 1]
    raise ValueError(f"Path {path} does not contain 'LIGHT' directory or has no child directory after it.")


def file_exists_in_tree(file_name: str, dir: Path):
    """Check if a file with the given name exists anywhere in the directory tree."""
    dir = Path(dir)
    for f in dir.rglob(file_name):
        if f.is_file():
            return True
    return False

def ensure_local(file: Path):
    """Force Dropbox to download a file if it's cloud-only."""
    try:
        if file.exists():
            # This will force Dropbox to download the file
            subprocess.run(["attrib", "-P", str(file)], shell=True)
    except Exception as e:
        logger.warning(f"Could not ensure local copy for {file}: {e}")


def remove_empty_dirs(path: Path, protect_root: bool = False):
    """
    Recursively remove empty directories starting from `path`.
    Treats folders that only contain 'desktop.ini' as empty.
    """
    if not path.exists():
        return

    for child in list(path.iterdir()):
        if child.is_dir():
            remove_empty_dirs(child, protect_root=False)

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
            
            
def cleanup_subdirs(source: Path, subdirs):
    """Remove all empty folders inside specific subdirectories."""
    for sub in subdirs:
        target_dir = source / sub
        if target_dir.exists() and target_dir.is_dir():
            remove_empty_dirs(target_dir, protect_root=True)
            
def remove_file_with_retry(file_path: Path, retries=3):
    for attempt in range(retries):
        try:
            os.remove(file_path)
            return
        except OSError as e:
            if attempt < retries - 1:
                logger.warning(f"Retrying delete due to {e}...")
                time.sleep(5)
            else:
                raise
            
def move_with_retry(src: Path, dst: Path, delete_after_transfer: bool, retries=3):
    for attempt in range(retries):
        try:
            ensure_local(src)
            shutil.copy2(src, dst)
            if delete_after_transfer:
                remove_file_with_retry(src)
            return
        except OSError as e:
            if attempt < retries - 1:
                logger.warning(f"Retrying move due to {e}...")
                time.sleep(5)
            else:
                raise
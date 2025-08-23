from pathlib import Path
import shutil
import logging

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
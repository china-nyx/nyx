"""Smoke check — import every module in a code tree to verify no breakage.

Auto-discovers top-level packages (directories with __init__.py).
No hard-coded package names."""
import glob
import importlib
import os
import sys
from pathlib import Path
from typing import List, Set, Tuple


# Files that are entry-points or side-effect heavy — skip during smoke.
_SKIP_FILES: Set[str] = frozenset({
    "__init__.py",
    "__main__.py",
    "boot.py",
})


def _discover_modules(root: str) -> List[str]:
    """Return dotted module names for every .py file under top-level packages in *root*."""
    root_path = Path(root).resolve()
    mods: List[str] = []

    for entry in sorted(root_path.iterdir()):
        if not entry.is_dir():
            continue
        init = entry / "__init__.py"
        if not init.is_file():
            continue
        # Walk all .py files under this package (one level deep is enough for flat packages)
        for py in glob.glob(str(entry / "*.py")):
            base = os.path.basename(py)
            if base not in _SKIP_FILES:
                rel = os.path.relpath(py, root)
                mods.append(rel[:-3].replace(os.sep, "."))

    return sorted(mods)


def run(code_dir: str) -> Tuple[bool, str]:
    """Import every module in *code_dir* in-process.  Returns (ok, detail)."""
    if not Path(code_dir).is_dir():
        return False, f"code dir not found: {code_dir}"

    mods = _discover_modules(code_dir)
    if not mods:
        return False, "no modules discovered"

    old_path = list(sys.path)
    imported: List[str] = []
    try:
        sys.path.insert(0, code_dir)
        for m in mods:
            importlib.import_module(m)
            imported.append(m)
        return True, f"smoke ok ({len(mods)} modules)"
    except Exception as e:
        return False, f"smoke failed: {e}"
    finally:
        sys.path[:] = old_path
        for m in reversed(imported):
            sys.modules.pop(m, None)

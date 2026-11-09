"""
Path containment helper shared by the engine's file-access gatekeepers.

`resolved.startswith(base)` is not a containment check: for base
"/x/scratchpad" it also accepts "/x/scratchpad_evil/...". Compare whole
path components instead.
"""

import os


def is_within(base, path):
    """
    True if `path` resolves (following symlinks) to `base` or something
    inside it. Both arguments may be relative; they are resolved first.
    """
    base_real = os.path.realpath(base)
    path_real = os.path.realpath(path)
    try:
        return os.path.commonpath([base_real, path_real]) == base_real
    except ValueError:
        # Different drives on Windows, or mix of absolute/relative.
        return False

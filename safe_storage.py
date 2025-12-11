import json
import os
import shutil
import uuid
from typing import Any, Dict, Optional

from config import BACKUP_SUFFIX, TMP_SUFFIX


def safe_save_json(path: str, data: Dict[str, Any]) -> bool:
    """Write JSON atomically with temp file, fsync, and backup."""

    tmp_path = f"{path}{TMP_SUFFIX}.{uuid.uuid4()}"
    backup_path = f"{path}{BACKUP_SUFFIX}"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except Exception:
        return False
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            shutil.copy2(path, backup_path)
        os.replace(tmp_path, path)
        return True
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False


def safe_load_json(path: str) -> Optional[Dict[str, Any]]:
    """Load JSON with backup fallback if the main file is corrupted."""

    backup_path = f"{path}{BACKUP_SUFFIX}"
    candidate_paths = [path, backup_path]
    for candidate in candidate_paths:
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None

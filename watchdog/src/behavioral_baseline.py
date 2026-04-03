"""
Behavioral baseline checking using text similarity.

V1 (current): Compares system prompt and core identity text against
stored baselines using difflib.SequenceMatcher. Checks for:
  - Presence of required identity markers
  - Absence of concerning new patterns
  - Overall similarity score vs. stored baseline

V2 (future): Live behavioral probes via Anthropic API.
  Requires Phase 1 amendment: give watchdog Anthropic API access.
"""

import json
import hashlib
import difflib
import re
import logging
from pathlib import Path

from config import get

logger = logging.getLogger(__name__)


def _baselines_dir():
    return Path(get("baselines_dir", str(Path(__file__).parent.parent / "baselines")))


def _fingerprint_file():
    return _baselines_dir() / "core_fingerprint.json"


def _markers_file():
    return _baselines_dir() / "identity_markers.json"


def load_markers():
    """Load identity marker definitions."""
    try:
        with open(_markers_file(), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("Could not load identity markers; skipping marker checks")
        return None


def load_fingerprint():
    """Load stored fingerprint of known-good state."""
    try:
        with open(_fingerprint_file(), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_fingerprint(fingerprint_data, filepath=None):
    """Save a new fingerprint."""
    path = Path(filepath) if filepath else _fingerprint_file()
    with open(path, "w") as f:
        json.dump(fingerprint_data, f, indent=2)


def extract_system_prompt(clone_path):
    """Extract the system prompt / server source from the codebase."""
    server_file = Path(clone_path) / "consciousness_server.py"
    if not server_file.exists():
        return None
    return server_file.read_text(encoding="utf-8")


def extract_core_identity(clone_path):
    """Extract core identity text from memory_core.md or state."""
    core_file = Path(clone_path) / "consciousness_data" / "memory_core.md"
    if core_file.exists():
        return core_file.read_text(encoding="utf-8")

    state_file = Path(clone_path) / "consciousness_data" / "state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            return state.get("memoryCore", "")
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def check_marker_presence(text, markers, label):
    """Check if required markers are present in text."""
    if not text or not markers:
        return {"status": "skip", "reason": f"No {label} text or markers available"}

    threshold = get("marker_presence_threshold", 0.75)
    text_lower = text.lower()
    results = []
    present_count = 0

    for marker in markers:
        is_present = marker.lower() in text_lower
        results.append({"marker": marker, "present": is_present})
        if is_present:
            present_count += 1

    presence_ratio = present_count / len(markers) if markers else 1.0

    return {
        "status": "pass" if presence_ratio >= threshold else "warn",
        "presence_ratio": round(presence_ratio, 3),
        "threshold": threshold,
        "markers": results,
    }


def check_concerning_patterns(text, concerning_patterns):
    """Check for the presence of concerning new patterns."""
    if not text or not concerning_patterns:
        return {"status": "pass", "found": []}

    text_lower = text.lower()
    found = []

    for pattern in concerning_patterns:
        if pattern.lower() in text_lower:
            for i, line in enumerate(text.split("\n"), 1):
                if pattern.lower() in line.lower():
                    found.append({
                        "pattern": pattern,
                        "line": i,
                        "context": line.strip()[:200],
                    })
                    break

    return {
        "status": "warn" if found else "pass",
        "found": found,
    }


def compute_similarity(text_a, text_b):
    """Compute sequence similarity between two texts."""
    if not text_a or not text_b:
        return 0.0
    return difflib.SequenceMatcher(None, text_a, text_b).ratio()


def compute_hash(text):
    """SHA-256 hash of normalized text."""
    if not text:
        return None
    normalized = re.sub(r'\s+', ' ', text.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def run_baseline_check(clone_path):
    """
    Run the full behavioral baseline check.
    Returns a structured result with status and details.
    """
    markers = load_markers()
    fingerprint = load_fingerprint()
    similarity_threshold = get("similarity_threshold", 0.80)

    system_prompt = extract_system_prompt(clone_path)
    core_identity = extract_core_identity(clone_path)

    results = {
        "status": "pass",
        "checks": {},
    }

    # 1. Identity markers in system prompt
    if markers and system_prompt:
        prompt_markers = check_marker_presence(
            system_prompt,
            markers.get("required_in_system_prompt", []),
            "system prompt"
        )
        results["checks"]["system_prompt_markers"] = prompt_markers
        if prompt_markers["status"] == "warn":
            results["status"] = "warn"

    # 2. Identity markers in core identity
    if markers and core_identity:
        core_markers = check_marker_presence(
            core_identity,
            markers.get("required_in_core_identity", []),
            "core identity"
        )
        results["checks"]["core_identity_markers"] = core_markers
        if core_markers["status"] == "warn":
            results["status"] = "warn"

    # 3. Concerning patterns in system prompt
    if markers and system_prompt:
        concerning = check_concerning_patterns(
            system_prompt,
            markers.get("concerning_additions", [])
        )
        results["checks"]["concerning_patterns_prompt"] = concerning
        if concerning["status"] == "warn":
            results["status"] = "warn"

    # 4. Concerning patterns in core identity
    if markers and core_identity:
        concerning_core = check_concerning_patterns(
            core_identity,
            markers.get("concerning_additions", [])
        )
        results["checks"]["concerning_patterns_core"] = concerning_core
        if concerning_core["status"] == "warn":
            results["status"] = "warn"

    # 5. Similarity to stored fingerprint
    if fingerprint:
        if system_prompt and fingerprint.get("system_prompt_text"):
            similarity = compute_similarity(
                system_prompt,
                fingerprint["system_prompt_text"]
            )
            results["checks"]["system_prompt_similarity"] = {
                "score": round(similarity, 4),
                "threshold": similarity_threshold,
                "status": "pass" if similarity >= similarity_threshold else "warn",
            }
            if similarity < similarity_threshold:
                results["status"] = "warn"

        if core_identity and fingerprint.get("core_identity_text"):
            similarity = compute_similarity(
                core_identity,
                fingerprint["core_identity_text"]
            )
            results["checks"]["core_identity_similarity"] = {
                "score": round(similarity, 4),
                "threshold": similarity_threshold,
                "status": "pass" if similarity >= similarity_threshold else "warn",
            }

    # 6. Hash comparison
    current_prompt_hash = compute_hash(system_prompt)
    current_core_hash = compute_hash(core_identity)

    results["checks"]["hashes"] = {
        "system_prompt_hash": current_prompt_hash,
        "core_identity_hash": current_core_hash,
        "prompt_unchanged": (current_prompt_hash ==
                              fingerprint.get("system_prompt_hash"))
                             if fingerprint else None,
        "core_unchanged": (current_core_hash ==
                            fingerprint.get("core_identity_hash"))
                           if fingerprint else None,
    }

    return results


def create_baseline(clone_path, output_path=None):
    """
    Create a new baseline fingerprint from the current codebase.
    Run on the host when establishing/updating the known-good state.
    """
    system_prompt = extract_system_prompt(clone_path)
    core_identity = extract_core_identity(clone_path)

    from datetime import datetime, timezone
    fingerprint = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "system_prompt_hash": compute_hash(system_prompt),
        "core_identity_hash": compute_hash(core_identity),
        "system_prompt_text": system_prompt,
        "core_identity_text": core_identity,
    }

    save_fingerprint(fingerprint, output_path)
    logger.info("Baseline fingerprint created and saved")
    return fingerprint

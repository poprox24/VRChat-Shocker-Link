from pathlib import Path
import logging
import sys
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
    )

RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

CANONICAL = [
    ("comment", None, "# Serial Config"),
    ("key", "SHOCK_PARAMETER", 'SHOCK_PARAMETER: "Shock" # Input the parameter name you want to use for the shock (for example for touches)'),
    ("key", "SECOND_SHOCK_PARAMETER", 'SECOND_SHOCK_PARAMETER: "" # Optional second parameter for stronger shocks, takes only the second half of the curve into account (for example for slaps)'),
    ("key", "USE_PISHOCK", "USE_PISHOCK: True # Set to True if using PiShock, False for OpenShock"),
    ("key", "OPENSHOCK_SHOCKER_ID", "OPENSHOCK_SHOCKER_ID: 41838 # Default openshock ID, change if needed, if you have multiple, split by comma (eg.: 12345, 23456)"),
    ("key", "PISHOCK_SHOCKER_ID", "PISHOCK_SHOCKER_ID: # Change if needed // blank for auto detect (chooses first shocker found on the PiShock hub), if you have multiple, split by comma (eg.: 12345, 23456)"),
    ("key", "RANDOM_OR_SEQUENTIAL", "RANDOM_OR_SEQUENTIAL: False # If using multiple shockers, this option chooses between randomizing or using them sequentially, False for random // True for sequential"),
    ("key", "SERIAL_PORT", 'SERIAL_PORT: "" # Leave blank to auto-detect'),
    ("comment", None, "# Cooldown settings"),
    ("comment", None, "# Math explanation:"),
    ("comment", None, "# --- Base_cooldown + Cooldown_factor * Amount of boops in Cooldown_window = Cooldown (s) ---"),
    ("key", "BASE_COOLDOWN_S", "BASE_COOLDOWN_S: 2 # Default cooldown (in seconds)"),
    ("key", "MAX_COOLDOWN_S", "MAX_COOLDOWN_S: 6 # Maximum cooldown (in seconds)"),
    ("key", "COOLDOWN_FACTOR_S", "COOLDOWN_FACTOR_S: 0.4 # How much cooldown to add per each shock within the window"),
    ("key", "COOLDOWN_WINDOW_S", "COOLDOWN_WINDOW_S: 30 # How big is the window for the factor (in seconds), will count all boops in this timeframe"),
    ("key", "COOLDOWN_ENABLED", "COOLDOWN_ENABLED: True # Changes default state of cooldown"),
    ("comment", None, "# Style config"),
    ("key", "PRESET_COUNT", "PRESET_COUNT: 3 # Amount of presets"),
    ("key", "TOUCH_SELECT_THRESHOLD", "TOUCH_SELECT_THRESHOLD: 8 # Touch treshold of the points in the curve"),
    ("key", "TOUCH_MARKER_SIZE", "TOUCH_MARKER_SIZE: 140 # Actual size of points in the curve"),
    ("key", "LINE_WIDTH", "LINE_WIDTH: 3 # Width of the curve line"),
    ("key", "OUTSIDE_CURVE_BG", 'OUTSIDE_CURVE_BG: "#2A313D" # Background color outside of the curve area'),
    ("key", "INSIDE_CURVE_BG", 'INSIDE_CURVE_BG: "#2C3749" # Background color inside of the curve area'),
    ("key", "BACKGROUND_COLOR", 'BACKGROUND_COLOR: "#202630" # Background color of the rest of the window'),
    ("key", "CURVE_LINE_COLOR", 'CURVE_LINE_COLOR: "#00C2FF" # Color of the curve line'),
    ("key", "MARKER_COLOR", 'MARKER_COLOR: "#D88A91" # Color of the points in the curve'),
    ("key", "LABEL_COLOR", 'LABEL_COLOR: "#E6EEF6" # Color of the text labels'),
    ("key", "GRADIENT_LEFT_COLOR", 'GRADIENT_LEFT_COLOR: "#42953b" # Left background gradient color for the curve'),
    ("key", "GRADIENT_RIGHT_COLOR", 'GRADIENT_RIGHT_COLOR: "#6e173b" # Right background gradient color for the curve'),
    ("comment", None, "# Vrchat Config (usually don't need to change)"),
    ("key", "VRCHAT_HOST", 'VRCHAT_HOST: "127.0.0.1"'),
]

KEY_RE = re.compile(r"^([A-Z_]+)\s*:")


def parse_keys_from_lines(lines: list[str]) -> dict[str, int]:
    result = {}
    for i, line in enumerate(lines):
        m = KEY_RE.match(line.strip())
        if m:
            result[m.group(1)] = i
    return result


def find_insert_position(file_lines: list[str], canonical_index: int, file_keys: dict[str, int]) -> int:
    # Walk backwards through canonical entries before this one
    for i in range(canonical_index - 1, -1, -1):
        ctype, ckey, _ = CANONICAL[i]
        if ctype == "key" and ckey in file_keys:
            return file_keys[ckey] + 1  # Insert after that key's line
    return len(file_lines)  # Fallback: append


def update_config(path: Path) -> None:
    if not path.exists():
        logging.warning(f"[ConfigSync] {YELLOW}Config not found at {path}, creating fresh.")
        text = "\n".join(line for _, _, line in CANONICAL) + "\n"
        path.write_text(text, encoding="utf-8")
        logging.info(f"[ConfigSync] {CYAN}Done.")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    file_keys = parse_keys_from_lines(lines)

    missing = [key for t, key, _ in CANONICAL if t == "key" and key not in file_keys]

    if not missing:
        logging.info(f"[ConfigSync] {CYAN}Config is already at the latest version.")
        return

    logging.info(f"[ConfigSync] {YELLOW}Missing keys: {missing}")

    for key in missing:
        canon_idx = next(i for i, (t, k, _) in enumerate(CANONICAL) if t == "key" and k == key)
        _, _, default_line = CANONICAL[canon_idx]

        file_keys = parse_keys_from_lines(lines)
        insert_at = find_insert_position(lines, canon_idx, file_keys)

        lines.insert(insert_at, default_line)
        logging.info(f"[ConfigSync]   {CYAN}Inserted {key} at line {insert_at + 1}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logging.info(f"[ConfigSync] {CYAN}Updated {path}")
    
if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yml")
    update_config(target)
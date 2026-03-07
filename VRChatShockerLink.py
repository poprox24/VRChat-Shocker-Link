from VRC_OSCQuery import vrc_client, dict_to_dispatcher, start_osc
from pishock.zap.serialapi import SerialAutodetectError, SerialAPI
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from serial.serialutil import SerialException
from serial.tools import list_ports
import matplotlib.pyplot as plt
from queue import Queue, Empty
from tkinter import ttk
import tkinter as tk
import numpy as np
import threading
import logging
import random
import serial
import shutil
import time
import json
import yaml
import os

# Load config
config_path = "config.yml"
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
    )

RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"

try:
    config = yaml.safe_load(open(config_path)) or {}
except FileNotFoundError:
    logging.exception(f"{RED}Could not find config.yml file. Using default config")
    config = {}
except Exception as e:
    logging.exception(f"{RED}Could not load config.yml file. Using default config")
    config = {}

# Cleanup
to_be_deleted = {
    "vrchat_oscquery"
}
for item in to_be_deleted:
    if os.path.isdir(item):
        try:
            shutil.rmtree(os.path.abspath(item))
            logging.info(f"[Janitor] {CYAN}Deleted a no longer needed directory {item}.")
        except Exception as e:
            logging.warning(f"[Janitor] {YELLOW}Failed to delete a no longer needed directory {item}, please delete this folder manually.")


def return_list(x):
    if x is None:
        return []

    if isinstance(x, str):
        parts = [p.strip() for p in x.split(",")]
        return [p for p in parts if p]

    if isinstance(x, (list, tuple)):
        return list(x)

    return [x]
    
# --- NETWORK / Serial Config
USE_PISHOCK = config.get("USE_PISHOCK", False) # Use PiShock if True, else OpenShock
OPENSHOCK_SHOCKER_IDS = return_list(config.get("OPENSHOCK_SHOCKER_ID", [None])) # ID for OpenShock shockers

PISHOCK_SHOCKER_IDS = return_list(config.get("PISHOCK_SHOCKER_ID", []))
RANDOM_OR_SEQUENTIAL = config.get("RANDOM_OR_SEQUENTIAL", False)

OPENSHOCK_SERIAL_BAUDRATE = 115200
SERIAL_PORT = config.get("serial_port", "")
SHOCK_PARAM = f"/avatar/parameters/{config.get('SHOCK_PARAMETER', None)}" # OSC parameter to listen for shock trigger
SECOND_SHOCK_PARAM = f"/avatar/parameters/{config.get('SECOND_SHOCK_PARAMETER', None)}" # Seccond parameter for stronger shocks


VRCHAT_HOST = config.get("VRCHAT_HOST", "127.0.0.1")

# Base config
BASE_COOLDOWN_S = config.get("BASE_COOLDOWN_S", 2)
MAX_COOLDOWN_S = config.get("MAX_COOLDOWN_S", 6)
COOLDOWN_FACTOR_S = config.get("COOLDOWN_FACTOR_S", 0.4)
COOLDOWN_WINDOW_S = config.get("COOLDOWN_WINDOW_S", 30)
COOLDOWN_ENABLED = config.get("COOLDOWN_ENABLED", True)

UI_VIEW_MIN_PERCENT = 30
UI_VIEW_MAX_PERCENT = 68
UI_CONTROL_POINTS = [(36, 0.5), (45, 0.4), (59, 0.25)]

CONFIG_FILE_PATH = "curve_config.json"

# Style config
TOUCH_SELECT_THRESHOLD = config.get("TOUCH_SELECT_THRESHOLD", 8)
TOUCH_MARKER_SIZE = config.get("TOUCH_MARKER_SIZE", 120)
LINE_WIDTH = config.get("LINE_WIDTH", 3)
OUTSIDE_CURVE_BG = config.get("OUTSIDE_CURVE_BG", "#2A313D")
INSIDE_CURVE_BG = config.get("INSIDE_CURVE_BG", "#2C3749")
BACKGROUND_COLOR = config.get("BACKGROUND_COLOR", "#202630")
CURVE_LINE_COLOR = config.get("CURVE_LINE_COLOR", "#00C2FF")
MARKER_COLOR = config.get("MARKER_COLOR", "#D88A91")
LABEL_COLOR = config.get("LABEL_COLOR", "#E6EEF6")
PRESET_NORMAL_BG = config.get("PRESET_NORMAL_BG", "#202630")
PRESET_DEFAULT_BG = config.get("PRESET_DEFAULT_BG", "#2E8A57")
GRADIENT_LEFT_COLOR = config.get("GRADIENT_LEFT_COLOR", "#42953b")
GRADIENT_RIGHT_COLOR = config.get("GRADIENT_RIGHT_COLOR", "#6e173b")
PRESET_COUNT = config.get("PRESET_COUNT", 3)

# ~~~      VARIABLES      ~~~
# Drag/Edit state
dragging_index = None
highlight_index = None
right_click_input_widget = None
drag_context = {}

# Undo/Redo history
undo_history = []
redo_history = []

# Timestamps for trigger cooldown
trigger_timestamps = []
last_trigger_time = 0
    
# Presets
presets = [None] * PRESET_COUNT
preset_names = [f"Preset {i+1}" for i in range(PRESET_COUNT)]
default_preset_index = None
preset_buttons = []
preset_save_buttons = []

# Serial
pishock_api = None
shockers = None
serial_connection = None        # Shocker Serial Connection
shockers = []                   # Shocker List
serial_q = Queue()              # Serial Queue
serial_stop = threading.Event() # Serial stop for shutdown logic

# Shocker
last_shocker_index = -1         # Last shocker used for sequential firing
shock_q = Queue()               # Shocker queue
shocker_stop = threading.Event()# Shocker stop for shutdown logic

MIN_SHOCK_DURATION = -1
MAX_SHOCK_DURATION = -1
MESSAGE_COOLDOWN = 1.2          # VRC Message Cooldown

# Config for chat message sending
clear_timer = None              # Timer for clearing messages
last_send_time = 0              # Time of last message
send_lock = threading.Lock()    # Prevent multiple threads trying to send messages at once

curve_cache = None              # Caches the curve distribution
bezier_cache = None

state_lock = threading.Lock()   # State lock
curve_lock = threading.Lock()

# Render throttling
last_render = 0                 # Time of last render
RENDER_INTERVAL = 0.016          # Interval - 16ms/60fps

# UI
line_artist = None
marker_artist = None
ring_artist = None
vline_min = None
vline_max = None
legend = None

# OSC
zeroconf_instance = None

# ~~~      UNDO / REDO LOGIC      ~~~
# Apply a snapshot
def apply_snapshot(snapshot):
    global MIN_SHOCK_DURATION, MAX_SHOCK_DURATION, UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT, curve_cache
    
    UI_CONTROL_POINTS.clear()
    UI_CONTROL_POINTS.extend(snapshot["curve_points"])
    MIN_SHOCK_DURATION = snapshot["min_duration"]
    MAX_SHOCK_DURATION = snapshot["max_duration"]
    UI_VIEW_MIN_PERCENT = snapshot["ui_min_x"]
    UI_VIEW_MAX_PERCENT = snapshot["ui_max_x"]
    
    invalidate_curve_cache()

    # Update UI elements
    try:
        try:
            min_duration_scale.set(MIN_SHOCK_DURATION)
            max_duration_scale.set(MAX_SHOCK_DURATION)
            min_duration_var.set(f"Min Duration ({MIN_SHOCK_DURATION:.1f}s)")
            max_duration_var.set(f"Max Duration ({MAX_SHOCK_DURATION:.1f}s)")
            ui_min_scale.set(UI_VIEW_MIN_PERCENT)
            ui_max_scale.set(UI_VIEW_MAX_PERCENT)
            min_view_var.set(f"UI View Min ({int(UI_VIEW_MIN_PERCENT)}%)")
            max_view_var.set(f"UI View Max ({int(UI_VIEW_MAX_PERCENT)}%)")
        except NameError:
            # UI not built yet, ignore: values will sync once widgets exist
            pass
    except Exception:
        logging.exception(f"{RED}Unable to apply snapshot.")
        pass
    
def make_snapshot():
    return {
        "curve_points": UI_CONTROL_POINTS.copy(),
        "min_duration": MIN_SHOCK_DURATION,
        "max_duration": MAX_SHOCK_DURATION,
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    }
    
# Save current state to undo history
def save_undo_snapshot():
    # Prepare data
    snapshot = {
        "curve_points": UI_CONTROL_POINTS.copy(),
        "min_duration": MIN_SHOCK_DURATION,
        "max_duration": MAX_SHOCK_DURATION,
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    }

    # Push to undo stack
    undo_history.append(snapshot)

    # Limit history size
    if len(undo_history) > 50:
        undo_history.pop(0)

    # Clear redo history
    redo_history.clear()
    
def apply_history(source, dest):
    if not source:
        return
    
    dest.append(make_snapshot())
    apply_snapshot(source.pop())
    render_curve()
    save_config()

# Undo/Redo Event
def undo_action(event=None): apply_history(undo_history, redo_history)
def redo_action(event=None): apply_history(redo_history, undo_history)

def toggle_temporary_mode():
    global MIN_SHOCK_DURATION, MAX_SHOCK_DURATION, UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT, temporary_mode_disabled

    if not temporary_mode_disabled.get():
        load_config_from_file()
        apply_snapshot({
            "curve_points": UI_CONTROL_POINTS.copy(),
            "min_duration": MIN_SHOCK_DURATION,
            "max_duration": MAX_SHOCK_DURATION,
            "ui_min_x": UI_VIEW_MIN_PERCENT,
            "ui_max_x": UI_VIEW_MAX_PERCENT,
        })
        render_curve()

        logging.info(f"{RESET}Config reloaded on temporary mode disable")
    logging.info(f"{RESET}Temporary mode {YELLOW}{'enabled' if temporary_mode_disabled.get() else 'disabled'}")


# ~~~      LOAD / SAVE CONFIG      ~~~
# Attempt to load config, default if not found or error
def load_config_from_file():
    global CONFIG_FILE_PATH, UI_CONTROL_POINTS, MIN_SHOCK_DURATION, MAX_SHOCK_DURATION, UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT, PRESET_COUNT, default_preset_index, preset_names
    if os.path.exists(CONFIG_FILE_PATH):
        try:
            with open(CONFIG_FILE_PATH, "r") as f:
                data = json.load(f)
            loaded_pts = [(float(x), float(y)) for x, y in data.get("curve_points", UI_CONTROL_POINTS)]
            UI_CONTROL_POINTS.clear()
            UI_CONTROL_POINTS.extend(loaded_pts)
            MIN_SHOCK_DURATION = float(data.get("min_duration", MIN_SHOCK_DURATION))
            MAX_SHOCK_DURATION = float(data.get("max_duration", MAX_SHOCK_DURATION))
            UI_VIEW_MIN_PERCENT = int(data.get("ui_min_x", data.get("curve_min_x", UI_VIEW_MIN_PERCENT)))
            UI_VIEW_MAX_PERCENT = int(data.get("ui_max_x", data.get("curve_max_x", UI_VIEW_MAX_PERCENT)))

            # Load presets
            raw_presets = data.get("presets", [])
            if isinstance(raw_presets, list):
                raw_presets = (raw_presets + [None] * PRESET_COUNT)[:PRESET_COUNT]
            else:
                raw_presets = [None] * PRESET_COUNT
            # Ensure each preset has the expected keys
            for i in range(PRESET_COUNT):
                p = raw_presets[i]
                if isinstance(p, dict):
                    presets[i] = p
                else:
                    presets[i] = None

            # Load preset names if present
            raw_names = data.get("preset_names", None)
            if isinstance(raw_names, list) and len(raw_names) >= PRESET_COUNT:
                preset_names = raw_names[:PRESET_COUNT]

            default_idx = data.get("default_preset", None)
            if isinstance(default_idx, int) and 0 <= default_idx < PRESET_COUNT and presets[default_idx] is not None:
                default_preset_index = default_idx
                # Apply snapshot
                apply_snapshot(presets[default_preset_index])
        except Exception as e:
            logging.exception(f"{RED}Config load failed: {e}")

# Save new config to file
def save_config():
    # Do not save if disabled
    if temporary_mode_disabled.get():
        return

    # Prepare data
    data = {
        "curve_points": [(round(x, 2), round(y, 2)) for x, y in UI_CONTROL_POINTS],
        "min_duration": round(MIN_SHOCK_DURATION, 1),
        "max_duration": round(MAX_SHOCK_DURATION, 1),
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
        "presets": presets,
        "default_preset": default_preset_index,
        "preset_names": preset_names
    }

    # Write to file
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"{YELLOW}Failed to save config: {e}")


#~~~      PRESETS      ~~~
def save_preset(index):
    global presets
    if not (0 <= index < PRESET_COUNT):
        return
    presets[index] = make_snapshot()
    save_config()
    update_preset_buttons_appearance()
    logging.info(f"{RESET}Saved preset {index+1}")

def load_preset(index):
    if not (0 <= index < PRESET_COUNT):
        return
    p = presets[index]
    if not p:
        logging.info(f"{RESET}No preset saved at slot {index+1}")
        return
    save_undo_snapshot()
    apply_snapshot(p)
    render_curve()
    logging.info(f"{RESET}Loaded preset {index+1}")

def set_default_preset(index):
    global default_preset_index
    if not (0 <= index < PRESET_COUNT):
        return
    default_preset_index = index
    save_config()
    update_preset_buttons_appearance()
    logging.info(f"{RESET}Set preset {index+1} as default")

def update_preset_buttons_appearance():
    try:
        for i, btn in enumerate(preset_buttons):
            is_default = (i == default_preset_index)
            has_data = presets[i] is not None
            bg = PRESET_DEFAULT_BG if is_default else (PRESET_NORMAL_BG if has_data else "#3a3f46")
            fg = "white"
            btn.config(text=preset_names[i], bg=bg, fg=fg)
            save_btn = preset_save_buttons[i]
            save_btn.config(state=tk.NORMAL)
    except Exception:
        pass

# ~~~      OSC / SERIAL SETUP      ~~~
def osc_server():
    global zeroconf_instance
    
    dispatch = {}
    if (config.get('SHOCK_PARAMETER')):
        dispatch[SHOCK_PARAM] = handle_osc_packet
    if (config.get('SECOND_SHOCK_PARAMETER')):
        dispatch[SECOND_SHOCK_PARAM] = handle_osc_packet
    
    if not dispatch:
        logging.warning(f"{YELLOW}No OSC parameters setup, please set them up in the config file.")
        return
    
    used_params = {param.split("/")[-1] for param in dispatch.keys()}
    zeroconf_instance = start_osc("Shocker Link", dict_to_dispatcher(dispatch), params=used_params)
    if zeroconf_instance is None:
        logging.error(f"{RED}OSC server failed to start. VRChat integration disabled.")
        return
    logging.info(f"{RESET}Started OSC server for: {YELLOW}{list(dispatch.keys())}")

# Send chat message via OSC with cooldown and auto-clear
def send_chat_message(message_text, clear_after=True):
    global clear_timer, last_send_time

    with send_lock:
        now = time.perf_counter()
        # Always allow shock messages to bypass message cooldown
        bypass = "⚡" in message_text

        # If cooldown is active and bypass is false, skip sending
        if not bypass and now - last_send_time < MESSAGE_COOLDOWN:
            return
        
        last_send_time = now

        try:
            vrc_udp_client.send_message("/chatbox/input", (message_text, True, False))

            # Schedule a clear message
            if clear_after:

                if clear_timer is not None:
                    clear_timer.cancel()
                
                clear_timer = threading.Timer(4, send_chat_message, args=("", False))
                clear_timer.start()

        except Exception as e:
            logging.exception(f"{RED}OSC send failed: {e}")
            return
        logging.info(f"{RESET}Sent message: {message_text}")

def handle_osc_packet(address, *args):
    global last_trigger_time, trigger_timestamps, state_lock, shock_q
    if not args or args[0] != 1: # Only continue if an OSC packet is received
        return

    # Only accept valid shock parameter
    if (address == SHOCK_PARAM or address == SECOND_SHOCK_PARAM):
        
        now = time.time()
        with state_lock:
            trigger_timestamps[:] = [t for t in trigger_timestamps if now - t <= COOLDOWN_WINDOW_S]
            trigger_count = len(trigger_timestamps)
            dynamic_cooldown = min(BASE_COOLDOWN_S + COOLDOWN_FACTOR_S * trigger_count, MAX_COOLDOWN_S)

            # Check cooldown
            if COOLDOWN_ENABLED and now - last_trigger_time <= dynamic_cooldown:
                cooldown_msg = f"On cooldown: {round(last_trigger_time - now + dynamic_cooldown, 1)}s"
            else:
                cooldown_msg = None
                last_trigger_time = now
                trigger_timestamps.append(now)
        
        if cooldown_msg:
            send_chat_message(cooldown_msg)
            return

        # Determine shock intensity and duration
        intensities, weights = compute_curve_distribution()

        if address == SHOCK_PARAM:
            # For main shock param, use full curve
            intensity_percent = int(random.choices(intensities, weights=weights, k=1)[0])
        else:
            # For second shock param, use only the upper half of the curve
            sorted_indices = np.argsort(intensities)
            upper_half_indices = sorted_indices[len(sorted_indices)//2:]
            intensity_percent = int(random.choices(intensities[upper_half_indices], weights=weights[upper_half_indices], k=1)[0])

        duration_s = round(random.uniform(MIN_SHOCK_DURATION, MAX_SHOCK_DURATION), 1)

        # Send shock and chat message
        shock_q.put((intensity_percent, duration_s))
        send_chat_message(f"⚡ {intensity_percent}% | {duration_s}s")

def connect_serial():
    global serial_connection, pishock_api, shockers, PISHOCK_SHOCKER_IDS
    
    # If no port specified, scan automatically
    ports = []
    if SERIAL_PORT.strip():
        ports = [SERIAL_PORT]
    else:
        ports = [p for p in list_ports.comports()]

    if not USE_PISHOCK:
        if serial_connection is None or not getattr(serial_connection, "is_open", False):
            logging.info(f"{RESET}Available ports: {ports}")

            for attempt in range(3):
                for port in ports.device: # USB Path
                    try:
                        ser = serial.Serial(port, OPENSHOCK_SERIAL_BAUDRATE, timeout=1)
                        ser.write(b"domain\n")
                        resp = ser.read(50)
                        if b"openshock" in resp:
                            ser.flush()
                            logging.info(f"{RESET}Connected to serial port {CYAN}{port}")
                            serial_connection = ser
                            shockers = list(OPENSHOCK_SHOCKER_IDS)
                            return ser
                        else:
                            ser.close()
                    except SerialException as e:
                        logging.warning(f"{RED} Couldn't open {port}. It's probably in use by another program.")
                    except Exception as e:
                        logging.exception(f"{RED}Failed on {port}: {e}")
                    logging.warning(f"{YELLOW}Connection attempt {RESET}{attempt+1}/3 {YELLOW}for port {RESET}{port} {YELLOW}failed.")
                if attempt < 3:
                    logging.warning(f"{YELLOW}Retrying in 3 seconds...")
                    time.sleep(3)

            logging.error(f"{RED}Failed to open serial. Shocks disabled.")
            serial_connection = None
            return None
    else:
        if not SERIAL_PORT or SERIAL_PORT == "":
            found = False
            # Try to find the port manually first
            for port in ports:
                try:
                    ser = serial.Serial(port.device, OPENSHOCK_SERIAL_BAUDRATE, timeout=1)
                    
                    # Send info command to PiShock Hub
                    data = json.dumps({"cmd": "info"}) + "\n"
                    ser.write(data.encode("utf-8"))
                    count = 0
                    
                    while count < 40:
                        resp = ser.readline()
                        count += 1
                        # Read info response and wait for up to 40 lines to find it
                        if resp.startswith(b"TERMINALINFO: "):
                            if b"pishock" in resp:
                                ser.close()
                                try:
                                    pishock_api = SerialAPI(port.device)
                                    logging.info(f"{RESET}Connected to serial port {CYAN}{port.device}")
                                    found = True
                                    break
                                except Exception as e:
                                    logging.exception(f"{RED} Unknown error while searching for PiShock hub.")
                                    break
                        elif resp == b"":
                            break
                    if found:
                        break
                                
                except SerialException as e:
                    logging.warning(f"{RED} Couldn't open {port}. It's probably in use by another program.")
                    break
                except Exception as e:
                    logging.exception(f"{RED}Failed on {port}: {e}")
                    break
            # If we don't find a port, try finding automatically using pishock_api
            if not found:
                try:
                    pishock_api = SerialAPI(None)
                    logging.info(f"{RESET}Connected to PiShock Hub")
                except SerialAutodetectError as e:
                    logging.exception(f"{RED}Couldn't connect to the PiShock Device.\nTry disconnecting other serial devices or changing port.")
                    pishock_api = None
        # Port entered manually, skip all
        else:
            try:
                pishock_api = SerialAPI(SERIAL_PORT)
            except SerialAutodetectError as e:
                logging.exception(f"{RED}Couldn't connect to the PiShock Device.\nWrong port setup in config.")
                pishock_api = None
        
        if pishock_api:
            if not PISHOCK_SHOCKER_IDS:
                # Find pishock shocker
                info = pishock_api.info()
                shockers = info.get("shockers", [])
                first_shocker_id = shockers[0]["id"] if shockers else None
                if first_shocker_id is not None:
                    logging.info(f"{RESET}Found shocker with ID {first_shocker_id}")
                    shocker = pishock_api.shocker(first_shocker_id)
                    shockers.append(shocker)
                else:
                    logging.warning(f"{YELLOW}No shockers found.")
            else:
                for shocker_id in PISHOCK_SHOCKER_IDS:
                    shocker_instance = pishock_api.shocker(shocker_id)
                    shockers.append(shocker_instance)
                    logging.info(f"{RESET}Created shocker instance for ID {shocker_id}")

def serial_worker():
    global serial_connection
    while not serial_stop.is_set():
        try:
            cmd = serial_q.get(timeout=0.5)
        except Empty:
            continue
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if serial_connection is None or not getattr(serial_connection, "is_open", False):
                    connect_serial()
                if serial_connection and getattr(serial_connection, "is_open", True):
                    serial_connection.write(cmd)
                    serial_connection.flush()
                    break
            except Exception as e:
                logging.exception(f"{RED}Failed to write to serial (Attempt {RESET}{attempt+1}/{max_retries}){RED}: {e}")
                serial_connection = None
                time.sleep(0.5)
        else:
            print("Failed to send shock after retries.")

#~~~      SHOCKER LOGIC      ~~~
def shocker_worker():
    global shock_q, serial_connection, shockers, last_shocker_index
    while not shocker_stop.is_set():
        try:
            intensity_percent, duration_s = shock_q.get(timeout=0.3)
        except Empty:
            continue
        
        if not shockers:
            logging.warning(f"{YELLOW}No shockers configured, dropping shock.")
            continue
    
        if not RANDOM_OR_SEQUENTIAL:
            # Random
            chosen_shocker = random.choice(shockers)
            logging.info(f"{RESET}Selected shocker: {chosen_shocker}")
        else:
            # Sequential
            last_shocker_index = (last_shocker_index + 1) % len(shockers)
            chosen_shocker = shockers[last_shocker_index]
            logging.info(f"{RESET}Selected shocker: {chosen_shocker}")
            

        # Using OpenShock
        if not USE_PISHOCK:
            if serial_connection is None or not getattr(serial_connection, "is_open", False):
                logging.warning(f"{YELLOW}Serial not available. Cannot send shock. Attempting to reconnect...")
                connect_serial()
                if serial_connection and serial_connection.is_open:
                    shock_q.put((intensity_percent, duration_s)) # Re-queue shock
                else:
                    logging.error(f"{RED}Reconnect failed, dropping shock.")
                continue
            # Data for shock
            payload = {
                "model": "caixianlin",
                "id": chosen_shocker,
                "type": "shock",
                "intensity": int(intensity_percent),
                "durationMs": int(round(float(duration_s) * 1000))
            }
            cmd = "rftransmit " + json.dumps(payload)
            serial_q.put((cmd + "\n").encode('ascii'))
        else:
            # Using PiShock  
            chosen_shocker.shock(duration=round(float(duration_s), 1), intensity=int(intensity_percent))
        
        
# ~~~      BEZIER CURVE AND DISTRIBUTION LOGIC      ~~~
def bezier_interpolate(points, steps):
    p0, p1, p2 = np.array(points[0]), np.array(points[1]), np.array(points[2])
    t = np.linspace(0, 1, steps)[:, None]
    return (1-t)**2 * p0 + 2*(1-t)*t * p1 + t**2 * p2

def compute_curve_distribution():
    global curve_cache
    
    # Generate a smooth curve from control points
    with curve_lock:
        if curve_cache is not None:
            return curve_cache # Same points as last time, skip compute
        pts = sorted(UI_CONTROL_POINTS, key=lambda p: p[0])
    
    curve = bezier_interpolate(pts, steps=100)
    curve = curve[curve[:, 1] > 0]
    xs = np.clip(curve[:, 0].astype(int), 1, 100)
    ys = np.clip(curve[:, 1], 0, 1)
    if ys.sum() == 0:
        ys[:] = 1
        
    curve_cache = (xs, ys)
    return xs, ys


# ~~~      UI EVENT HANDLERS      ~~~
def on_min_duration_change(val):
    global MIN_SHOCK_DURATION
    MIN_SHOCK_DURATION = float(val)
    min_duration_var.set(f"Min Duration ({float(val):.1f}s)")

def on_max_duration_change(val):
    global MAX_SHOCK_DURATION
    MAX_SHOCK_DURATION = float(val)
    max_duration_var.set(f"Max Duration ({MAX_SHOCK_DURATION:.1f}s)")

def on_ui_view_min_change(val):
    global UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT
    v = int(float(val))
    if v >= UI_VIEW_MAX_PERCENT:
        v = max(1, UI_VIEW_MAX_PERCENT - 1)
        ui_min_scale.set(v)
    UI_VIEW_MIN_PERCENT = max(1, min(99, v))
    min_view_var.set(f"UI View Min ({int(UI_VIEW_MIN_PERCENT)}%)")
    throttled_render()

def on_ui_view_max_change(val):
    global UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT
    v = int(float(val))
    if v <= UI_VIEW_MIN_PERCENT:
        v = min(100, UI_VIEW_MIN_PERCENT + 1)
        ui_max_scale.set(v)
    UI_VIEW_MAX_PERCENT = min(100, max(2, v))
    max_view_var.set(f"UI View Max ({int(UI_VIEW_MAX_PERCENT)}%)")
    throttled_render()

def finish_text_input(event=None):
    global right_click_input_widget, highlight_index, curve_cache

    if not right_click_input_widget:
        return
    
    user_input = right_click_input_widget.get()
    right_click_input_widget.destroy()
    right_click_input_widget = None
    highlight_index = None

    if not user_input:
        render_curve()
        return
    
    # Expect format "x,y"
    try:
        x_str, y_str = user_input.split(",")
        x_val = float(x_str.strip())
        y_val = float(y_str.strip())
    except Exception:
        logging.warning(f"{YELLOW}Invalid input format")
        return
    
    # Find nearest point
    x_val = np.clip(x_val, 1, 100)
    y_val = np.clip(y_val, 0, 100)
    dists = [abs(p[0] - x_val) for p in UI_CONTROL_POINTS]
    nearest = int(np.argmin(dists))

    # Save snapshot before change
    save_undo_snapshot()

    # Update point and re-render
    UI_CONTROL_POINTS[nearest] = (x_val, y_val / 100)
    invalidate_curve_cache()
    save_config()
    render_curve()

# Mouse press handler
def on_mouse_press(event):
    global dragging_index, highlight_index, right_click_input_widget, drag_context

    # Ignore if not in axes
    if event.inaxes != ax:
        return

    # Right-click to edit point
    if event.button == 3:
        if right_click_input_widget is not None:
            right_click_input_widget.destroy()
            right_click_input_widget = None
            
        click_x = event.xdata
        click_y = event.ydata
        if click_x is None or click_y is None:
            return
        dists = [abs(point[0] - click_x) for point in UI_CONTROL_POINTS]
        nearest_index = int(np.argmin(dists))
        highlight_index = nearest_index
            
        canvas_widget = canvas.get_tk_widget()
        
        pointer_x = canvas_widget.winfo_pointerx()
        pointer_y = canvas_widget.winfo_pointery()
        
        local_x = pointer_x - canvas_widget.winfo_rootx()
        local_y = pointer_y - canvas_widget.winfo_rooty()
        
        right_click_input_widget = tk.Entry(canvas_widget, width=15)
        
        placeholder_text = "X,Y | 0-100"
        right_click_input_widget.insert(0, placeholder_text)
        right_click_input_widget.config(fg="gray")
        
        def on_key(event):
            if right_click_input_widget.get() == placeholder_text:
                right_click_input_widget.delete(0, tk.END)
                right_click_input_widget.config(fg="black")
                
        def on_finish(event):
            if not right_click_input_widget.get() == placeholder_text:
                finish_text_input()
            else:
                right_click_input_widget.destroy()
            

        right_click_input_widget.bind("<Key>", on_key)
        right_click_input_widget.place(x=local_x, y=local_y)
        right_click_input_widget.focus_set()
        right_click_input_widget.bind("<Return>", on_finish)
        right_click_input_widget.bind("<FocusOut>", on_finish)
        
        render_curve()
        return

    # Left-click to drag point
    if event.xdata is None:
        return

    # Find nearest point
    click_x = event.xdata
    dists = [abs(p[0] - click_x) for p in UI_CONTROL_POINTS]
    nearest = int(np.argmin(dists))
    if dists[nearest] < 5:
        dragging_index = nearest

        # Save snapshot before change
        save_undo_snapshot()

        # Logic for the middle point follow
        # Average the first and last points to stay relatively centered
        if len(UI_CONTROL_POINTS) == 3 and dragging_index in (0, 2):
            drag_context["dragged_endpoint"] = dragging_index
            drag_context["start_endpoint_pos"] = UI_CONTROL_POINTS[dragging_index]
            drag_context["start_middle_pos"] = UI_CONTROL_POINTS[1]
            p0 = np.array(UI_CONTROL_POINTS[0])
            p2 = np.array(UI_CONTROL_POINTS[2])
            pm = np.array(UI_CONTROL_POINTS[1])
            v = p2 - p0
            vlen = np.hypot(v[0], v[1])
            if vlen < 1e-6:
                drag_context["follow_mode"] = "translate"
            else:
                v_unit = v / vlen
                proj = float(np.dot(pm - p0, v_unit))
                t = proj / vlen
                perp_unit = np.array([-v_unit[1], v_unit[0]])
                perp_mag = float(np.dot(pm - (p0 + v_unit * proj), perp_unit))
                drag_context["follow_mode"] = "project"
                drag_context["t"] = t
                drag_context["perp_mag"] = perp_mag

# Mouse release handler
def on_mouse_release(event):
    global dragging_index, curve_cache
    
    dragging_index = None
    drag_context.clear()
    invalidate_curve_cache()
    save_config()

# Mouse motion handler
def on_mouse_motion(event):
    global dragging_index, curve_cache

    # Ignore if not dragging
    if dragging_index is None or event.inaxes != ax or event.xdata is None:
        return

    # Clamp to valid range
    new_x = np.clip(event.xdata, 1, 100)
    new_y = max(0, event.ydata)

    # Logic for middle point
    UI_CONTROL_POINTS[dragging_index] = (new_x, new_y)

    # If dragging an endpoint and we have stored start positions, move middle relative to them
    if dragging_index in (0, 2) and "start_endpoint_pos" in drag_context:
        x0, y0 = drag_context["start_endpoint_pos"]
        mx0, my0 = drag_context["start_middle_pos"]

        dx = new_x - x0
        dy = new_y - y0

        UI_CONTROL_POINTS[1] = (mx0 + dx, my0 + dy)
        invalidate_curve_cache() # Update cache after middle point updates

    # If we have a follow mode, adjust middle point accordingly
    # Average the first and last points to stay relatively centered
    if len(UI_CONTROL_POINTS) == 3 and dragging_index in (0, 2) and "follow_mode" in drag_context:
        p0 = np.array(UI_CONTROL_POINTS[0])
        p2 = np.array(UI_CONTROL_POINTS[2])
        v = p2 - p0
        vlen = np.hypot(v[0], v[1])

        if drag_context.get("follow_mode") == "translate" or vlen < 1e-6:
            start_ep = np.array(drag_context["start_endpoint_pos"])
            start_mid = np.array(drag_context["start_middle_pos"])
            dx = np.array([new_x, new_y]) - start_ep
            new_mid = start_mid + dx
        else:
            v_unit = v / vlen
            perp_unit = np.array([-v_unit[1], v_unit[0]])
            t = drag_context.get("t", 0.5)
            perp_mag = drag_context.get("perp_mag", 0.0)
            new_mid = p0 + v_unit * (t * vlen) + perp_unit * perp_mag

        UI_CONTROL_POINTS[1] = (float(new_mid[0]), float(new_mid[1]))
        
    # Mouse position label
    if event.inaxes != ax or event.xdata is None or event.ydata is None:
        mouse_pos_x.set("Intensity: -")
        mouse_pos_y.set("Weight:    -")
    else:
        mouse_pos_x.set(f"Intensity: {event.xdata:0.1f}")
        mouse_pos_y.set(f"Weight:    {event.ydata:0.2f}")
        
    throttled_render()

# Toggle cooldown logic
def toggle_cooldown_enabled():
    global COOLDOWN_ENABLED

    COOLDOWN_ENABLED = not COOLDOWN_ENABLED
    logging.info(f"{RESET}Cooldown {YELLOW}{'enabled' if COOLDOWN_ENABLED else 'disabled'}")

# --- Preset Logic ---
preset_rename_widget = None
preset_rename_index = None
def start_preset_rename(event, index):
    """Create an inline Entry at the mouse pointer to rename preset `index`."""
    global preset_rename_widget, preset_rename_index
    # Destroy existing if present
    if preset_rename_widget is not None:
        try:
            preset_rename_widget.destroy()
        except Exception:
            pass
        preset_rename_widget = None
        preset_rename_index = None

    # Parent the entry to the button's parent so coordinates match
    btn = preset_buttons[index] if index < len(preset_buttons) else None
    parent = btn.master if btn else preset_frame

    pointer_x = parent.winfo_pointerx()
    pointer_y = parent.winfo_pointery()
    local_x = pointer_x - parent.winfo_rootx()
    local_y = pointer_y - parent.winfo_rooty()

    preset_rename_widget = tk.Entry(parent, width=18)
    preset_rename_widget.insert(0, preset_names[index])
    preset_rename_widget.select_range(0, tk.END)
    preset_rename_widget.place(x=local_x, y=local_y)
    preset_rename_widget.focus_set()
    preset_rename_index = index

    # Handlers
    def _finish(event=None):
        finish_preset_rename()
    def _cancel(event=None):
        cancel_preset_rename()

    preset_rename_widget.bind("<Return>", _finish)
    preset_rename_widget.bind("<FocusOut>", _finish)
    preset_rename_widget.bind("<Escape>", _cancel)

def finish_preset_rename():
    global preset_rename_widget, preset_rename_index
    if preset_rename_widget is None or preset_rename_index is None:
        return
    new_name = preset_rename_widget.get().strip()
    try:
        preset_rename_widget.destroy()
    except Exception:
        pass
    preset_rename_widget = None
    idx = preset_rename_index
    preset_rename_index = None

    if not new_name:
        return
    preset_names[idx] = new_name
    try:
        preset_buttons[idx].config(text=new_name)
    except Exception:
        pass
    save_config()
    update_preset_buttons_appearance()

def cancel_preset_rename():
    global preset_rename_widget, preset_rename_index
    if preset_rename_widget is not None:
        try:
            preset_rename_widget.destroy()
        except Exception:
            pass
    preset_rename_widget = None
    preset_rename_index = None


# ~~~      UI CONSTRUCTION      ~~~
def build_gradient():
    # Gradient background
    def hex_to_rgb01(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

    left_rgb = np.array(hex_to_rgb01(GRADIENT_LEFT_COLOR))
    right_rgb = np.array(hex_to_rgb01(GRADIENT_RIGHT_COLOR))

    ncols = 512
    row = np.linspace(left_rgb, right_rgb, ncols)[None, :, :]
    return np.repeat(row, 40, axis=0)

gradient = build_gradient()

def init_plot():
    global line_artist, marker_artist, ring_artist, vline_min, vline_max, legend
    
    ax.imshow(gradient, extent=(0, 100, 0, 1), aspect='auto', origin='lower', zorder=0)
    
    # Create artists with dummy data, save references
    line_artist, = ax.plot([], [], linewidth = LINE_WIDTH, zorder = 4)
    marker_artist = ax.scatter([], [], zorder=6, s=TOUCH_MARKER_SIZE, edgecolors='k', marker="o", linewidth=0.6, facecolor=MARKER_COLOR)
    ring_artist = ax.scatter([], [], s=TOUCH_MARKER_SIZE * 1.8, facecolors="none", edgecolors='white', marker="o", linewidths=1.4, alpha=0.45, zorder=7)
    vline_min = ax.axvline(0, color='#5eead4', linestyle='--', linewidth=1, zorder=2)
    vline_max = ax.axvline(0, color='#fbbf24', linestyle='--', linewidth=1, zorder=2)

    # Static stuff
    fig.patch.set_facecolor(OUTSIDE_CURVE_BG)
    ax.set_facecolor(INSIDE_CURVE_BG)
    ax.set_axisbelow(True)
    ax.set_title("Intensity Probability Curve", fontsize=14, color=LABEL_COLOR, pad=8)
    ax.set_xlabel("Intensity (%)", fontsize=12, color=LABEL_COLOR)
    ax.set_ylabel("Weight", fontsize=12, color=LABEL_COLOR)
    ax.set_yticks(np.linspace(0, 1, 11))
    ax.set_yticklabels([f"{v:.1f}" for v in np.linspace(0, 1, 11)], color=LABEL_COLOR)
    ax.tick_params(axis='x', colors=LABEL_COLOR)
    ax.tick_params(axis='y', colors=LABEL_COLOR)
    ax.set_ylim(0, 1)
    ax.grid(which='major', linestyle='-', linewidth=0.9, alpha=0.6, zorder=3)
    
    sorted_pts = sorted(UI_CONTROL_POINTS, key=lambda p: p[0])
    min_x, min_y = sorted_pts[0]
    max_x, max_y = sorted_pts[-1]
    vline_min.set_label(f"Min {int(min_x)}% with {min_y*10:.1f} weight")
    vline_max.set_label(f"Max {int(max_x)}% with {max_y*10:.1f} weight")
    
    legend = ax.legend(loc='upper right', bbox_to_anchor=(1.0, 1.0), framealpha=0.9, fontsize=10)
    legend.get_frame().set_facecolor(OUTSIDE_CURVE_BG)
    legend.get_frame().set_edgecolor('#222')
    for text in legend.get_texts():
        text.set_color(LABEL_COLOR)

def render_curve():
    global bezier_cache
    
    sorted_pts = sorted(UI_CONTROL_POINTS, key=lambda p: p[0])
    
    if bezier_cache is None or bezier_cache[0] != sorted_pts:
        bezier_cache = (
            sorted_pts,
            bezier_interpolate(sorted_pts, steps = 100)
        )
    curve = bezier_cache[1]

    # Update curve line
    line_artist.set_data(curve[:, 0], curve[:, 1])

    # Update markers
    xs, ys = zip(*sorted_pts)
    marker_artist.set_offsets(np.column_stack([xs, ys]))

    # Update selection ring
    active = dragging_index if dragging_index is not None else highlight_index
    if active is not None:
        sx, sy = UI_CONTROL_POINTS[active]
        ring_artist.set_offsets([[sx, sy]])
        ring_artist.set_visible(True)
    else:
        ring_artist.set_visible(False)

    # Update vlines
    min_x, min_y = sorted_pts[0]
    max_x, max_y = sorted_pts[-1]
    vline_min.set_xdata([min_x, min_x])
    vline_max.set_xdata([max_x, max_x])
    vline_min.set_label(f"Min {int(min_x)}% with {min_y*10:.1f} weight")
    vline_max.set_label(f"Max {int(max_x)}% with {max_y*10:.1f} weight")

    # Rebuild when view range changes
    ax.set_xlim(UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT)
    all_fives = np.arange(0, 101, 5)
    major_xticks = all_fives[(all_fives >= UI_VIEW_MIN_PERCENT) & (all_fives <= UI_VIEW_MAX_PERCENT)]
    ax.set_xticks(major_xticks if major_xticks.size else [UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT])
    
    legend.texts[0].set_text(f"Min {int(min_x)}% with {min_y*10:.1f} weight")
    legend.texts[1].set_text(f"Max {int(max_x)}% with {max_y*10:.1f} weight")

    canvas.draw_idle() 

def throttled_render():
    global last_render
    now = time.perf_counter()

    if now - last_render >= RENDER_INTERVAL:
        last_render = now
        render_curve()
        
def invalidate_curve_cache():
    global curve_cache, bezier_cache
    curve_cache = None
    bezier_cache = None

# ~~~      TKINTER UI SETUP      ~~~
root = tk.Tk()
root.title("Shock Control GUI")

style = ttk.Style(root)

# Apply a dark theme if available
try:
    style.theme_use('clam')
except Exception:
    logging.exception(f"{RED}Unable to apply theme, UI might look wrong.")

# Configure styles
style.configure('.', font=('Segoe UI', 11), padding=6)
style.configure('TButton', padding=(0, 0), relief='flat', font=('Segoe UI', 9))
style.configure('TLabel', font=('Segoe UI', 11), background=BACKGROUND_COLOR, foreground='white')
style.configure('TCheckbutton', font=('Segoe UI', 11), background=BACKGROUND_COLOR, foreground='white')
style.configure('TFrame', background=BACKGROUND_COLOR)
style.configure('TScale', troughcolor='#222', background=BACKGROUND_COLOR)
root.configure(bg=BACKGROUND_COLOR)

# Main frame
frame_controls = ttk.Frame(root)
frame_controls.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

# MIN DURATION SLIDER
min_duration_var = tk.StringVar(value=f"Min Duration ({MIN_SHOCK_DURATION:.1f}s)")
ttk.Label(frame_controls, textvariable=min_duration_var).pack()
min_duration_scale = ttk.Scale(frame_controls, from_=0.1, to=5, orient=tk.HORIZONTAL, command=on_min_duration_change)
min_duration_scale.set(MIN_SHOCK_DURATION)
min_duration_scale.pack(fill=tk.X)
min_duration_scale.bind("<ButtonPress-1>", lambda e: save_undo_snapshot())
min_duration_scale.bind("<ButtonRelease-1>", lambda e: save_config())

# MAX DURATION SLIDER
max_duration_var = tk.StringVar(value=f"Max Duration ({MAX_SHOCK_DURATION:.1f}s)")
ttk.Label(frame_controls, textvariable=max_duration_var).pack()
max_duration_scale = ttk.Scale(frame_controls, from_=0.1, to=5, orient=tk.HORIZONTAL, command=on_max_duration_change)
max_duration_scale.set(MAX_SHOCK_DURATION)
max_duration_scale.pack(fill=tk.X)
max_duration_scale.bind("<ButtonPress-1>", lambda e: save_undo_snapshot())
max_duration_scale.bind("<ButtonRelease-1>", lambda e: save_config())

# PLOT FRAME
frame_plot = ttk.Frame(root)
frame_plot.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

# Matplotlib figure and canvas
fig, ax = plt.subplots(figsize=(5, 4))
canvas = FigureCanvasTkAgg(fig, master=frame_plot)
canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

init_plot()

minmax_frame = ttk.Frame(frame_plot)
minmax_frame.pack(fill=tk.X, pady=5)

# UI VIEW MIN SLIDER
min_view_var = tk.StringVar(value=f"UI View Min ({int(UI_VIEW_MIN_PERCENT)}%)")
ttk.Label(minmax_frame, text="UI View Min %", textvariable=min_view_var).pack(anchor='w')
ui_min_scale = ttk.Scale(minmax_frame, from_=1, to=99, orient=tk.HORIZONTAL, command=on_ui_view_min_change)
ui_min_scale.set(UI_VIEW_MIN_PERCENT)
ui_min_scale.pack(fill=tk.X)
ui_min_scale.bind("<ButtonPress-1>", lambda e: save_undo_snapshot())
ui_min_scale.bind("<ButtonRelease-1>", lambda e: save_config())

# UI VIEW MAX SLIDER
max_view_var = tk.StringVar(value=f"UI View Max ({int(UI_VIEW_MAX_PERCENT)}%)")
ttk.Label(minmax_frame, text="UI View Max %", textvariable=max_view_var).pack(anchor='w')
ui_max_scale = ttk.Scale(minmax_frame, from_=2, to=100, orient=tk.HORIZONTAL, command=on_ui_view_max_change)
ui_max_scale.set(UI_VIEW_MAX_PERCENT)
ui_max_scale.pack(fill=tk.X)
ui_max_scale.bind("<ButtonPress-1>", lambda e: save_undo_snapshot())
ui_max_scale.bind("<ButtonRelease-1>", lambda e: save_config())

label_temporary_mode = tk.Label(root, text="Temporary Mode", bg=BACKGROUND_COLOR, fg='white')
label_temporary_mode.place(relx=0.01, rely=0.93, anchor='sw')

# TEMPORARY TOGGLE
temporary_mode_disabled = tk.BooleanVar(value=False)

temporary_toggle = ttk.Checkbutton(root, text="", variable=temporary_mode_disabled, command=lambda: toggle_temporary_mode())
temporary_toggle.place(relx=0.01, rely=0.98, anchor='sw')

# COOLDOWN TOGGLE
cooldown_var = tk.BooleanVar(value=True)
cooldown_check = ttk.Checkbutton(frame_controls, text="Enable Cooldown", variable=cooldown_var, command=toggle_cooldown_enabled)

# Test shock button
buttons_frame = ttk.Frame(frame_controls)
buttons_frame.pack(fill=tk.X)

if config.get('SHOCK_PARAMETER'):
    test_shock = ttk.Button(buttons_frame, text="Test 1st Param", command=lambda: handle_osc_packet(SHOCK_PARAM, 1))
    test_shock.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

if config.get('SECOND_SHOCK_PARAMETER'):
    second_test_shock = ttk.Button(buttons_frame, text="Test 2nd Param", command=lambda: handle_osc_packet(SECOND_SHOCK_PARAM, 1))
    second_test_shock.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

# --- Presets UI ---
preset_frame = ttk.Frame(frame_controls)
preset_frame.pack(fill=tk.X, pady=(8, 4))

for i in range(PRESET_COUNT):
    btn = tk.Button(preset_frame, text=preset_names[i], width=10,
                    command=lambda i=i: load_preset(i))
    btn.grid(row=i, column=0, sticky='w', padx=(0,4), pady=2)

    # Right-click -> inline rename (Entry overlay). Middle-click -> set default.
    btn.bind("<Button-3>", lambda e, i=i: start_preset_rename(e, i))
    btn.bind("<Button-2>", lambda e, i=i: set_default_preset(i))

    preset_buttons.append(btn)

    sbtn = tk.Button(preset_frame, text="💾", width=3,
                     command=lambda i=i: save_preset(i))
    sbtn.grid(row=i, column=1, sticky='w', padx=(2,0))
    preset_save_buttons.append(sbtn)

# Connect mouse events
canvas.mpl_connect("button_press_event", on_mouse_press)
canvas.mpl_connect("button_release_event", on_mouse_release)
canvas.mpl_connect("motion_notify_event", on_mouse_motion)

# MOUSE POSITION LABELS
mouse_pos_x = tk.StringVar(value="Intensity: -")
mouse_pos_y = tk.StringVar(value="Weight:    -")

mouse_pos_x_label = tk.Label(root, textvariable=mouse_pos_x, font=("Courier New", 8), bg=BACKGROUND_COLOR, fg='white')
mouse_pos_y_label = tk.Label(root, textvariable=mouse_pos_y, font=("Courier New", 8), bg=BACKGROUND_COLOR, fg='white')

mouse_pos_x_label.place(relx=0.01, rely=0.84, anchor='sw')
mouse_pos_y_label.place(relx=0.01, rely=0.87, anchor='sw')

# Extra padding for children in the control frame
for child in frame_controls.winfo_children():
    try:
        child.pack_configure(padx=8, pady=6)
    except Exception:
        logging.exception(f"{RED}Unable to apply padding. UI sizing might be broken.")
        pass

# Bind Undo/Redo
root.bind_all('<Control-z>', undo_action)
root.bind_all('<Control-y>', redo_action)


# Shutdown logic
def shutdown():
    save_config()
    logging.info(f"{YELLOW}Stopping serial server")
    global serial_connection
    serial_stop.set()
    shocker_stop.set()
    serial_thread.join(timeout=1)
    shocker_thread.join(timeout=1)
    try:
        if serial_connection and getattr(serial_connection, "is_open", False):
            serial_connection.close()
            logging.info(f"{YELLOW}Closed serial port")
    except Exception as e:
        logging.exception(f"{RED}Error closing serial: {e}")
    if zeroconf_instance:
        logging.info(f"{YELLOW}Stopping OSC server")
        zeroconf_instance.unregister_all_services()
        zeroconf_instance.close()
    root.destroy()
    os._exit(0)

# Start OSC server thread
osc_server_thread = threading.Thread(target=osc_server, daemon=True)
serial_thread = threading.Thread(target=serial_worker, daemon=True)
shocker_thread = threading.Thread(target=shocker_worker, daemon=True)

root.protocol("WM_DELETE_WINDOW", shutdown)


# ~~~      STARTUP      ~~~
def start_services():
    global vrc_udp_client
    
    vrc_udp_client = vrc_client(VRCHAT_HOST)
    connect_serial()
    serial_thread.start()
    osc_server_thread.start()
    shocker_thread.start()

if __name__ == '__main__':
    load_config_from_file()
    update_preset_buttons_appearance()
    render_curve()
    root.after(0, lambda: (fig.tight_layout(pad=1.2), canvas.draw_idle()))
    
    # Make an initial undo snapshot of the startup state
    save_undo_snapshot()
    
    start_services()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        shutdown()

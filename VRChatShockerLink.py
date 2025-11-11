from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from zeroconf import ServiceInfo, Zeroconf
from serial.tools import list_ports
import matplotlib.pyplot as plt
from pishock import SerialAPI
from queue import Queue, Empty
from vrchat_oscquery.vrchat_oscquery.threaded import vrc_osc
from vrchat_oscquery.vrchat_oscquery.common import vrc_client, dict_to_dispatcher
from tkinter import ttk
import tkinter as tk
import numpy as np
import threading
import logging
import random
import socket
import serial
import time
import json
import yaml
import os

# Load config from YAML
config_path = "config.yml"
logging.basicConfig(
    level=logging.INFO,
    format= '%(message)s'
    )

try:
    config = yaml.safe_load(open(config_path)) or {}
except FileNotFoundError:
    logging.exception("Could not find config.yml file. Using default config")
    config = {}
except Exception as e:
    logging.exception("Could not load config.yml file. Using default config")
    config = {}
    
# --- NETWORK / Serial Config
USE_PISHOCK = config.get("USE_PISHOCK", False) # Use PiShock if True, else OpenShock
OPENSHOCK_SHOCKER_ID = config.get("OPENSHOCK_SHOCKER_ID", 41838) # ID for OpenShock shocker
VRCHAT_HOST = config.get("VRCHAT_HOST", "127.0.0.1")
OPENSHOCK_SERIAL_BAUDRATE = 115200
SERIAL_PORT = config.get("serial_port", "")
SHOCK_PARAM = f"/avatar/parameters/{config.get('SHOCK_PARAMETER', 'Shock')}" # OSC parameter to listen for shock trigger
SECOND_SHOCK_PARAM = f"/avatar/parameters/{config.get('SECOND_SHOCK_PARAMETER', 'SlapShock') or 'SlapShock'}" # Seccond parameter for stronger shocks, if empty use "SlapShock" to prevent false OSC triggers

client = vrc_client()

# Base config
BASE_COOLDOWN_S = 2
MAX_COOLDOWN_S = 6
COOLDOWN_FACTOR_S = 0.4
COOLDOWN_WINDOW_S = 30
COOLDOWN_ENABLED = True

MIN_SHOCK_DURATION = 0.4
MAX_SHOCK_DURATION = 1.7

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

# Pishock Vars
if USE_PISHOCK:
    pishock_api = SerialAPI(port = SERIAL_PORT or None) if USE_PISHOCK else None
    shocker = None

# ~~~      OSC / SERIAL SETUP      ~~~
def osc_server():
    servers = []
    if (config.get('SHOCK_PARAMETER')):
        server1 = vrc_osc("Schocker Link First Param", dict_to_dispatcher({f"{SHOCK_PARAM}": handle_osc_packet}))
        servers.append(("1st", server1))
    if (config.get('SECOND_SHOCK_PARAMETER')):
        server2 = vrc_osc("Schocker Link Second Param", dict_to_dispatcher({f"{SECOND_SHOCK_PARAM}": handle_osc_packet}))
        servers.append(("2nd", server2))
    
    for name, srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        logging.info(f"Started OSC server: {name}")

serial_connection = None
def connect_serial():
    global serial_connection, pishock_api, shocker

    if not USE_PISHOCK:
        if serial_connection is None or not getattr(serial_connection, "is_open", False):
            # If no port specified, scan automatically
            ports = []
            if SERIAL_PORT.strip():
                ports = [SERIAL_PORT]
            else:
                ports = [p.device for p in list_ports.comports()]

            logging.info(f"Available ports: {ports}")

            for attempt in range(3):
                for port in ports:
                    try:
                        ser = serial.Serial(port, OPENSHOCK_SERIAL_BAUDRATE, timeout=1)
                        ser.write(b"domain\n")
                        resp = ser.read(50)
                        if b"openshock" in resp:
                            ser.flush()
                            logging.info(f"Connected to serial port {port}")
                            serial_connection = ser
                            return ser
                        else:
                            ser.close()
                    except Exception as e:
                        logging.exception(f"Failed on {port}: {e}")
                    logging.warning(f"Reconnection attempt {attempt+1}/3 failed. Retrying in 3 seconds...")
                    time.sleep(3)

            logging.error("Failed to open serial. Shocks disabled.")
            serial_connection = None
            return None
    else:
        # Find pishock shocker
        info = pishock_api.info()
        shockers = info.get("shockers", [])
        first_shocker_id = shockers[0]["id"] if shockers else None
        if first_shocker_id is not None:
            logging.info(f"Found shocker with ID {first_shocker_id}")
            shocker = pishock_api.shocker(first_shocker_id)
        else:
            logging.warning("No shockers found.")

serial_q = Queue()
serial_stop = threading.Event()
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
                try:
                    serial_connection.write(cmd)
                    serial_connection.flush()
                except Exception as e:
                    logging.exception(f"Failed to write to serial (Attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(0.5)
        else:
            print("Failed to send shock after retries.")

# ~~~      LOAD / SAVE CONFIG      ~~~
# Attempt to load config, default if not found or error
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
    except Exception as e:
        logging.exception(f"Config load failed: {e}")

# Save new config to file
def save_config():
    # Do not save if disabled
    if not save_enabled_var.get():
        return
    
    # Prepare data
    data = {
        "curve_points": [(round(x, 2), round(y, 2)) for x, y in UI_CONTROL_POINTS],
        "min_duration": round(MIN_SHOCK_DURATION, 1),
        "max_duration": round(MAX_SHOCK_DURATION, 1),
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    }

    # Write to file
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logging.exception(f"Failed to save config: {e}")

# ~~~      UNDO / REDO LOGIC      ~~~
# Save current state to undo history
def load_undo_snapshot():
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

# Apply a snapshot
def apply_snapshot(snapshot):
    global MIN_SHOCK_DURATION, MAX_SHOCK_DURATION, UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT
    
    UI_CONTROL_POINTS.clear()
    UI_CONTROL_POINTS.extend(snapshot["curve_points"])
    MIN_SHOCK_DURATION = snapshot["min_duration"]
    MAX_SHOCK_DURATION = snapshot["max_duration"]
    UI_VIEW_MIN_PERCENT = snapshot["ui_min_x"]
    UI_VIEW_MAX_PERCENT = snapshot["ui_max_x"]

    # Update UI elements
    try:
        min_duration_scale.set(MIN_SHOCK_DURATION)
        max_duration_scale.set(MAX_SHOCK_DURATION)
        min_duration_var.set(f"Min Duration ({MIN_SHOCK_DURATION:.1f}s)")
        max_duration_var.set(f"Max Duration ({MAX_SHOCK_DURATION:.1f}s)")
        ui_min_scale.set(UI_VIEW_MIN_PERCENT)
        ui_max_scale.set(UI_VIEW_MAX_PERCENT)
        min_view_var.set(f"UI View Min ({int(UI_VIEW_MIN_PERCENT)}%)")
        max_view_var.set(f"UI View Max ({int(UI_VIEW_MAX_PERCENT)}%)")
    except Exception:
        logging.exception("Unable to apply snapshot.")
        pass


# Undo action
def undo_action(event=None):
    # If there is nothing to undo, return
    if not undo_history:
        return
    
    # Push current snapshot to redo history
    redo_history.append({
        "curve_points": UI_CONTROL_POINTS.copy(),
        "min_duration": MIN_SHOCK_DURATION,
        "max_duration": MAX_SHOCK_DURATION,
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    })

    # Remove last undo snapshot and apply it
    snapshot = undo_history.pop()
    apply_snapshot(snapshot)
    render_curve()
    save_config()

# Redo action
def redo_action(event=None):
    if not redo_history:
        return
    
    # Push current snapshot to undo history
    undo_history.append({
        "curve_points": UI_CONTROL_POINTS.copy(),
        "min_duration": MIN_SHOCK_DURATION,
        "max_duration": MAX_SHOCK_DURATION,
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    })

    # Remove last redo snapshot and apply it
    snap = redo_history.pop()
    apply_snapshot(snap)
    render_curve()
    save_config()


# ~~~      MESSAGE SENDING LOGIC      ~~~
# Config for chat message sending
clear_timer = None
last_send_time = 0
send_lock = threading.Lock()
MESSAGE_COOLDOWN = 1.2

# Send chat message via OSC with cooldown and auto-clear
def send_chat_message(message_text, clear_after=True):
    global clear_timer, last_send_time

    with send_lock:
        now = time.time()
        # Always allow shock messages to bypass message cooldown
        bypass = "⚡" in message_text

        # If cooldown is active and bypass is false, skip sending
        if not bypass and now - last_send_time < MESSAGE_COOLDOWN:
            return
        
        # Update last send time
        last_send_time = now

        try:
            client.send_message("/chatbox/input", (message_text, True, False))

            # Schedule a clear if a message is sent
            if clear_after:

                # If a new message is sent, cancel any existing clear timer
                if clear_timer is not None:
                    clear_timer.cancel()
                
                # Schedule a new clear timer
                clear_timer = threading.Timer(4, send_chat_message, args=("", False))
                clear_timer.start()

        except Exception as e:
            logging.exception(f"OSC send failed: {e}")
            return
        logging.info(f"Sent message: {message_text}")

# Send shock command via serial
def send_shock(duration_s, intensity_percent):
    global serial_connection, shocker

    # Using OpenShock
    if not USE_PISHOCK:
        if serial_connection is None or not getattr(serial_connection, "is_open", False):
            logging.warning("Serial not available. Cannot send shock. Attempting to reconnect...")
            connect_serial()
            return
        # Data for shock
        payload = {
            "model": "caixianlin",
            "id": OPENSHOCK_SHOCKER_ID,
            "type": "shock",
            "intensity": int(intensity_percent),
            "durationMs": int(round(float(duration_s) * 1000))
        }
        cmd = "rftransmit " + json.dumps(payload)
        serial_q.put((cmd + "\n").encode('ascii'))
        return
    else:
        # Using PiShock
        shocker.shock(duration=round(float(duration_s), 1), intensity=int(intensity_percent))


# ~~~      OSC MESSAGE HANDLER      ~~~
# Handle incoming OSC packets
state_lock = threading.Lock()
def handle_osc_packet(address, *args):
    global last_trigger_time, trigger_timestamps, state_lock

    # Only accept valid shock parameter
    if (address == SHOCK_PARAM or address == SECOND_SHOCK_PARAM) and args:
        

        # If parameter equals 1, continue
        param_value = args[0]
        if param_value == 1:
            now = time.time()
            with state_lock:
                trigger_timestamps[:] = [t for t in trigger_timestamps if now - t <= COOLDOWN_WINDOW_S]
                trigger_count = len(trigger_timestamps)
                dynamic_cooldown = min(BASE_COOLDOWN_S + COOLDOWN_FACTOR_S * trigger_count, MAX_COOLDOWN_S)

                # Check cooldown
                if COOLDOWN_ENABLED and now - last_trigger_time <= dynamic_cooldown:
                    send_chat_message(f"On cooldown: {round(last_trigger_time - now + dynamic_cooldown, 1)}s")
                    should_proceed = False
                    return
                else:
                    last_trigger_time = now
                    trigger_timestamps.append(now)
                    should_proceed = True
                
            if not should_proceed:
                return

            # Determine shock intensity and duration
            intensities, weights = compute_curve_distribution()

            if address == SHOCK_PARAM:
                # For main shock param, use full curve
                intensity = int(random.choices(intensities, weights=weights, k=1)[0])
            else:
                # For second shock param, use only the upper half of the curve
                sorted_indices = np.argsort(intensities)
                upper_half_indices = sorted_indices[len(sorted_indices)//2:]
                intensity = int(random.choices(intensities[upper_half_indices], weights=weights[upper_half_indices], k=1)[0])

            duration = round(random.uniform(MIN_SHOCK_DURATION, MAX_SHOCK_DURATION), 1)

            # Send shock and chat message
            send_shock(duration_s=duration, intensity_percent=intensity)
            send_chat_message(f"⚡ {intensity}% | {duration}s")
            
# ~~~      Bezier Curve and Distribution Logic      ~~~
# Bezier curve interpolation for rendering curve
def bezier_interpolate(points, steps=100):
    # Prepare points and curve
    p0, p1, p2 = points
    curve = []
    # Calculate curve points
    t_vals = np.linspace(0, 1, steps)
    # Quadratic Bezier formula
    for t in t_vals:
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        curve.append((x, y))

    return np.array(curve)

# Compute intensity distribution from curve
def compute_curve_distribution():
    # Generate a smooth curve from control points
    # Honestly this is math I barely understand, but it works
    curve = bezier_interpolate(sorted(UI_CONTROL_POINTS, key=lambda p: p[0]), steps=100)
    curve = curve[curve[:, 1] > 0]
    xs = np.clip(curve[:, 0].astype(int), 1, 100)
    ys = np.clip(curve[:, 1], 0, 1)
    if ys.sum() == 0:
        ys[:] = 1
    return xs, ys


# ~~~      UI EVENT HANDLERS      ~~~
# Min duration change
def on_min_duration_change(val):
    global MIN_SHOCK_DURATION
    MIN_SHOCK_DURATION = float(val)
    min_duration_var.set(f"Min Duration ({float(val):.1f}s)")

# Max duration change
def on_max_duration_change(val):
    global MAX_SHOCK_DURATION
    MAX_SHOCK_DURATION = float(val)
    max_duration_var.set(f"Max Duration ({MAX_SHOCK_DURATION:.1f}s)")

# UI view min change
def on_ui_view_min_change(val):
    global UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT
    v = int(float(val))
    if v >= UI_VIEW_MAX_PERCENT:
        v = max(1, UI_VIEW_MAX_PERCENT - 1)
        ui_min_scale.set(v)
    UI_VIEW_MIN_PERCENT = max(1, min(99, v))
    min_view_var.set(f"UI View Min ({int(UI_VIEW_MIN_PERCENT)}%)")
    render_curve()

# UI view max change
def on_ui_view_max_change(val):
    global UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT
    v = int(float(val))
    if v <= UI_VIEW_MIN_PERCENT:
        v = min(100, UI_VIEW_MIN_PERCENT + 1)
        ui_max_scale.set(v)
    UI_VIEW_MAX_PERCENT = min(100, max(2, v))
    max_view_var.set(f"UI View Max ({int(UI_VIEW_MAX_PERCENT)}%)")
    render_curve()

# Finish text edit from right-click entry
def finish_text_edit(event=None):
    global right_click_input_widget, highlight_index

    # If no widget, return
    if not right_click_input_widget:
        return
    
    # Parse input
    user_input = right_click_input_widget.get()
    right_click_input_widget.destroy()
    right_click_input_widget = None
    highlight_index = None

    # Validate input
    if not user_input:
        render_curve()
        return
    
    # Expect format "x,y"
    try:
        x_str, y_str = user_input.split(",")
        x_val = float(x_str.strip())
        y_val = float(y_str.strip())
    except Exception:
        logging.warning("Invalid input format")
        return
    
    # Find nearest point
    x_val = np.clip(x_val, 1, 100)
    y_val = np.clip(y_val, 0, 100)
    dists = [abs(p[0] - x_val) for p in UI_CONTROL_POINTS]
    nearest = int(np.argmin(dists))

    # Save snapshot before change
    load_undo_snapshot()

    # Update point and re-render
    UI_CONTROL_POINTS[nearest] = (x_val, y_val / 100)
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
                finish_text_edit()
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
        load_undo_snapshot()

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
    global dragging_index
    
    dragging_index = None
    drag_context.clear()
    save_config()

# Mouse motion handler
render_job = None
def on_mouse_motion(event):
    global dragging_index, render_job

    # Ignore if not dragging
    if dragging_index is None or event.inaxes != ax or event.xdata is None:
        return

    # Clamp to valid range
    new_x = np.clip(event.xdata, 1, 100)
    new_y = max(0, event.ydata)

    # Logic for middle point
    UI_CONTROL_POINTS[dragging_index] = (new_x, new_y)

    # If dragging an endpoint and we have stored start positions, move middle relative to them
    if len(UI_CONTROL_POINTS) == 3 and dragging_index in (0, 2) and "start_endpoint_pos" in drag_context:
        x0, y0 = drag_context["start_endpoint_pos"]
        mx0, my0 = drag_context["start_middle_pos"]

        dx = new_x - x0
        dy = new_y - y0

        UI_CONTROL_POINTS[1] = (mx0 + dx, my0 + dy)

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
        
    if render_job is not None:
        root.after_cancel(render_job)
    render_job = root.after(2, render_curve)

# Render the curve and UI
def render_curve():
    ax.clear()
    sorted_pts = sorted(UI_CONTROL_POINTS, key=lambda p: p[0])
    curve = bezier_interpolate(sorted_pts)

    # Background style
    fig.patch.set_facecolor(OUTSIDE_CURVE_BG)
    ax.set_facecolor(INSIDE_CURVE_BG)    

    # Draw curve
    ax.plot(curve[:, 0], curve[:, 1], color=CURVE_LINE_COLOR, linewidth=LINE_WIDTH)

    # Marker points
    xs, ys = zip(*sorted_pts)
    ax.scatter(xs, ys, zorder=5, color=MARKER_COLOR, s=TOUCH_MARKER_SIZE, edgecolors='k', marker="o", linewidth=0.6)

    # Draw ring around selected point
    try:
        if dragging_index is not None and 0 <= dragging_index < len(sorted_pts):
            sx, sy = UI_CONTROL_POINTS[dragging_index]
            ax.scatter([sx], [sy], s=TOUCH_MARKER_SIZE * 1.8, facecolors="none",
                       edgecolors='white', marker="o", linewidths=1.4, alpha=0.45, zorder=4)
        if highlight_index is not None:
            sx, sy = UI_CONTROL_POINTS[highlight_index]
            ax.scatter([sx], [sy], s=TOUCH_MARKER_SIZE * 1.8, facecolors="none",
                       edgecolors='white', marker="o", linewidths=1.4, alpha=0.45, zorder=4)
    except Exception:
        logging.exception("Unable to draw ring around selected point. (You can ignore this error)")
        pass

    # Draw min/max lines
    min_x, min_y = sorted_pts[0]
    max_x, max_y = sorted_pts[-1]
    ax.axvline(min_x, color='#5eead4', linestyle='--', label=f"Min {int(min_x)}% with {min_y *10:.1f} weight", linewidth=1)
    ax.axvline(max_x, color='#fbbf24', linestyle='--', label=f"Max {int(max_x)}% with {max_y * 10:.1f} weight", linewidth=1)

    # Labels
    ax.set_title("Intensity Probability Curve", fontsize=14, color=LABEL_COLOR, pad=8)
    ax.set_xlabel("Intensity (%)", fontsize=12, color=LABEL_COLOR)
    ax.set_ylabel("Weight", fontsize=12, color=LABEL_COLOR)

    # Axis styling
    ax.set_yticks(np.linspace(0, 1, 11))
    ax.set_yticklabels([f"{v:.1f}" for v in np.linspace(0, 1, 11)], color=LABEL_COLOR)

    ax.tick_params(axis='x', colors=LABEL_COLOR)
    ax.tick_params(axis='y', colors=LABEL_COLOR)

    legend = ax.legend(loc='upper right', bbox_to_anchor=(1.02, 1.0), framealpha=0.9, fontsize=10)
    if legend:
        legend.get_frame().set_facecolor(OUTSIDE_CURVE_BG)
        legend.get_frame().set_edgecolor('#222')
        for text in legend.get_texts():
            text.set_color(LABEL_COLOR)

    ax.set_xlim(UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT)
    ax.set_ylim(0, 1)

    fig.tight_layout(pad=1.2)
    canvas.draw_idle()

# Slider release handler
def on_slider_release(event):
    save_config()

# UI mouse position update
def update_mouse_position_label(event):
    if event.inaxes != ax or event.xdata is None or event.ydata is None:
        mouse_pos_x.set("Intensity: -")
        mouse_pos_y.set("Weight:    -")
    else:
        mouse_pos_x.set(f"Intensity: {event.xdata:0.1f}")
        mouse_pos_y.set(f"Weight:    {event.ydata:0.2f}")

# Toggle cooldown logic
def toggle_cooldown_enabled():
    global COOLDOWN_ENABLED

    COOLDOWN_ENABLED = not COOLDOWN_ENABLED
    logging.info(f"Cooldown {'enabled' if COOLDOWN_ENABLED else 'disabled'}")

# ~~~      TKINTER UI SETUP      ~~~
root = tk.Tk()
root.title("Shock Control GUI")

style = ttk.Style(root)

# Apply a dark theme if available
try:
    style.theme_use('clam')
except Exception:
    logging.exception("Unable to apply theme")

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
min_duration_scale.bind("<ButtonPress-1>", lambda e: load_undo_snapshot())
min_duration_scale.bind("<ButtonRelease-1>", lambda e: save_config())

# MAX DURATION SLIDER
max_duration_var = tk.StringVar(value=f"Max Duration ({MAX_SHOCK_DURATION:.1f}s)")
ttk.Label(frame_controls, textvariable=max_duration_var).pack()
max_duration_scale = ttk.Scale(frame_controls, from_=0.1, to=5, orient=tk.HORIZONTAL, command=on_max_duration_change)
max_duration_scale.set(MAX_SHOCK_DURATION)
max_duration_scale.pack(fill=tk.X)
max_duration_scale.bind("<ButtonPress-1>", lambda e: load_undo_snapshot())
max_duration_scale.bind("<ButtonRelease-1>", lambda e: save_config())

# PLOT FRAME
frame_plot = ttk.Frame(root)
frame_plot.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

# Matplotlib figure and canvas
fig, ax = plt.subplots(figsize=(5, 4))
canvas = FigureCanvasTkAgg(fig, master=frame_plot)
canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

minmax_frame = ttk.Frame(frame_plot)
minmax_frame.pack(fill=tk.X, pady=5)

# UI VIEW MIN SLIDER
min_view_var = tk.StringVar(value=f"UI View Min ({int(UI_VIEW_MIN_PERCENT)}%)")
ttk.Label(minmax_frame, text="UI View Min %", textvariable=min_view_var).pack(anchor='w')
ui_min_scale = ttk.Scale(minmax_frame, from_=1, to=99, orient=tk.HORIZONTAL, command=on_ui_view_min_change)
ui_min_scale.set(UI_VIEW_MIN_PERCENT)
ui_min_scale.pack(fill=tk.X)
ui_min_scale.bind("<ButtonPress-1>", lambda e: load_undo_snapshot())
ui_min_scale.bind("<ButtonRelease-1>", lambda e: save_config())

# UI VIEW MAX SLIDER
max_view_var = tk.StringVar(value=f"UI View Max ({int(UI_VIEW_MAX_PERCENT)}%)")
ttk.Label(minmax_frame, text="UI View Max %", textvariable=max_view_var).pack(anchor='w')
ui_max_scale = ttk.Scale(minmax_frame, from_=2, to=100, orient=tk.HORIZONTAL, command=on_ui_view_max_change)
ui_max_scale.set(UI_VIEW_MAX_PERCENT)
ui_max_scale.pack(fill=tk.X)
ui_max_scale.bind("<ButtonPress-1>", lambda e: load_undo_snapshot())
ui_max_scale.bind("<ButtonRelease-1>", lambda e: save_config())

label_save_toggle = tk.Label(root, text="Enable Saving", bg=BACKGROUND_COLOR, fg='white')
label_save_toggle.place(relx=0.01, rely=0.93, anchor='sw')

# SAVE TOGGLE
save_enabled_var = tk.BooleanVar(value=True)

save_toggle = ttk.Checkbutton(root, text="", variable=save_enabled_var, command=lambda: toggle_saving())
save_toggle.place(relx=0.01, rely=0.98, anchor='sw')

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


# Connect mouse events
canvas.mpl_connect("button_press_event", on_mouse_press)
canvas.mpl_connect("button_release_event", on_mouse_release)
canvas.mpl_connect("motion_notify_event", on_mouse_motion)
canvas.mpl_connect("motion_notify_event", update_mouse_position_label)

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
        logging.exception("Unable to apply padding. UI sizing might be broken.")
        pass

# Bind Undo/Redo
root.bind_all('<Control-z>', undo_action)
root.bind_all('<Control-y>', redo_action)

render_curve()

# Make an initial undo snapshot of the startup state
load_undo_snapshot()

# Toggle saving config
def toggle_saving():
    global UI_CONTROL_POINTS, MIN_SHOCK_DURATION, MAX_SHOCK_DURATION, UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT

    # If enabling, reload config from file to return to last saved state
    if save_enabled_var.get():
        if os.path.exists(CONFIG_FILE_PATH):
            try:
                with open(CONFIG_FILE_PATH, "r") as f:
                    data = json.load(f)
                new_pts = [(float(x), float(y)) for x, y in data.get("curve_points", [])]
                UI_CONTROL_POINTS.clear()
                UI_CONTROL_POINTS.extend(new_pts)
                MIN_SHOCK_DURATION = float(data.get("min_duration", MIN_SHOCK_DURATION))
                MAX_SHOCK_DURATION = float(data.get("max_duration", MAX_SHOCK_DURATION))
                UI_VIEW_MIN_PERCENT = int(data.get("ui_min_x", data.get("curve_min_x", UI_VIEW_MIN_PERCENT)))
                UI_VIEW_MAX_PERCENT = int(data.get("ui_max_x", data.get("curve_max_x", UI_VIEW_MAX_PERCENT)))
                render_curve()
                min_duration_scale.set(MIN_SHOCK_DURATION)
                max_duration_scale.set(MAX_SHOCK_DURATION)
                min_duration_var.set(f"Min Duration ({MIN_SHOCK_DURATION:.1f}s)")
                max_duration_var.set(f"Max Duration ({MAX_SHOCK_DURATION:.1f}s)")
                ui_min_scale.set(UI_VIEW_MIN_PERCENT)
                ui_max_scale.set(UI_VIEW_MAX_PERCENT)
                logging.info("Config reloaded on save enable")
            except Exception as e:
                logging.exception(f"Failed to reload config: {e}")
    logging.info(f"Saving {'enabled' if save_enabled_var.get() else 'disabled'}")

# Shutdown logic
def shutdown():
    save_config()
    logging.info("Stopping server")
    osc_server_thread.join(timeout=1)
    global serial_connection
    try:
        serial_stop.set()
        serial_thread.join(timeout=1)
        if serial_connection and getattr(serial_connection, "is_open", False):
            serial_connection.close()
            logging.info("Closed serial port")
    except Exception as e:
        logging.exception(f"Error closing serial: {e}")
    root.destroy()

# Start OSC server thread
osc_server_thread = threading.Thread(target=osc_server, daemon=True)
serial_thread = threading.Thread(target=serial_worker, daemon=True)

root.protocol("WM_DELETE_WINDOW", shutdown)

def start_services():
    connect_serial()
    serial_thread.start()
    osc_server_thread.start()

start_services()
# Start Tkinter main loop
try:
    root.mainloop()
except KeyboardInterrupt:
    shutdown()

import random
import time
import tkinter as tk
from tkinter import ttk
# replaced pishock with pyserial
import serial
from pythonosc import dispatcher as osc_dispatcher, osc_server, udp_client
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
import numpy as np
import threading
import json
import os

# --- NETWORK / Serial Config
VRCHAT_HOST = "127.0.0.1"
OSC_LISTEN_PORT = 9203
OSC_SEND_PORT = 9102
SERIAL_PORT_NAME = "COM4"
SERIAL_BAUDRATE = 115200
serial_conn = None

UI_CONTROL_POINTS = [(1, 1.0), (50, 1.0), (100, 1.0)]

osc_sender = udp_client.SimpleUDPClient(VRCHAT_HOST, OSC_SEND_PORT)

try:
    serial_conn = serial.Serial(SERIAL_PORT_NAME, SERIAL_BAUDRATE, timeout=1)
except Exception as e:
    print(f"Failed to open serial: {e}. Shocks will be disabled until serial is available.")

SHOCK_PARAM = "/avatar/parameters/Shock"


# Base settings
BASE_COOLDOWN_S = 2
MAX_COOLDOWN_S = 6
COOLDOWN_FACTOR_S = 0.4
COOLDOWN_WINDOW_S = 30
COOLDOWN_ENABLED = True

MIN_SHOCK_DURATION = 0.4
MAX_SHOCK_DURATION = 1.7

UI_VIEW_MIN_PERCENT = 1
UI_VIEW_MAX_PERCENT = 100


# Timestamps for trigger cooldown
trigger_timestamps = []
last_trigger_time = 0

# Undo/Redo history
undo_history = []
redo_history = []

CONFIG_FILE_PATH = "curve_config.json"

# Drag/Edit state
dragging_index = None
right_click_input_widget = None

# Style settings
TOUCH_SELECT_THRESHOLD = 8
TOUCH_MARKER_SIZE = 140
LINE_WIDTH = 3
OUTSIDE_CURVE_BG = "#2A313D"
INSIDE_CURVE_BG = "#2C3749"
BACKGROUND_COLOR = "#202630"
CURVE_LINE_COLOR = "#00c2ff"
MARKER_COLOR = "#D88A91"
LABEL_COLOR = "#e6eef6"

drag_context = {}

if os.path.exists(CONFIG_FILE_PATH):
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            data = json.load(f)
        loaded_pts = [(float(x), float(y)) for x, y in data.get("curve_points", [])]
        UI_CONTROL_POINTS.clear()
        UI_CONTROL_POINTS.extend(loaded_pts)
        MIN_SHOCK_DURATION = float(data.get("min_duration", MIN_SHOCK_DURATION))
        MAX_SHOCK_DURATION = float(data.get("max_duration", MAX_SHOCK_DURATION))
        UI_VIEW_MIN_PERCENT = int(data.get("ui_min_x", data.get("curve_min_x", UI_VIEW_MIN_PERCENT)))
        UI_VIEW_MAX_PERCENT = int(data.get("ui_max_x", data.get("curve_max_x", UI_VIEW_MAX_PERCENT)))
    except Exception as e:
        print("Config load failed:", e)

if not UI_CONTROL_POINTS:
    UI_CONTROL_POINTS = [(1, 1.0), (50, 1.0), (100, 1.0)]


def persist_config():
    if not save_enabled_var.get():
        return
    data = {
        "curve_points": [(round(x, 2), round(y, 2)) for x, y in UI_CONTROL_POINTS],
        "min_duration": round(MIN_SHOCK_DURATION, 1),
        "max_duration": round(MAX_SHOCK_DURATION, 1),
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    }
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print("Failed to save config:", e)


def load_undo_snapshot():
    snapshot = {
        "curve_points": UI_CONTROL_POINTS.copy(),
        "min_duration": MIN_SHOCK_DURATION,
        "max_duration": MAX_SHOCK_DURATION,
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    }
    undo_history.append(snapshot)
    if len(undo_history) > 50:
        undo_history.pop(0)
    redo_history.clear()


def apply_snapshot(snapshot):
    global MIN_SHOCK_DURATION, MAX_SHOCK_DURATION, UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT
    UI_CONTROL_POINTS.clear()
    UI_CONTROL_POINTS.extend(snapshot["curve_points"])  # in-place

    MIN_SHOCK_DURATION = snapshot["min_duration"]
    MAX_SHOCK_DURATION = snapshot["max_duration"]
    UI_VIEW_MIN_PERCENT = snapshot["ui_min_x"]
    UI_VIEW_MAX_PERCENT = snapshot["ui_max_x"]

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
        pass


def undo_action(event=None):
    if not undo_history:
        return
    # push current full state to redo
    redo_history.append({
        "curve_points": UI_CONTROL_POINTS.copy(),
        "min_duration": MIN_SHOCK_DURATION,
        "max_duration": MAX_SHOCK_DURATION,
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    })
    snap = undo_history.pop()
    apply_snapshot(snap)
    render_curve()
    persist_config()


def redo_action(event=None):
    if not redo_history:
        return
    undo_history.append({
        "curve_points": UI_CONTROL_POINTS.copy(),
        "min_duration": MIN_SHOCK_DURATION,
        "max_duration": MAX_SHOCK_DURATION,
        "ui_min_x": UI_VIEW_MIN_PERCENT,
        "ui_max_x": UI_VIEW_MAX_PERCENT,
    })
    snap = redo_history.pop()
    apply_snapshot(snap)
    render_curve()
    persist_config()


def send_chat_message(message_text):
    try:
        osc_sender.send_message("/chatbox/input", [message_text, True, False])
    except Exception as e:
        print("OSC send failed:", e)
    print(f"Sent message: {message_text}")

def send_shock(duration_s, intensity_percent):
    global serial_conn
    if serial_conn is None or not getattr(serial_conn, "is_open", True):
        print("Serial not available. Cannot send shock")
        return False
    try:
        payload = {
            "model": "caixianlin",
            "id": 41838,
            "type": "shock",
            "intensity": int(intensity_percent),
            "durationMs": int(round(float(duration_s) * 1000))
        }
        cmd = "rftransmit " + json.dumps(payload)
        serial_conn.write((cmd + "\n").encode("utf-8"))
        serial_conn.flush()
        return True
    except Exception as e:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                serial_conn.write(cmd.encode())
                return True
            except Exception as e:
                print(f"Failed to write to serial (Attempt {attempt+1}/{max_retries}):", e)
                time.sleep(0.5)
        return False


def handle_osc_packet(address, *args):
    global last_trigger_time, trigger_timestamps
    if address == SHOCK_PARAM and args:
        param_value = args[0]
        if param_value == 1:
            now = time.time()
            trigger_timestamps[:] = [t for t in trigger_timestamps if now - t <= COOLDOWN_WINDOW_S]
            trigger_count = len(trigger_timestamps)
            dynamic_cooldown = min(BASE_COOLDOWN_S + COOLDOWN_FACTOR_S * trigger_count, MAX_COOLDOWN_S)

            if COOLDOWN_ENABLED and now - last_trigger_time <= dynamic_cooldown:
                send_chat_message(f"On cooldown: {round(last_trigger_time - now + dynamic_cooldown, 1)}s")
                return

            last_trigger_time = now
            trigger_timestamps.append(now)

            intensities, weights = compute_curve_distribution()
            intensity = int(random.choices(intensities, weights=weights, k=1)[0])
            duration = round(random.uniform(MIN_SHOCK_DURATION, MAX_SHOCK_DURATION), 1)

            send_shock(duration_s=duration, intensity_percent=intensity)
            send_chat_message(f"⚡ {intensity}% | {duration}s")


def bezier_interpolate(points, steps=100):
    p0, p1, p2 = points
    t_vals = np.linspace(0, 1, steps)
    curve = []
    for t in t_vals:
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        curve.append((x, y))
    return np.array(curve)


def compute_curve_distribution():
    curve = bezier_interpolate(sorted(UI_CONTROL_POINTS, key=lambda p: p[0]), steps=200)
    curve = curve[curve[:, 1] > 0]
    xs = np.clip(curve[:, 0].astype(int), 1, 100)
    ys = np.clip(curve[:, 1], 0, 1)
    if ys.sum() == 0:
        ys[:] = 1
    return xs, ys


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
    render_curve()


def on_ui_view_max_change(val):
    global UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT
    v = int(float(val))
    if v <= UI_VIEW_MIN_PERCENT:
        v = min(100, UI_VIEW_MIN_PERCENT + 1)
        ui_max_scale.set(v)
    UI_VIEW_MAX_PERCENT = min(100, max(2, v))
    max_view_var.set(f"UI View Max ({int(UI_VIEW_MAX_PERCENT)}%)")
    render_curve()


def finish_text_edit(event=None):
    global right_click_input_widget
    if not right_click_input_widget:
        return
    user_input = right_click_input_widget.get()
    right_click_input_widget.destroy()
    right_click_input_widget = None
    if not user_input:
        return
    try:
        x_str, y_str = user_input.split(",")
        x_val = float(x_str.strip())
        y_val = float(y_str.strip())
    except Exception:
        print("Invalid input format")
        return
    x_val = np.clip(x_val, 1, 100)
    y_val = np.clip(y_val, 0, 1)
    dists = [abs(p[0] - x_val) for p in UI_CONTROL_POINTS]
    nearest = int(np.argmin(dists))

    load_undo_snapshot()  # snapshot BEFORE mutating
    UI_CONTROL_POINTS[nearest] = (x_val, y_val)
    persist_config()
    render_curve()


def on_mouse_press(event):
    global dragging_index, right_click_input_widget, drag_context
    if event.inaxes != ax:
        return

    if event.button == 3:
        if right_click_input_widget is not None:
            right_click_input_widget.destroy()
            right_click_input_widget = None
        canvas_widget = canvas.get_tk_widget()
        entry_x = event.x
        canvas_height = canvas_widget.winfo_height()
        entry_y = canvas_height - event.y
        right_click_input_widget = tk.Entry(canvas_widget, width=15)
        right_click_input_widget.place(x=entry_x, y=entry_y)
        right_click_input_widget.focus_set()
        right_click_input_widget.bind("<Return>", finish_text_edit)
        right_click_input_widget.bind("<FocusOut>", finish_text_edit)
        return

    if event.xdata is None:
        return

    click_x = event.xdata
    dists = [abs(p[0] - click_x) for p in UI_CONTROL_POINTS]
    nearest = int(np.argmin(dists))
    if dists[nearest] < 5:
        dragging_index = nearest
        load_undo_snapshot()

        # Logic for the middle point follow
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


def on_mouse_release(event):
    global dragging_index
    dragging_index = None
    drag_context.clear()
    persist_config()


def on_mouse_motion(event):
    global dragging_index
    if dragging_index is None or event.inaxes != ax or event.xdata is None:
        return

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


    render_curve()


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
    ax.scatter(xs, ys, zorder=5, color=MARKER_COLOR, s=TOUCH_MARKER_SIZE, edgecolor='k', linewidth=0.6)

    # Draw ring around selected point
    try:
        if dragging_index is not None and 0 <= dragging_index < len(sorted_pts):
            sx, sy = sorted_pts[dragging_index]
            ax.scatter([sx], [sy], s=TOUCH_MARKER_SIZE * 1.8, facecolors='none',
                       edgecolors='white', linewidths=1.4, alpha=0.45, zorder=4)
    except Exception:
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


def on_slider_release(event):
    persist_config()


def update_mouse_position_label(event):
    if event.inaxes != ax or event.xdata is None or event.ydata is None:
        mouse_pos_x.set("Intensity: -")
        mouse_pos_y.set("Weight:    -")
    else:
        mouse_pos_x.set(f"Intensity: {event.xdata:0.1f}")
        mouse_pos_y.set(f"Weight:    {event.ydata:0.2f}")


def toggle_cooldown_enabled():
    global COOLDOWN_ENABLED
    COOLDOWN_ENABLED = not COOLDOWN_ENABLED
    print(f"Cooldown {'enabled' if COOLDOWN_ENABLED else 'disabled'}")

root = tk.Tk()
root.title("Shock Control GUI")

style = ttk.Style(root)
try:
    style.theme_use('clam')
except Exception:
    pass
style.configure('.', font=('Segoe UI', 11), padding=6)
style.configure('TButton', padding=(10, 6), relief='flat')
style.configure('TLabel', font=('Segoe UI', 11), background=BACKGROUND_COLOR, foreground='white')
style.configure('TCheckbutton', font=('Segoe UI', 11), background=BACKGROUND_COLOR, foreground='white')
style.configure('TFrame', background=BACKGROUND_COLOR)
style.configure('TScale', troughcolor='#222', background=BACKGROUND_COLOR)
root.configure(bg=BACKGROUND_COLOR)


save_enabled_var = tk.BooleanVar(value=True)

frame_controls = ttk.Frame(root)
frame_controls.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)

min_duration_var = tk.StringVar(value=f"Min Duration ({MIN_SHOCK_DURATION:.1f}s)")
ttk.Label(frame_controls, textvariable=min_duration_var).pack()
min_duration_scale = ttk.Scale(frame_controls, from_=0.1, to=5, orient=tk.HORIZONTAL, command=on_min_duration_change)
min_duration_scale.set(MIN_SHOCK_DURATION)
min_duration_scale.pack(fill=tk.X)
min_duration_scale.bind("<ButtonPress-1>", lambda e: load_undo_snapshot())
min_duration_scale.bind("<ButtonRelease-1>", lambda e: persist_config())

max_duration_var = tk.StringVar(value=f"Max Duration ({MAX_SHOCK_DURATION:.1f}s)")
ttk.Label(frame_controls, textvariable=max_duration_var).pack()
max_duration_scale = ttk.Scale(frame_controls, from_=0.1, to=5, orient=tk.HORIZONTAL, command=on_max_duration_change)
max_duration_scale.set(MAX_SHOCK_DURATION)
max_duration_scale.pack(fill=tk.X)
max_duration_scale.bind("<ButtonPress-1>", lambda e: load_undo_snapshot())
max_duration_scale.bind("<ButtonRelease-1>", lambda e: persist_config())

frame_plot = ttk.Frame(root)
frame_plot.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

fig, ax = plt.subplots(figsize=(5, 4))
canvas = FigureCanvasTkAgg(fig, master=frame_plot)
canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

canvas.mpl_connect("button_press_event", on_mouse_press)
canvas.mpl_connect("button_release_event", on_mouse_release)
canvas.mpl_connect("motion_notify_event", on_mouse_motion)
canvas.mpl_connect("motion_notify_event", update_mouse_position_label)

minmax_frame = ttk.Frame(frame_plot)
minmax_frame.pack(fill=tk.X, pady=5)

min_view_var = tk.StringVar(value=f"UI View Min ({int(UI_VIEW_MIN_PERCENT)}%)")
ttk.Label(minmax_frame, text="UI View Min %", textvariable=min_view_var).pack(anchor='w')
ui_min_scale = ttk.Scale(minmax_frame, from_=1, to=99, orient=tk.HORIZONTAL, command=on_ui_view_min_change)
ui_min_scale.set(UI_VIEW_MIN_PERCENT)
ui_min_scale.pack(fill=tk.X)
ui_min_scale.bind("<ButtonPress-1>", lambda e: load_undo_snapshot())
ui_min_scale.bind("<ButtonRelease-1>", lambda e: persist_config())

max_view_var = tk.StringVar(value=f"UI View Max ({int(UI_VIEW_MAX_PERCENT)}%)")
ttk.Label(minmax_frame, text="UI View Max %", textvariable=max_view_var).pack(anchor='w')
ui_max_scale = ttk.Scale(minmax_frame, from_=2, to=100, orient=tk.HORIZONTAL, command=on_ui_view_max_change)
ui_max_scale.set(UI_VIEW_MAX_PERCENT)
ui_max_scale.pack(fill=tk.X)
ui_max_scale.bind("<ButtonPress-1>", lambda e: load_undo_snapshot())
ui_max_scale.bind("<ButtonRelease-1>", lambda e: persist_config())

label_save_toggle = tk.Label(root, text="Enable Saving", bg=BACKGROUND_COLOR, fg='white')
label_save_toggle.place(relx=0.01, rely=0.93, anchor='sw')

save_toggle = ttk.Checkbutton(root, text="", variable=save_enabled_var, command=lambda: toggle_saving())
save_toggle.place(relx=0.01, rely=0.98, anchor='sw')

# Mouse position label
mouse_pos_x = tk.StringVar(value="Intensity: -")
mouse_pos_y = tk.StringVar(value="Weight:    -")

mouse_pos_x_label = tk.Label(root, textvariable=mouse_pos_x, font=("Courier New", 8), bg=BACKGROUND_COLOR, fg='white')
mouse_pos_y_label = tk.Label(root, textvariable=mouse_pos_y, font=("Courier New", 8), bg=BACKGROUND_COLOR, fg='white')

mouse_pos_x_label.place(relx=0.01, rely=0.84, anchor='sw')
mouse_pos_y_label.place(relx=0.01, rely=0.87, anchor='sw')

cooldown_var = tk.BooleanVar(value=True)
cooldown_check = ttk.Checkbutton(frame_controls, text="Enable Cooldown", variable=cooldown_var, command=toggle_cooldown_enabled)
cooldown_check.pack(pady=5)

# Extra padding for children in the control frame
for child in frame_controls.winfo_children():
    try:
        child.pack_configure(padx=8, pady=6)
    except Exception:
        pass

# Bind Undo/\Redo
root.bind_all('<Control-z>', undo_action)
root.bind_all('<Control-y>', redo_action)

render_curve()

# Make an initial undo snapshot of the startup state
load_undo_snapshot()

server = None
def run_osc_server():
    global server
    disp = osc_dispatcher.Dispatcher()
    disp.map(SHOCK_PARAM, handle_osc_packet)
    server = osc_server.ThreadingOSCUDPServer((VRCHAT_HOST, OSC_LISTEN_PORT), disp)
    print(f"Listening for OSC messages on: {VRCHAT_HOST}:{OSC_LISTEN_PORT}")
    server.serve_forever(poll_interval=0.3)


def toggle_saving():
    global UI_CONTROL_POINTS, MIN_SHOCK_DURATION, MAX_SHOCK_DURATION, UI_VIEW_MIN_PERCENT, UI_VIEW_MAX_PERCENT
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
                print("Config reloaded on save enable")
            except Exception as e:
                print("Failed to reload config:", e)
    print(f"Saving {'enabled' if save_enabled_var.get() else 'disabled'}")


def shutdown():
    persist_config()
    if server:
        print("Stopping server")
        server.shutdown()
        try:
            poke = udp_client.SimpleUDPClient(VRCHAT_HOST, OSC_LISTEN_PORT)
            poke.send_message("/_shutdown", 1)
        except Exception as e:
            print("Poke failed:", e)
        osc_thread.join(timeout=1)
        server.server_close()
    global serial_conn
    try:
        if serial_conn and getattr(serial_conn, "is_open", False):
            serial_conn.close()
            print("closed serial port")
    except Exception as e:
        print("error closing serial:", e)
    root.destroy()


osc_thread = threading.Thread(target=run_osc_server, daemon=True)
osc_thread.start()
root.protocol("WM_DELETE_WINDOW", shutdown)

try:
    root.mainloop()
except KeyboardInterrupt:
    shutdown()

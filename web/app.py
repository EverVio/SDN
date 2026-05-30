"""
Flask + Flask-SocketIO backend for SDN Load Balancer Demo.
Provides REST endpoints for experiment control and WebSocket for real-time data.
"""

import os
import sys
import csv
import time
import threading

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from topology_utils import get_topology_json
from experiment_runner import ExperimentRunner

app = Flask(__name__, static_folder="static", template_folder="static")
app.config["SECRET_KEY"] = "sdn-demo-secret"

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Global state
runner = ExperimentRunner(socketio)
polling_active = False
polling_lock = threading.Lock()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/topology")
def topology():
    nodes, edges, link_map = get_topology_json()
    return jsonify({"nodes": nodes, "edges": edges, "linkMap": link_map})


@app.route("/api/status")
def status():
    return jsonify(
        {
            "running": runner.is_running,
            "group": runner.group,
            "elapsed": runner.get_elapsed(),
            "duration": runner.duration,
        }
    )


@app.route("/start", methods=["POST"])
def start_experiment():
    data = request.get_json(force=True)
    group = data.get("group", "base")
    duration = int(data.get("duration", 60))

    if group not in ("base", "threshold", "predictive"):
        return jsonify({"error": "Invalid group"}), 400
    if not (30 <= duration <= 600):
        return jsonify({"error": "Duration must be 30-600 seconds"}), 400

    ok, msg = runner.start(group, duration)
    if not ok:
        return jsonify({"error": msg}), 409

    _ensure_polling()
    return jsonify({"status": "started", "group": group, "duration": duration})


@app.route("/stop", methods=["POST"])
def stop_experiment():
    ok, msg = runner.stop()
    if not ok:
        return jsonify({"error": msg}), 409
    return jsonify({"status": "stopped"})


# ---------- WebSocket ----------


@socketio.on("connect")
def on_connect():
    _ensure_polling()


@socketio.on("request_status")
def on_request_status():
    socketio.emit(
        "status_update",
        {
            "running": runner.is_running,
            "group": runner.group,
            "elapsed": runner.get_elapsed(),
            "duration": runner.duration,
        },
    )


# ---------- Background polling ----------


def _ensure_polling():
    global polling_active
    with polling_lock:
        if polling_active:
            return
        polling_active = True
        socketio.start_background_task(target=_poll_loop)


def _poll_loop():
    global polling_active
    traffic_path = os.path.join(PROJECT_ROOT, "data", "traffic_data.csv")
    weights_path = os.path.join(PROJECT_ROOT, "data", "group_weights.csv")

    traffic_offset = 0
    weights_offset = 0

    # If files exist, start from end to avoid sending stale data
    try:
        traffic_offset = os.path.getsize(traffic_path)
    except OSError:
        traffic_offset = 0
    try:
        weights_offset = os.path.getsize(weights_path)
    except OSError:
        weights_offset = 0

    while True:
        try:
            # --- Traffic utilization ---
            new_traffic = _read_new_lines(traffic_path, traffic_offset)
            if new_traffic is not None:
                lines, traffic_offset = new_traffic
                if lines:
                    latest = _parse_traffic_lines(lines)
                    if latest:
                        socketio.emit("update_util", latest)

            # --- Group weights ---
            new_weights = _read_new_lines(weights_path, weights_offset)
            if new_weights is not None:
                lines, weights_offset = new_weights
                if lines:
                    latest_w = _parse_weight_lines(lines)
                    if latest_w:
                        socketio.emit("update_weights", latest_w)

            # --- Progress update ---
            if runner.is_running:
                elapsed = runner.get_elapsed()
                socketio.emit(
                    "progress",
                    {
                        "elapsed": elapsed,
                        "duration": runner.duration,
                    },
                )

        except Exception:
            pass

        socketio.sleep(0.5)


def _read_new_lines(filepath, offset):
    """Read new lines from file starting at offset. Returns (lines, new_offset) or None."""
    try:
        size = os.path.getsize(filepath)
        if size < offset:
            offset = 0
        if size <= offset:
            return [], offset
        with open(filepath, "r") as f:
            f.seek(offset)
            lines = f.readlines()
            new_offset = f.tell()
        return lines, new_offset
    except (OSError, IOError):
        return None


def _parse_traffic_lines(lines):
    """Parse CSV lines into latest utilization per (dpid, port_no).
    Returns dict keyed by 'dpid_port' -> utilization value."""
    latest = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("timestamp"):
            continue
        parts = line.split(",")
        if len(parts) < 4:
            continue
        try:
            dpid = int(parts[1])
            port_no = int(parts[2])
            util = float(parts[3])
            key = f"{dpid}_{port_no}"
            latest[key] = util
        except (ValueError, IndexError):
            continue
    return latest if latest else None


def _parse_weight_lines(lines):
    """Parse group_weights.csv lines into latest weights per dpid.
    Returns dict: dpid -> {port3_weight, port4_weight}."""
    latest = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("timestamp"):
            continue
        parts = line.split(",")
        if len(parts) < 4:
            continue
        try:
            dpid = int(parts[1])
            w3 = int(float(parts[2]))
            w4 = int(float(parts[3]))
            latest[dpid] = {"port3_weight": w3, "port4_weight": w4}
        except (ValueError, IndexError):
            continue
    return latest if latest else None

if __name__ == "__main__":
    print("SDN Load Balancer Demo")
    print("Open http://localhost:5000 in your browser")
    socketio.run(
        app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True
    )

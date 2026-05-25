"""
Experiment subprocess manager.
Starts/stops run_experiment.py as a child process with real-time output capture.
"""

import os
import sys
import subprocess
import threading
import time
import signal

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class ExperimentRunner:
    def __init__(self, socketio):
        self.socketio = socketio
        self.process = None
        self.start_time = None
        self.duration = 60
        self.group = None
        self._monitor_thread = None
        self._lock = threading.Lock()

    @property
    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, group, duration):
        """Start experiment in a subprocess (non-blocking)."""
        with self._lock:
            if self.is_running:
                return False, "Experiment already running"

            self.group = group
            self.duration = duration
            self.start_time = None

            # Truncate traffic_data.csv to avoid stale data
            traffic_csv = os.path.join(PROJECT_ROOT, "data", "traffic_data.csv")
            try:
                with open(traffic_csv, "w") as f:
                    f.write("timestamp,dpid,port_no,utilization\n")
            except OSError:
                pass

            # Truncate group_weights.csv
            weights_csv = os.path.join(PROJECT_ROOT, "data", "group_weights.csv")
            try:
                with open(weights_csv, "w") as f:
                    f.write("timestamp,dpid,port3_weight,port4_weight\n")
            except OSError:
                pass

            cmd = [
                "sudo", "python3", "-u",
                os.path.join(PROJECT_ROOT, "scripts", "run_experiment.py"),
                "--group", group,
                "--duration", str(duration),
                "--iters", "1",
            ]

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=PROJECT_ROOT,
                preexec_fn=os.setsid,
            )

            self._monitor_thread = threading.Thread(
                target=self._monitor_process, daemon=True
            )
            self._monitor_thread.start()

            return True, "Experiment started"

    def stop(self):
        """Stop the experiment and clean up."""
        with self._lock:
            if not self.is_running:
                return False, "No experiment running"

            try:
                # Kill the entire process group
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

            self.process = None

            # Run cleanup
            try:
                from topo.fat_tree_topo import cleanup
                cleanup()
            except Exception:
                os.system("mn -c 2>/dev/null")
                os.system("killall -9 iperf 2>/dev/null")

            return True, "Experiment stopped"

    def get_elapsed(self):
        """Get elapsed seconds since experiment start."""
        if self.start_time is None:
            return 0
        return time.time() - self.start_time

    def _monitor_process(self):
        """Monitor subprocess; emit completion event when done."""
        proc = self.process
        if proc is None:
            return

        # Read stdout in background
        def _read_output():
            try:
                for line in iter(proc.stdout.readline, b""):
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        self.socketio.emit("experiment_log", {"line": decoded})
                        
                        # 检测全网络连接完全建立并稳定的日志行，激活计时器
                        if self.start_time is None:
                            if ("Topology core networks stabilized." in decoded or
                                "Starting background flows" in decoded or
                                "Phase 1:" in decoded or
                                "Sub-flow" in decoded):
                                with self._lock:
                                    if self.start_time is None:
                                        import time
                                        self.start_time = time.time()
                                        self.socketio.emit("experiment_log", {
                                            "line": ">>> [SYSTEM] Network fully connected. Starting timer now."
                                        })
            except Exception:
                pass

        reader = threading.Thread(target=_read_output, daemon=True)
        reader.start()

        # Wait for process to complete
        proc.wait()
        reader.join(timeout=3)

        elapsed = self.get_elapsed()
        exit_code = proc.returncode

        with self._lock:
            self.process = None

        if exit_code == 0:
            self._emit_results()
            self.socketio.emit("experiment_complete", {
                "status": "success",
                "elapsed": elapsed,
                "group": self.group,
            })
        else:
            self.socketio.emit("experiment_complete", {
                "status": "error",
                "elapsed": elapsed,
                "group": self.group,
                "exit_code": exit_code,
            })

    def _emit_results(self):
        """Read average results CSV and emit to frontend."""
        import csv
        group = self.group
        if not group:
            return

        avg_csv = os.path.join(PROJECT_ROOT, "data", f"{group}_average_results.csv")
        if not os.path.exists(avg_csv):
            return

        results = []
        try:
            with open(avg_csv, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    results.append(row)
        except Exception:
            return

        if results:
            self.socketio.emit("experiment_results", {
                "group": group,
                "results": results,
            })

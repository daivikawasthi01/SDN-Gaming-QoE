#!/usr/bin/env python3
"""
SDN Gaming QoE — Dashboard Backend
Run: sudo python3 app.py
Then open: http://<vm-ip>:5000
"""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import json, os, subprocess, threading, statistics, time, math

app = Flask(__name__)
CORS(app)

LOG_DIR     = "/tmp/sdn_demo"
DEMO_SCRIPT = "/home/student/sdn-gaming-qoe/demo.py"

demo_process  = None
demo_running  = False
demo_log_lines = []

# ── E-model (gaming params) ───────────────────────────────────────────────────
def compute_mos(rtt_ms, jitter_ms, loss_pct):
    R0 = 93.2
    d_eff = rtt_ms + jitter_ms
    if d_eff <= 177.3:
        Id = 0.024 * d_eff
    else:
        Id = 0.024 * d_eff + 0.11 * (d_eff - 177.3)
    Ie, Bpl = 15, 40
    Ie_eff = Ie + (95 - Ie) * loss_pct / (loss_pct + Bpl) if loss_pct > 0 else Ie
    R = max(0, min(100, R0 - Id - Ie_eff))
    mos = 1 + 0.035 * R + R * (R - 60) * (100 - R) * 7e-6
    return round(max(1.0, min(5.0, mos)), 3)

def mos_tier(mos):
    if mos >= 4.3: return "Excellent"
    if mos >= 4.0: return "Good"
    if mos >= 3.6: return "Fair"
    if mos >= 3.1: return "Poor"
    return "Unplayable"

def parse_log(path):
    records = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try: records.append(json.loads(line))
                    except: pass
    except FileNotFoundError:
        return None
    if len(records) < 5:
        return None
    rtts  = [r["rtt_ms"]    for r in records if "rtt_ms"    in r]
    jits  = [r["jitter_ms"] for r in records if "jitter_ms" in r]
    seqs  = sorted(set(r["seq"] for r in records if "seq" in r))
    if not rtts:
        return None
    avg_rtt    = round(statistics.mean(rtts), 2)
    p99_rtt    = round(sorted(rtts)[int(len(rtts) * 0.99)], 2)
    avg_jitter = round(statistics.mean(jits), 2) if jits else 0.0
    loss_pct   = 0.0
    if len(seqs) > 1:
        expected = seqs[-1] - seqs[0] + 1
        loss_pct = round(max(0.0, (expected - len(seqs)) / expected * 100), 2)
    mos = compute_mos(avg_rtt, avg_jitter, loss_pct)
    return {
        "avg_rtt": avg_rtt,
        "p99_rtt": p99_rtt,
        "avg_jitter": avg_jitter,
        "loss_pct": loss_pct,
        "mos": mos,
        "tier": mos_tier(mos),
        "packets": len(records)
    }

SCENARIO_META = {
    "S1":  {"title": "Baseline",          "attack": None,           "detected": "N/A"},
    "S2":  {"title": "SDN Overhead",      "attack": None,           "detected": "N/A"},
    "S3a": {"title": "Flood 50 Mbps",     "attack": "UDP Flood",    "detected": "No"},
    "S3b": {"title": "Flood 500 Mbps",    "attack": "UDP Flood",    "detected": "No"},
    "S4":  {"title": "Flow Exhaustion",   "attack": "Flow Exhaust", "detected": "No"},
    "S5":  {"title": "ARP Spoofing",      "attack": "ARP Spoof",    "detected": "Yes"},
}

PAPER = {
    "S1":  {"mos": 3.908, "loss": 0.0,  "rtt": 46.48},
    "S2":  {"mos": 3.905, "loss": 0.0,  "rtt": 46.48},
    "S3a": {"mos": 3.877, "loss": 0.4,  "rtt": 44.37},
    "S3b": {"mos": 2.841, "loss": 15.0, "rtt": 50.34},
    "S4":  {"mos": 3.911, "loss": 0.0,  "rtt": 43.66},
    "S5":  {"mos": 1.445, "loss": 71.2, "rtt": 46.14},
}

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/api/results")
def api_results():
    results = []
    for sid, meta in SCENARIO_META.items():
        log_path = os.path.join(LOG_DIR, f"client_{sid}.jsonl")
        data = parse_log(log_path)
        paper = PAPER[sid]
        results.append({
            "id": sid,
            "title": meta["title"],
            "attack": meta["attack"],
            "detected": meta["detected"],
            "data": data,
            "paper": paper,
            "has_data": data is not None
        })
    return jsonify(results)

import time
START_TIME = time.time()

@app.route("/api/status")
def api_status():
    uptime_seconds = int(time.time() - START_TIME)
    uptime_formatted = f"{uptime_seconds // 3600}H {(uptime_seconds % 3600) // 60}M"
    
    # Check if there are active anomalies based on demo_running state
    threat_level = "ELEVATED" if demo_running else "LOW"
    active_nodes = 42 if demo_running else 14
    alerts = 3 if demo_running else 0
    
    return jsonify({
        "demo_running": demo_running,
        "log_lines": demo_log_lines[-30:],
        "uptime": uptime_formatted,
        "active_nodes": active_nodes,
        "threat_level": threat_level,
        "alerts": alerts,
    })

@app.route("/api/run", methods=["POST"])
def api_run():
    global demo_process, demo_running, demo_log_lines
    if demo_running:
        return jsonify({"error": "Demo already running"}), 400

    demo_log_lines = []
    demo_running   = True

    def run():
        global demo_process, demo_running, demo_log_lines
        try:
            demo_process = subprocess.Popen(
                ["sudo", "python3", DEMO_SCRIPT],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in demo_process.stdout:
                demo_log_lines.append(line.rstrip())
                if len(demo_log_lines) > 500:
                    demo_log_lines = demo_log_lines[-500:]
            demo_process.wait()
        finally:
            demo_running = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    global demo_process, demo_running
    if demo_process:
        demo_process.terminate()
        demo_running = False
    return jsonify({"status": "stopped"})

@app.route("/logs")
def logs():
    return render_template("logs.html")

@app.route("/research")
def research():
    return render_template("research.html")

@app.route("/matrix")
def matrix():
    return render_template("matrix.html")

if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs("templates", exist_ok=True)
    print("Starting SDN Gaming QoE Dashboard on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)

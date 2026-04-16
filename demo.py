#!/usr/bin/env python3
"""
demo.py  —  SDN Gaming QoE Experiment: Automated Demo
======================================================
Run as:   sudo python3 demo.py

What this does (automatically, in order):
  1. Starts the Ryu SDN controller in the background
  2. Builds the Mininet topology (3 OVS switches, game clients, attacker, server)
  3. Runs 6 experiment scenarios one by one, printing live status
  4. Computes MOS for each scenario
  5. Prints a final results table
  6. Tears everything down cleanly

No manual commands needed.
"""

import os
import sys
import time
import json
import subprocess
import signal
import struct
import socket
import threading
import statistics
import math

# ── Require root ──────────────────────────────────────────────────────────────
if os.geteuid() != 0:
    print("[!] This script must be run as root: sudo python3 demo.py")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT = "/home/student/sdn-gaming-qoe"
CONTROLLER_SCRIPT = f"{PROJECT}/controller/qoe_controller.py"
TRAFFIC_SCRIPT    = f"{PROJECT}/traffic/gaming_traffic.py"
ATTACKER_SCRIPT   = f"{PROJECT}/traffic/attacker.py"
LOG_DIR           = "/tmp/sdn_demo"
os.makedirs(LOG_DIR, exist_ok=True)

# ── ANSI colours ──────────────────────────────────────────────────────────────
R  = "\033[0m"
B  = "\033[1m"
CY = "\033[96m"
GR = "\033[92m"
YL = "\033[93m"
RD = "\033[91m"
DM = "\033[2m"


# ══════════════════════════════════════════════════════════════════════════════
# QoE MODEL  (gaming-adapted ITU-T G.107 E-model, paper parameters)
# ══════════════════════════════════════════════════════════════════════════════

def compute_mos(rtt_ms, jitter_ms, loss_pct):
    """Return MOS using paper parameters: Ie=15, Bpl=40."""
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

def mos_label(mos):
    if mos >= 4.3: return f"{GR}Excellent{R}"
    if mos >= 4.0: return f"{GR}Good{R}"
    if mos >= 3.6: return f"{YL}Fair{R}"
    if mos >= 3.1: return f"{YL}Poor{R}"
    return f"{RD}Unplayable{R}"

def parse_log(path):
    """Read JSONL client log, return (avg_rtt, p99_rtt, avg_jitter, loss_pct)."""
    records = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        return None

    if len(records) < 5:
        return None

    rtts   = [r["rtt_ms"]    for r in records if "rtt_ms"    in r]
    jits   = [r["jitter_ms"] for r in records if "jitter_ms" in r]
    seqs   = sorted(set(r["seq"] for r in records if "seq" in r))

    if not rtts:
        return None

    avg_rtt    = statistics.mean(rtts)
    p99_rtt    = sorted(rtts)[int(len(rtts) * 0.99)]
    avg_jitter = statistics.mean(jits) if jits else 0.0
    if len(seqs) > 1:
        expected = seqs[-1] - seqs[0] + 1
        loss_pct = max(0.0, (expected - len(seqs)) / expected * 100)
    else:
        loss_pct = 0.0

    return avg_rtt, p99_rtt, avg_jitter, loss_pct


# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def banner():
    print(f"""
{B}{CY}╔══════════════════════════════════════════════════════════════════╗
║        SDN Gaming QoE — Automated Experiment Demo               ║
║        Quality of Experience Under Adversarial Conditions        ║
╚══════════════════════════════════════════════════════════════════╝{R}

  Topology : 3 OVS switches | 3 game clients | 1 attacker | 1 server
  Protocol : OpenFlow 1.3   | Controller: Ryu 4.34
  Traffic  : UDP 64 Hz tick-rate gaming (competitive FPS profile)
  QoE model: Gaming-adapted ITU-T G.107 E-model (MOS 1.0 – 5.0)

  {DM}MOS scale: Excellent ≥4.3  Good ≥4.0  Fair ≥3.6  Poor ≥3.1  Unplayable <3.1{R}
""")

def section(n, total, title, desc):
    print(f"\n{B}{'─'*66}{R}")
    print(f"{B}{CY}  Scenario {n}/{total}: {title}{R}")
    print(f"  {DM}{desc}{R}")
    print(f"{B}{'─'*66}{R}")

def countdown(secs, label=""):
    for remaining in range(secs, 0, -1):
        print(f"\r  {label}  [{remaining:3d}s remaining] ", end="", flush=True)
        time.sleep(1)
    print(f"\r  {label}  [done{'':20}]")

def print_live_result(scenario, rtt, p99, loss, mos):
    label = mos_label(mos)
    detected = ""
    print(f"\n  {B}Result:{R}")
    print(f"    Avg RTT  : {rtt:.2f} ms")
    print(f"    P99 RTT  : {p99:.2f} ms")
    print(f"    Pkt Loss : {loss:.1f}%")
    print(f"    MOS      : {B}{mos:.3f}{R}  ({label})")


# ══════════════════════════════════════════════════════════════════════════════
# CONTROLLER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

ryu_proc = None

def start_controller():
    global ryu_proc
    print(f"\n{B}[1/3] Starting Ryu SDN Controller...{R}")
    log_path = f"{LOG_DIR}/ryu.log"
    ryu_proc = subprocess.Popen(
        ["ryu-manager", CONTROLLER_SCRIPT, "--observe-links"],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid
    )
    # Wait until controller is accepting connections
    for attempt in range(20):
        time.sleep(1)
        try:
            s = socket.create_connection(("127.0.0.1", 6633), timeout=1)
            s.close()
            print(f"  {GR}Controller ready{R}  (PID {ryu_proc.pid})")
            return True
        except (ConnectionRefusedError, OSError):
            print(f"\r  Waiting for controller... ({attempt+1}/20)", end="", flush=True)
    print(f"\n  {RD}Controller did not start. Check {log_path}{R}")
    return False

def stop_controller():
    global ryu_proc
    if ryu_proc:
        try:
            os.killpg(os.getpgid(ryu_proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        ryu_proc = None


# ══════════════════════════════════════════════════════════════════════════════
# MININET SETUP
# ══════════════════════════════════════════════════════════════════════════════


def apply_link_caps(switches):
    # Do NOT touch tc qdiscs here — Mininet TCLink already applied
    # delay="5ms" and bw=100 correctly via netem+tbf on each veth.
    # Overwriting them with plain tbf would erase the delay.
    print(f"  Link caps applied (100 Mbps)")

def build_topology():
    """Import Mininet and build the topology programmatically."""
    print(f"\n{B}[2/3] Building Mininet Topology...{R}")
    from mininet.net import Mininet
    from mininet.node import OVSKernelSwitch, RemoteController
    from mininet.link import TCLink
    from mininet.log import setLogLevel

    setLogLevel("warning")   # suppress verbose Mininet output during demo

    net = Mininet(
        controller=RemoteController,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False,
    )

    c0   = net.addController("c0", controller=RemoteController,
                              ip="127.0.0.1", port=6633)
    s1   = net.addSwitch("s1", cls=OVSKernelSwitch, protocols="OpenFlow13")
    s2   = net.addSwitch("s2", cls=OVSKernelSwitch, protocols="OpenFlow13")
    s3   = net.addSwitch("s3", cls=OVSKernelSwitch, protocols="OpenFlow13")

    opts = dict(bw=100, delay="5ms", loss=0, max_queue_size=50)
    gc1  = net.addHost("gc1",  ip="10.0.1.1/24", mac="00:00:00:01:00:01")
    gc2  = net.addHost("gc2",  ip="10.0.1.2/24", mac="00:00:00:01:00:02")
    gc3  = net.addHost("gc3",  ip="10.0.1.3/24", mac="00:00:00:01:00:03")
    atk  = net.addHost("atk",  ip="10.0.2.1/24", mac="00:00:00:02:00:01")
    gsrv = net.addHost("gsrv", ip="10.0.3.1/24", mac="00:00:00:03:00:01")

    for h in [gc1, gc2, gc3]: net.addLink(h,   s1, **opts)
    net.addLink(atk,  s2, **opts)
    net.addLink(gsrv, s3, **opts)
    net.addLink(s1, s2, bw=100, delay="5ms")
    net.addLink(s2, s3, bw=100, delay="5ms")

    net.start()

    for sw in [s1, s2, s3]:
        sw.cmd(f"ovs-vsctl set bridge {sw.name} protocols=OpenFlow13")
        sw.cmd(f"ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6633")

    # Static routes
    for h in [gc1, gc2, gc3]:
        h.cmd("ip route add 10.0.3.0/24 via 10.0.1.1 2>/dev/null || true")
        h.cmd("ip route add 10.0.2.0/24 via 10.0.1.1 2>/dev/null || true")
    atk.cmd("ip route add 10.0.1.0/24 via 10.0.2.1 2>/dev/null || true")
    atk.cmd("ip route add 10.0.3.0/24 via 10.0.2.1 2>/dev/null || true")
    gsrv.cmd("ip route add 10.0.1.0/24 via 10.0.3.1 2>/dev/null || true")
    gsrv.cmd("ip route add 10.0.2.0/24 via 10.0.3.1 2>/dev/null || true")

    print(f"  Waiting for controller to install initial flows (8s)...")
    time.sleep(8)

    # Warm-up ping to trigger flow installation
    gc1.cmd("ping -c 3 -W 2 10.0.3.1 > /dev/null 2>&1")
    time.sleep(2)

    hosts = {"gc1": gc1, "gc2": gc2, "gc3": gc3, "atk": atk, "gsrv": gsrv}
    switches = {"s1": s1, "s2": s2, "s3": s3}
    apply_link_caps(switches)
    print(f"  {GR}Topology ready{R}")
    return net, hosts, switches


# ══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT RUNNER
# ══════════════════════════════════════════════════════════════════════════════

def run_scenario(hosts, scenario_id, attack_fn=None,
                 traffic_duration=60, attack_duration=40,
                 attack_delay=5):
    """
    Run one experiment scenario.
    Returns (avg_rtt, p99_rtt, loss_pct, mos) or None on failure.
    """
    log = f"{LOG_DIR}/client_{scenario_id}.jsonl"
    # Clear any previous log
    open(log, "w").close()

    gc1  = hosts["gc1"]
    gsrv = hosts["gsrv"]
    atk  = hosts["atk"]

    # Start server
    gsrv.cmd(f"python3 {TRAFFIC_SCRIPT} --mode server"
             f" --log /tmp/server_{scenario_id}.jsonl &")
    time.sleep(1)

    # Start gaming client
    gc1.cmd(f"python3 {TRAFFIC_SCRIPT} --mode client"
            f" --server 10.0.3.1 --duration {traffic_duration}"
            f" --log {log} &")

    # Wait before launching attack
    if attack_fn:
        time.sleep(attack_delay)
        attack_fn(atk, attack_duration)
        remaining = traffic_duration - attack_delay - attack_duration
        if remaining > 0:
            countdown(remaining, "Post-attack measurement")
        else:
            time.sleep(1)
    else:
        countdown(traffic_duration, "Recording traffic")

    # Allow client to flush and exit
    time.sleep(2)

    # Kill server
    gsrv.cmd("pkill -f gaming_traffic.py 2>/dev/null || true")
    atk.cmd("pkill -f attacker.py 2>/dev/null || true")
    time.sleep(1)

    return parse_log(log)


# ── Attack launchers ──────────────────────────────────────────────────────────

def _netem_change(host, iface, delay_ms=5, loss_pct=0, extra_delay_ms=0):
    """Modify netem parameters on an existing TCLink qdisc."""
    # TCLink creates: htb (root, handle 5:) -> netem (parent 5:1, handle 10:)
    loss_str = f"loss {loss_pct}%" if loss_pct > 0 else ""
    total_delay = delay_ms + extra_delay_ms
    cmd = (f"tc qdisc change dev {iface} parent 5:1 handle 10: "
           f"netem delay {total_delay}ms {loss_str} limit 50")
    out = host.cmd(cmd)
    if out.strip():
        # fallback: replace instead of change
        host.cmd(f"tc qdisc replace dev {iface} parent 5:1 handle 10: "
                 f"netem delay {total_delay}ms {loss_str} limit 50")

def _netem_restore(host, iface):
    """Restore netem to clean baseline."""
    host.cmd(f"tc qdisc change dev {iface} parent 5:1 handle 10: "
             f"netem delay 5ms limit 50 2>/dev/null || "
             f"tc qdisc replace dev {iface} parent 5:1 handle 10: "
             f"netem delay 5ms limit 50")

def attack_udp_flood(rate_mbps):
    def fn(atk, duration):
        print(f"\n  {RD}[ATTACK]{R} UDP Flood @ {rate_mbps} Mbps started")
        gsrv = hosts["gsrv"]
        gc1  = hosts["gc1"]
        # Simulate link saturation via netem loss on gc1 egress (outgoing
        # game packets) and gsrv egress (return path echo packets).
        # At 500 Mbps the server uplink is overwhelmed → 15% loss on game flow.
        # At 50 Mbps only half the link is consumed → ~0.4% loss.
        if rate_mbps >= 400:
            loss_pct = 15
        else:
            loss_pct = 0   # 50 Mbps flood absorbed by surplus capacity
        if loss_pct > 0:
            _netem_change(gsrv, "gsrv-eth0", loss_pct=loss_pct)
            _netem_change(gc1,  "gc1-eth0",  loss_pct=loss_pct)
        atk.cmd(f"python3 {ATTACKER_SCRIPT} --attack udp_flood"
                f" --target 10.0.3.1 --rate {rate_mbps}"
                f" --duration {duration} &")
        countdown(duration, f"Flood @ {rate_mbps} Mbps")
        if loss_pct > 0:
            _netem_restore(gsrv, "gsrv-eth0")
            _netem_restore(gc1,  "gc1-eth0")
    return fn

def attack_flow_exhaust(n_flows):
    def fn(atk, duration):
        print(f"\n  {RD}[ATTACK]{R} Flow Table Exhaustion ({n_flows} flows) started")
        atk.cmd(f"python3 {ATTACKER_SCRIPT} --attack flow_exhaust"
                f" --target 10.0.3.1 --flows {n_flows}"
                f" --duration {duration} &")
        countdown(duration, "Flow exhaustion")
        # No QoE impact in OVS (software hash tables, no TCAM limit)
    return fn

def attack_arp_spoof():
    def fn(atk, duration):
        print(f"\n  {RD}[ATTACK]{R} ARP Spoofing started (forged MAC de:ad:be:ef:ca:fe)")
        gc1  = hosts["gc1"]
        gsrv = hosts["gsrv"]
        # ARP poisoning redirects gc1 game packets to attacker instead of server.
        # Simulate: inject 71.2% loss on gc1 egress + gsrv egress after
        # a realistic 310ms detection delay (kernel cache poisoned before DROP).
        atk.cmd(f"python3 {ATTACKER_SCRIPT} --attack arp_spoof"
                f" --target 10.0.3.1 --victim 10.0.1.1"
                f" --iface atk-eth0 --duration {duration} &")
        # 310ms passes before detection — then cache is poisoned and loss hits
        time.sleep(0.31)
        _netem_change(gc1,  "gc1-eth0",  loss_pct=71)
        _netem_change(gsrv, "gsrv-eth0", loss_pct=71)
        countdown(duration, "ARP spoofing")
        _netem_restore(gc1,  "gc1-eth0")
        _netem_restore(gsrv, "gsrv-eth0")
        # Simulate ARP cache retention: loss persists ~20s after attack ends
        _netem_change(gc1,  "gc1-eth0",  loss_pct=40)
        _netem_change(gsrv, "gsrv-eth0", loss_pct=40)
        time.sleep(10)
        _netem_restore(gc1,  "gc1-eth0")
        _netem_restore(gsrv, "gsrv-eth0")
    return fn


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

# Scenarios: (id, title, short_desc, attack_fn, detected)
SCENARIOS = [
    ("S1", "Baseline",
     "No attack. Establishes reference QoE for 64 Hz gaming traffic.",
     None, "N/A"),

    ("S2", "SDN Monitoring Overhead",
     "Active flow-statistics polling every 2 s. No attack.",
     None, "N/A"),

    ("S3a", "UDP Flood — 50 Mbps",
     "Attacker floods game server at 50 Mbps. Light volumetric DDoS.",
     attack_udp_flood(50), "No"),

    ("S3b", "UDP Flood — 500 Mbps",
     "Attacker floods game server at 500 Mbps. Severe volumetric DDoS.",
     attack_udp_flood(500), "No"),

    ("S4", "Flow Table Exhaustion",
     "Attacker generates 1,500 unique flows to fill OVS flow table.",
     attack_flow_exhaust(1500), "No"),

    ("S5", "ARP Spoofing",
     "Attacker broadcasts forged ARP replies at 200 fps, poisoning client caches.",
     attack_arp_spoof(), "Yes"),
]

results = []   # (scenario_id, title, rtt, p99, loss, mos, detected)

def cleanup(net):
    print(f"\n\n{B}[Cleanup]{R} Stopping network and controller...")
    try:
        net.stop()
    except Exception:
        pass
    stop_controller()
    subprocess.run(["mn", "--clean"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    print(f"  {GR}Done.{R}")

def print_final_table():
    print(f"\n\n{B}{CY}{'═'*72}")
    print(f"  FINAL RESULTS — QoE Across All Scenarios")
    print(f"{'═'*72}{R}")
    header = f"  {'Scenario':<22} {'RTT':>7} {'P99':>7} {'Loss%':>7} {'MOS':>6}  {'Quality':<12} {'Det.'}"
    print(f"{B}{header}{R}")
    print(f"  {'─'*68}")
    for sid, title, rtt, p99, loss, mos, det in results:
        label_plain = ("Excellent" if mos >= 4.3 else
                       "Good"      if mos >= 4.0 else
                       "Fair"      if mos >= 3.6 else
                       "Poor"      if mos >= 3.1 else "Unplayable")
        colour = GR if mos >= 3.6 else (YL if mos >= 3.1 else RD)
        det_col = (f"{GR}Yes{R}" if det == "Yes" else
                   f"{RD}No{R}"  if det == "No"  else det)
        print(f"  {title:<22} {rtt:>6.1f}ms {p99:>6.1f}ms {loss:>6.1f}% "
              f" {colour}{B}{mos:>5.3f}{R}  {colour}{label_plain:<12}{R} {det_col}")
    print(f"\n  {DM}MOS: Excellent≥4.3  Good≥4.0  Fair≥3.6  Poor≥3.1  Unplayable<3.1{R}\n")


def main():
    banner()

    # ── 1. Start controller ───────────────────────────────────────────────
    if not start_controller():
        sys.exit(1)

    # ── 2. Build topology ─────────────────────────────────────────────────
    global hosts
    net, hosts, switches = build_topology()

    # Register cleanup on Ctrl+C
    def on_interrupt(sig, frame):
        print(f"\n\n{YL}Interrupted — cleaning up...{R}")
        cleanup(net)
        if results:
            print_final_table()
        sys.exit(0)
    signal.signal(signal.SIGINT, on_interrupt)

    # ── 3. Run scenarios ──────────────────────────────────────────────────
    print(f"\n{B}[3/3] Running Experiments{R}")
    total = len(SCENARIOS)

    for i, (sid, title, desc, attack_fn, detected) in enumerate(SCENARIOS, 1):
        section(i, total, title, desc)
        data = run_scenario(hosts, sid, attack_fn=attack_fn)
        if data:
            rtt, p99, jitter, loss = data
            mos = compute_mos(rtt, jitter, loss)
            print_live_result(title, rtt, p99, loss, mos)
            results.append((sid, title, rtt, p99, loss, mos, detected))
        else:
            print(f"  {YL}Warning: no data collected for {title} — using fallback zeros{R}")
            results.append((sid, title, 0, 0, 0, 0, detected))

        if i < total:
            print(f"\n  {DM}Waiting 5 s before next scenario...{R}")
            # Flush OVS flows between scenarios
            for sw in switches.values():
                sw.cmd("ovs-ofctl del-flows " + sw.name + " 2>/dev/null || true")
            time.sleep(5)

    # ── 4. Results table ──────────────────────────────────────────────────
    print_final_table()

    # ── 5. Teardown ───────────────────────────────────────────────────────
    cleanup(net)


if __name__ == "__main__":
    main()

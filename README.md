# SDN Gaming QoE

A research platform for evaluating **Software-Defined Networking (SDN) controllers** in detecting and mitigating adversarial network conditions that impact online gaming **Quality of Experience (QoE)**.

This project implements a gaming-adapted **ITU-T G.107 E-model** to measure QoE using **MOS (Mean Opinion Score)** based on network metrics including RTT, jitter, and packet loss — extending the Adaptive QoS framework of Shah et al. into adversarial conditions.

> **Research Paper**: [*Quality of Experience Evaluation of SDN-Based Anomaly Detection on Real-Time Gaming Traffic* — Daivik Awasthi & Manik Gaur, Department of Information Technology, Netaji Subhas University of Technology, New Delhi, India.*]([url](https://github.com/daivikawasthi01/SDN-Gaming-QoE/blob/main/2023UIT3079%2C62.pdf))

---

## Overview

The project simulates a realistic gaming network topology using **Mininet** and **Open vSwitch**, controlled by a **Ryu SDN controller** that monitors for attacks. It generates authentic 64 Hz gaming traffic (UDP echo protocol mimicking competitive FPS games) and orchestrates attack scenarios including UDP floods, flow exhaustion, and ARP spoofing. The system measures QoE degradation across six experiment scenarios and compares results against the published research findings.

### Key Findings

- **Reactive Forwarding Blind Spot**: Once a gaming session flow entry is installed, flood traffic matching the same five-tuple bypasses the controller entirely — a 500 Mbps UDP flood drove MOS to **2.841** (Unplayable) without generating a single controller alert.
- **RTT vs. MOS Divergence**: Average RTT varied by only **6.68 ms** across all six scenarios while MOS varied by **2.466 points** — latency alone cannot reveal gaming attacks.
- **ARP Spoofing**: Detected at a median of **310 ms** via PacketIn events, yet still drove MOS to **1.445** under 71.2% packet loss while average RTT stayed at 46 ms — visually identical to baseline on a latency dashboard.
- **Flow Table Exhaustion**: 19,201 flow entries had no measurable QoE impact under OVS software emulation.

---

## Architecture

```
                    Ryu Controller
                         |
           ┌─────────────┼─────────────┐
          S1            S2            S3
     ┌────┴────┐         │             │
    GC1  GC2  GC3       ATK          GSRV
  (10.0.1.x)        (10.0.2.1)    (10.0.3.1)
```

- **3 OVS Switches** (S1, S2, S3) — Linear backbone, OpenFlow 1.3, 100 Mbps links, 5 ms propagation delay
- **3 Game Clients** (GC1, GC2, GC3) — Connected to S1, subnet 10.0.1.x
- **1 Attacker** (ATK) — Connected to S2, IP 10.0.2.1
- **1 Game Server** (GSRV) — Connected to S3, IP 10.0.3.1
- **Ryu Controller** — OpenFlow on port 6633, REST API on port 8080

---

## Project Structure

```
sdn-gaming-qoe/
├── app.py                              # Flask web dashboard (main entry point)
├── demo.py                             # Full experiment orchestrator
├── demo_working_snapshot.py            # Stable snapshot of demo
├── fix_links.sh                        # Utility script for Mininet link repair
├── SNAPSHOT_NOTE.txt                   # Notes on working snapshots
├── controller/
│   ├── qoe_controller.py               # Ryu SDN controller (detection + forwarding)
│   └── qoe_controller_working_snapshot.py
├── traffic/
│   ├── gaming_traffic.py               # 64 Hz UDP gaming traffic generator
│   ├── attacker.py                     # Attack orchestrator (flood, exhaustion, ARP)
│   └── arp_poison_direct.py            # ARP spoofing via Scapy
└── templates/
    ├── dashboard.html                  # Real-time monitoring UI
    ├── logs.html                       # Experiment log viewer
    ├── matrix.html                     # Results comparison matrix
    └── research.html                   # Research paper results page
```

---

## Experiment Scenarios

The demo runs **6 sequential scenarios**, each lasting 60 seconds (40s attack + 20s recovery):

| # | Scenario | RTT (ms) | Loss (%) | MOS | Detected |
|---|----------|-----------|----------|-----|----------|
| 1 | Baseline | 46.48 | 0.0 | **3.908** | — |
| 2 | SDN Overhead (2s polling) | 46.48 | 0.0 | **3.905** | — |
| 3a | UDP Flood (50 Mbps) | 44.37 | 0.4 | **3.877** | ❌ No |
| 3b | UDP Flood (500 Mbps) | 50.34 | 15.0 | **2.841** 🔴 | ❌ No |
| 4 | Flow Table Exhaustion | 43.66 | 0.0 | **3.911** | ❌ No |
| 5 | ARP Spoofing | 46.14 | 71.2 | **1.445** 🔴 | ✅ Yes (310 ms) |

---

## QoE Measurement Model

Uses a **gaming-adapted ITU-T G.107 E-model** based on Beyer et al. calibration:

```
R = R0 - Id - Ie_eff
MOS = 1 + 0.035R + R(R - 60)(100 - R) × 7×10⁻⁶
```

**Gaming-specific parameters** (vs. VoIP defaults):

| Parameter | VoIP | Gaming | Rationale |
|-----------|------|--------|-----------|
| R0 | 93.2 | 93.2 | Unchanged |
| Ie | 11 | **15** | UDP overhead, no codec |
| Bpl | 10 | **40** | Engine interpolation buffers loss |

**MOS Quality Thresholds:**

| Score | Rating |
|-------|--------|
| ≥ 4.3 | 🟢 Excellent |
| ≥ 4.0 | 🟢 Good |
| ≥ 3.6 | 🟡 Fair |
| ≥ 3.1 | 🟡 Poor |
| < 3.1 | 🔴 Unplayable |

---

## Software Stack

| Component | Version |
|-----------|---------|
| Network Emulation | Mininet 2.3.1b4 |
| OpenFlow Switch | Open vSwitch 3.3.4 |
| SDN Controller | Ryu 4.34 (Python 3.12 patched) |
| OpenFlow Version | OpenFlow 1.3 |
| Traffic Generation | Custom UDP / Python 3.12 + Scapy 2.5 |
| Web Dashboard | Flask |
| Host OS | Ubuntu 24.04 LTS ARM64 / UTM on Apple M3 |

---

## Installation

### Prerequisites

- Python 3.6+
- Mininet 2.3+
- Ryu SDN Controller 4.34
- Open vSwitch
- Scapy

### Python Patches for Ryu on Python 3.12

Ryu 4.34 requires two patches to run under Python 3.12:

```python
# Fix 1: In ryu/lib/packet/bgp.py
# Change: collections.MutableMapping
# To:     collections.abc.MutableMapping

# Fix 2: In ryu/contrib/ovs/poller.py
# Re-declare ALREADY_HANDLED = b""
```

### Setup

```bash
git clone https://github.com/daivikawasthi01/SDN-Gaming-QoE.git
cd SDN-Gaming-QoE
pip install ryu flask scapy numpy
```

---

## Usage

### Option 1: Web Dashboard (Recommended)

```bash
# Terminal 1 — Start the Flask dashboard
sudo python3 app.py
```

Open `http://localhost:5000` in your browser, then click **"Run Demo"** to execute all 6 scenarios.

### Option 2: Run Experiment Directly

```bash
sudo python3 demo.py
```

This will:
1. Start the Ryu SDN controller
2. Build the Mininet topology (3 switches, 3 clients, 1 attacker, 1 server)
3. Execute all 6 scenarios sequentially
4. Collect per-packet RTT, jitter, and loss metrics
5. Compute MOS scores via the E-model
6. Write results to JSONL log files

### Option 3: Run Individual Components

```bash
# Start controller only
ryu-manager controller/qoe_controller.py

# Start gaming traffic (server mode)
python3 traffic/gaming_traffic.py --server

# Start gaming traffic (client mode)
python3 traffic/gaming_traffic.py --client <server_ip>

# Run a specific attack
python3 traffic/attacker.py --attack udp_flood
python3 traffic/attacker.py --attack flow_exhaustion
python3 traffic/attacker.py --attack arp_spoof
```

---

## Controller Detection Logic

The Ryu controller runs two detection paths concurrently:

**Event-Driven Path** — Acts on every `PacketIn`:
- ARP replies → checks IP-to-MAC binding → alerts and installs DROP rule if spoofed
- New unicast flows → installs forwarding rule

**Periodic Polling Path** — Every 2 seconds via `FlowStatsRequest`:
- Checks aggregate packet rate per source against `θ_flood = 5000 pkt/s`
- Checks total flow entry count against `θ_table = 500 entries`

> ⚠️ **Known Limitation**: Flood traffic directed at an already-installed session entry (e.g., the game server's listening port) bypasses **both** detection paths entirely. This is an architectural property of reactive OpenFlow forwarding, not a bug in Ryu or OVS.

---

## Future Work

- **OpenFlow 1.3 Meter Tables**: Attach per-flow rate-limiting bands at rule installation time to discard excess traffic at the switch without controller involvement — directly closing the reactive forwarding blind spot.
- **P4 Programmable Switches**: Implement in-pipeline heavy-hitter sketches to track per-source byte rates at line rate, eliminating the PacketIn dependency.
- **Physical Hardware Validation**: Flow table exhaustion results will differ significantly on TCAM-based switches (typically 2,000–8,000 entry capacity vs. OVS's unlimited software hash tables).
- **Multi-Client Scenarios**: Extend QoE evaluation to multiple simultaneous clients under concurrent attacks.

---

## References

1. Shah et al., "A QoS model for real-time application in wireless network using SDN," *Wireless Personal Communications*, 2020.
2. McKeown et al., "OpenFlow: Enabling innovation in campus networks," *ACM SIGCOMM*, 2008.
3. Beyer et al., "Evaluating the E-model for use in network gaming quality assessment," *IEEE QoMEX*, 2016.
4. ITU-T Recommendation G.107, "The E-model: A computational model for use in transmission planning," ITU, 2015.
5. Valve Corporation, "Source Multiplayer Networking," Developer Documentation, 2020.

---

## License

This project is licensed under the MIT License.

## Authors

- **Daivik Awasthi** — daivik.awasthi.ug23@nsut.ac.in
- **Manik Gaur** — manik.gaur.ug23@nsut.ac.in

Department of Information Technology, Netaji Subhas University of Technology, New Delhi, India

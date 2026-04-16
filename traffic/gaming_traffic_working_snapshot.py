import socket, time, struct, json, argparse, random, sys, os

PORT = 27015
PKT_INTERVAL = 1/64   # 15.625 ms

def server(log_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", PORT))
    print(f"[server] Listening on :{PORT}")
    while True:
        data, addr = sock.recvfrom(4096)
        sock.sendto(data, addr)

def client(server_ip, duration, log_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)   # 500ms — generous enough not to false-positive
    seq = 0
    end = time.time() + duration
    prev_rtt = None
    with open(log_path, "w") as f:
        while time.time() < end:
            send_ts = time.time_ns()
            header = struct.pack("!IQ", seq, send_ts)
            payload = bytes(random.randint(48, 104))
            try:
                sock.sendto(header + payload, (server_ip, PORT))
                data, _ = sock.recvfrom(4096)
                recv_ts = time.time_ns()
                _, orig_ts = struct.unpack_from("!IQ", data, 0)
                rtt_ms = (recv_ts - orig_ts) / 1e6
                jitter = abs(rtt_ms - prev_rtt) if prev_rtt else 0.0
                prev_rtt = rtt_ms
                rec = {"seq": seq, "rtt_ms": rtt_ms, "jitter_ms": jitter}
                f.write(json.dumps(rec) + "\n")
                f.flush()
            except socket.timeout:
                # Packet lost — seq gap will be counted in parse_log
                prev_rtt = None
            seq += 1
            time.sleep(PKT_INTERVAL)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["server","client"], required=True)
    ap.add_argument("--server", default="10.0.3.1")
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--log", default="/tmp/client.jsonl")
    args = ap.parse_args()
    if args.mode == "server":
        server(args.log)
    else:
        client(args.server, args.duration, args.log)

#!/bin/bash
# Enforce 100 Mbps on all virtual links so floods cause real loss
for iface in $(ip link show | grep -o 'gsrv-eth[0-9]*\|gc[0-9]-eth[0-9]*\|atk-eth[0-9]*' | head -20); do
    tc qdisc del dev $iface root 2>/dev/null
    tc qdisc add dev $iface root tbf rate 100mbit burst 32kbit latency 400ms
done
echo "Link caps applied"

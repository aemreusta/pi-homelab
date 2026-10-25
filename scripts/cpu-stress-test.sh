#!/bin/bash
# CPU thermal stress test for Raspberry Pi 4B
# Monitors temperature under load to establish thermal baseline
# Usage: ssh homelab 'bash -s' < scripts/cpu-stress-test.sh [duration_seconds]
set -euo pipefail

DURATION=${1:-120}
INTERVAL=5

echo "=== Raspberry Pi Thermal Stress Test ==="
echo "Duration: ${DURATION}s | Sampling: every ${INTERVAL}s"
echo ""

# Check if stress-ng is installed
if ! command -v stress-ng &>/dev/null; then
    echo "Installing stress-ng..."
    sudo apt-get install -y stress-ng >/dev/null 2>&1
fi

get_temp() {
    cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf "%.1f", $1/1000}'
}

get_freq() {
    cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null | awk '{printf "%.0f", $1/1000}'
}

get_throttle() {
    vcgencmd get_throttled 2>/dev/null | cut -d= -f2 || echo "N/A"
}

# Baseline
echo "--- Idle Baseline (10s) ---"
echo "Timestamp         | Temp(C) | Freq(MHz) | Throttled"
echo "------------------|---------|-----------|----------"
for _ in $(seq 1 2); do
    printf "%-18s | %7s | %9s | %s\n" \
        "$(date +%H:%M:%S)" "$(get_temp)" "$(get_freq)" "$(get_throttle)"
    sleep $INTERVAL
done

# Start stress test
echo ""
echo "--- Stress Test (${DURATION}s, 4 CPU workers) ---"
echo "Timestamp         | Temp(C) | Freq(MHz) | Throttled"
echo "------------------|---------|-----------|----------"

stress-ng --cpu 4 --timeout "${DURATION}s" --quiet &
STRESS_PID=$!

PEAK_TEMP=0
ELAPSED=0
while [ $ELAPSED -lt "$DURATION" ] && kill -0 $STRESS_PID 2>/dev/null; do
    TEMP=$(get_temp)
    FREQ=$(get_freq)
    THROTTLE=$(get_throttle)

    # Track peak
    if [ "$(echo "$TEMP > $PEAK_TEMP" | bc -l)" -eq 1 ]; then
        PEAK_TEMP=$TEMP
    fi

    printf "%-18s | %7s | %9s | %s\n" \
        "$(date +%H:%M:%S)" "$TEMP" "$FREQ" "$THROTTLE"

    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

wait $STRESS_PID 2>/dev/null || true

# Cooldown
echo ""
echo "--- Cooldown (30s) ---"
echo "Timestamp         | Temp(C) | Freq(MHz) | Throttled"
echo "------------------|---------|-----------|----------"
for _ in $(seq 1 6); do
    printf "%-18s | %7s | %9s | %s\n" \
        "$(date +%H:%M:%S)" "$(get_temp)" "$(get_freq)" "$(get_throttle)"
    sleep $INTERVAL
done

echo ""
echo "=== Results ==="
echo "Peak temperature: ${PEAK_TEMP}C"
echo "Final temperature: $(get_temp)C"
echo "Throttle status: $(get_throttle)"
echo ""
echo "Throttle flags reference:"
echo "  0x0     = no throttling"
echo "  0x50000 = throttling occurred (past)"
echo "  0x50005 = currently throttled"

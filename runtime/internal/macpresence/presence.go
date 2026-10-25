// Package macpresence decides whether the Mac worker is awake on the LAN.
//
// The Mac worker is a MacBook on battery and Wi-Fi. Sleep and low-battery
// hibernation are its normal states, not faults, yet every Mac-dependent check
// (NFS reads, offload heartbeats, the CI runner supervisor) used to page on its
// own each time the lid closed. This package produces the one root-cause
// signal those alerts are inhibited by: reachable, and since when (so alerts
// also stay quiet while the Mac settles after waking).
package macpresence

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"strings"
	"syscall"
	"time"

	"github.com/aemreusta/raspi-homelab/runtime/internal/atomicfile"
)

// State persists between one-minute probe runs.
type State struct {
	LastSeen            int64 `json:"last_seen"`
	ReachableSince      int64 `json:"reachable_since"`
	ConsecutiveFailures int   `json:"consecutive_failures"`
	LastProbe           int64 `json:"last_probe"`
	Reachable           bool  `json:"reachable"`
}

// LoadState parses persisted state. Missing state is a valid fresh start;
// anything unparseable or self-contradictory is rejected so the caller resets
// and reports state_valid=0 instead of crashing or trusting garbage.
func LoadState(data []byte) (State, bool) {
	if len(bytes.TrimSpace(data)) == 0 {
		return State{}, true
	}
	var s State
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&s); err != nil {
		return State{}, false
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return State{}, false
	}
	if s.LastSeen < 0 || s.ReachableSince < 0 || s.LastProbe < 0 || s.ConsecutiveFailures < 0 {
		return State{}, false
	}
	if s.Reachable != (s.ReachableSince > 0) {
		return State{}, false
	}
	return s, true
}

// Decide folds one probe result into the state. A reachable Mac is declared
// away only after `threshold` consecutive failed probes, so a dropped packet
// neither pages nor restarts the post-wake settle window.
func Decide(prev State, present bool, now int64, threshold int) State {
	s := prev
	s.LastProbe = now
	if present {
		s.ConsecutiveFailures = 0
		s.LastSeen = now
		if !s.Reachable {
			s.Reachable = true
			s.ReachableSince = now
		}
		return s
	}
	s.ConsecutiveFailures++
	if s.ConsecutiveFailures >= max(threshold, 1) {
		s.Reachable = false
		s.ReachableSince = 0
	}
	return s
}

// Render emits node_exporter textfile series.
func Render(s State, valid bool) string {
	var b strings.Builder
	gauge := func(name, help string, value int64) {
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s gauge\n%s %d\n", name, help, name, name, value)
	}
	gauge("mac_worker_reachable", "1 if the Mac worker answered on the LAN (debounced), else 0.", boolInt(s.Reachable))
	gauge("mac_worker_last_seen_timestamp_seconds", "Unix time the Mac worker last answered a probe.", s.LastSeen)
	gauge("mac_worker_reachable_since_timestamp_seconds", "Start of the current reachable streak; 0 while away.", s.ReachableSince)
	gauge("mac_worker_probe_timestamp_seconds", "Unix time of the last presence probe (collector liveness).", s.LastProbe)
	gauge("mac_worker_state_valid", "0 if persisted presence state was corrupt and had to be reset.", boolInt(valid))
	return b.String()
}

// DialFunc matches net.DialTimeout; injectable for tests.
type DialFunc func(network, addr string, timeout time.Duration) (net.Conn, error)

// Probe reports whether any target proves the host is awake. A completed
// connection or an explicit refusal (RST) both prove it; only timeouts and
// unreachable routes count as absent. Refusal matters: the probed service
// (e.g. an OrbStack container) may be down while the Mac itself is awake.
func Probe(targets []string, timeout time.Duration, dial DialFunc) bool {
	if dial == nil {
		dial = net.DialTimeout
	}
	for _, target := range targets {
		conn, err := dial("tcp", target, timeout)
		if err == nil {
			conn.Close()
			return true
		}
		if errors.Is(err, syscall.ECONNREFUSED) {
			return true
		}
	}
	return false
}

func boolInt(v bool) int64 {
	if v {
		return 1
	}
	return 0
}

// Config drives one collector run.
type Config struct {
	Targets   []string
	StatePath string
	Textfile  string
	Threshold int
	Timeout   time.Duration
	Now       func() time.Time
	Dial      DialFunc
}

// Run probes once, folds the result into persisted state and publishes both
// files atomically. The textfile is written on every run (including corrupt
// state) so a stalled collector is distinguishable from an away Mac.
func Run(cfg Config) error {
	if len(cfg.Targets) == 0 || cfg.StatePath == "" || cfg.Textfile == "" || cfg.Timeout <= 0 {
		return errors.New("mac-presence: targets, state, textfile and a positive timeout are required")
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	raw, err := os.ReadFile(cfg.StatePath)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		raw = []byte("unreadable")
	}
	prev, valid := LoadState(raw)
	present := Probe(cfg.Targets, cfg.Timeout, cfg.Dial)
	next := Decide(prev, present, cfg.Now().Unix(), cfg.Threshold)
	data, err := json.Marshal(next)
	if err != nil {
		return err
	}
	if err := atomicfile.Write(cfg.StatePath, append(data, '\n'), 0o644); err != nil {
		return err
	}
	return atomicfile.Write(cfg.Textfile, []byte(Render(next, valid)), 0o644)
}

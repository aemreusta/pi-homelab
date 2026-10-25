// Package watchdog preserves the legacy unhealthy-poll/restart budget while
// making failed scans, unsuccessful restarts and exhausted budgets durable.
package watchdog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/aemreusta/raspi-homelab/runtime/internal/atomicfile"
)

type State struct {
	Consecutive int64 `json:"consecutive"`
	Restarts    int64 `json:"restarts_24h"`
	LastRestart int64 `json:"last_restart"`
	LastAlert   int64 `json:"last_alert"`
}

type Plan struct {
	State  State
	Action string
	Notify bool
}

func Decide(s State, unhealthy bool, now, threshold, budget, cooldown int64) Plan {
	if !unhealthy {
		s.Consecutive, s.LastAlert = 0, 0
	}
	if s.Restarts > 0 && now-s.LastRestart >= 86400 {
		s.Restarts = 0
	}
	p := Plan{State: s, Action: "none"}
	if !unhealthy {
		return p
	}
	p.State.Consecutive++
	switch {
	case p.State.Consecutive < threshold:
		p.Action = "wait"
	case p.State.Restarts < budget:
		p.Action = "restart"
	default:
		p.Action = "budget"
		p.Notify = now-p.State.LastAlert >= cooldown
		if p.Notify {
			p.State.LastAlert = now
		}
	}
	return p
}

func ParseState(data []byte) (State, error) {
	parts := strings.Fields(string(data))
	if len(parts) < 3 || len(parts) > 4 {
		return State{}, errors.New("watchdog: malformed legacy state")
	}
	var n [4]int64
	for i, p := range parts {
		v, e := strconv.ParseInt(p, 10, 64)
		if e != nil || v < 0 || v > 1<<53 {
			return State{}, errors.New("watchdog: invalid state value")
		}
		n[i] = v
	}
	return State{n[0], n[1], n[2], n[3]}, nil
}

func (s State) bytes() []byte {
	return fmt.Appendf(nil, "%d\n%d\n%d\n%d\n", s.Consecutive, s.Restarts, s.LastRestart, s.LastAlert)
}

type Config struct {
	Docker, NotifyHelper, StateDir, StateFile, Textfile, Log string
	Threshold, MaxRestarts, Cooldown                         int64
}

type Container struct {
	State         State `json:"state"`
	Unhealthy     bool  `json:"unhealthy"`
	RestartFailed bool  `json:"restart_failed"`
}
type Result struct {
	LastRun              int64                `json:"last_run"`
	Success              bool                 `json:"success"`
	DockerReadOK         bool                 `json:"docker_read_ok"`
	Attempts             uint64               `json:"restart_attempts_total"`
	Restarts             uint64               `json:"restarts_total"`
	Failures             uint64               `json:"restart_failures_total"`
	Notifications        uint64               `json:"notification_attempts_total"`
	NotificationFailures uint64               `json:"notification_failures_total"`
	Containers           map[string]Container `json:"containers"`
}

type Runner func(context.Context, string, ...string) ([]byte, error)

// Docker output and notification bodies are never echoed into diagnostics.
// A capped buffer also bounds a malfunctioning command's memory use.
// Embedding bytes.Buffer would promote ReadFrom and bypass Write in io.Copy.
type cappedBuffer struct{ buffer bytes.Buffer }

func (b *cappedBuffer) Len() int { return b.buffer.Len() }

func (b *cappedBuffer) Write(p []byte) (int, error) {
	if len(p) > (1<<20)-b.Len() {
		return 0, errors.New("watchdog: command output too large")
	}
	return b.buffer.Write(p)
}
func command(ctx context.Context, path string, args ...string) ([]byte, error) {
	c := exec.CommandContext(ctx, path, args...)
	c.WaitDelay = 200 * time.Millisecond
	var out cappedBuffer
	c.Stdout = &out
	err := c.Run()
	return out.buffer.Bytes(), err
}

var namePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`)

type observation struct{ State, Health string }

func inventory(data []byte) ([]string, map[string]observation, error) {
	if len(data) > 1<<20 {
		return nil, nil, errors.New("watchdog: inventory too large")
	}
	rows := map[string]observation{}
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		if line == "" {
			continue
		}
		p := strings.Split(line, "|")
		if len(p) != 3 || !namePattern.MatchString(p[0]) {
			return nil, nil, errors.New("watchdog: invalid inventory row")
		}
		if _, ok := rows[p[0]]; ok {
			return nil, nil, errors.New("watchdog: duplicate inventory row")
		}
		switch p[1] {
		case "created", "running", "paused", "restarting", "removing", "exited", "dead":
		default:
			return nil, nil, errors.New("watchdog: invalid container state")
		}
		switch p[2] {
		case "", "none", "healthy", "unhealthy", "starting":
		default:
			return nil, nil, errors.New("watchdog: invalid health state")
		}
		rows[p[0]] = observation{p[1], p[2]}
	}
	if len(rows) > 4096 {
		return nil, nil, errors.New("watchdog: too many containers")
	}
	names := make([]string, 0, len(rows))
	for n := range rows {
		names = append(names, n)
	}
	sort.Strings(names)
	return names, rows, nil
}
func (o observation) eligibleUnhealthy() bool {
	return o.Health == "unhealthy" && (o.State == "running" || o.State == "paused" || o.State == "restarting")
}

func names(data []byte) ([]string, error) {
	if len(data) > 1<<20 {
		return nil, errors.New("watchdog: inventory too large")
	}
	list := strings.Fields(string(data))
	seen := map[string]bool{}
	if len(list) > 4096 {
		return nil, errors.New("watchdog: too many containers")
	}
	for _, n := range list {
		if !namePattern.MatchString(n) || seen[n] {
			return nil, errors.New("watchdog: invalid inventory")
		}
		seen[n] = true
	}
	sort.Strings(list)
	return list, nil
}

func bounded(ctx context.Context, d time.Duration, run Runner, path string, args ...string) ([]byte, error) {
	c, cancel := context.WithTimeout(ctx, d)
	defer cancel()
	return run(c, path, args...)
}

func readFile(path string) ([]byte, error) {
	s, e := os.Lstat(path)
	if e != nil {
		return nil, e
	}
	if !s.Mode().IsRegular() || s.Size() > 1<<20 {
		return nil, errors.New("watchdog: invalid state file")
	}
	return os.ReadFile(path)
}

func saveLegacy(cfg Config, name string, s State) error {
	return atomicfile.Write(filepath.Join(cfg.StateDir, "watchdog-"+name+".state"), s.bytes(), 0600)
}

func publish(cfg Config, r Result) error {
	b, e := json.Marshal(r)
	if e != nil {
		return e
	}
	if e = atomicfile.Write(cfg.StateFile, append(b, '\n'), 0600); e != nil {
		return e
	}
	return atomicfile.Write(cfg.Textfile, []byte(Metrics(r, cfg.MaxRestarts)), 0644)
}

func Metrics(r Result, budget int64) string {
	flag := func(v bool) int {
		if v {
			return 1
		}
		return 0
	}
	var b strings.Builder
	write := func(name, kind, help string, value any) {
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s %s\n%s %v\n", name, help, name, kind, name, value)
	}
	write("watchdog_last_run_timestamp_seconds", "gauge", "Start timestamp of the last watchdog scan.", r.LastRun)
	write("watchdog_success", "gauge", "Scan and state publication completed.", flag(r.Success))
	write("watchdog_docker_read_success", "gauge", "Both Docker inventories were read and validated.", flag(r.DockerReadOK))
	write("watchdog_restart_attempts_total", "counter", "Restart intentions persisted before command execution.", r.Attempts)
	write("watchdog_restarts_total", "counter", "Successful Docker restart commands since Go cutover.", r.Restarts)
	write("watchdog_restart_failures_total", "counter", "Failed or timed out Docker restart commands since Go cutover.", r.Failures)
	write("watchdog_notification_attempts_total", "counter", "Notification helper invocations since Go cutover.", r.Notifications)
	write("watchdog_notification_failures_total", "counter", "Unsuccessful notification helper invocations since Go cutover.", r.NotificationFailures)
	for _, metric := range []string{"unhealthy", "consecutive_unhealthy", "restarts_24h", "restart_failed", "budget_exhausted"} {
		fmt.Fprintf(&b, "# HELP watchdog_%s Current container watchdog observation.\n# TYPE watchdog_%s gauge\n", metric, metric)
	}
	keys := make([]string, 0, len(r.Containers))
	for n := range r.Containers {
		keys = append(keys, n)
	}
	sort.Strings(keys)
	for _, n := range keys {
		c := r.Containers[n]
		fmt.Fprintf(&b, "watchdog_unhealthy{container=%q} %d\nwatchdog_consecutive_unhealthy{container=%q} %d\nwatchdog_restarts_24h{container=%q} %d\nwatchdog_restart_failed{container=%q} %d\nwatchdog_budget_exhausted{container=%q} %d\n", n, flag(c.Unhealthy), n, c.State.Consecutive, n, c.State.Restarts, n, flag(c.RestartFailed), n, flag(c.Unhealthy && c.State.Restarts >= budget))
	}
	return b.String()
}

func Run(ctx context.Context, cfg Config) error { return Execute(ctx, cfg, time.Now().Unix(), command) }

// Execute validates both inventories and every live state before restarting.
// An unreadable Docker daemon never means an empty/healthy inventory or GC.
func Execute(ctx context.Context, cfg Config, now int64, run Runner) error {
	if cfg.Threshold < 1 || cfg.MaxRestarts < 1 || cfg.Cooldown < 1 || now < 1 {
		return errors.New("watchdog: invalid policy")
	}
	for _, p := range []string{cfg.Docker, cfg.NotifyHelper, cfg.StateDir, cfg.StateFile, cfg.Textfile, cfg.Log} {
		if !filepath.IsAbs(p) {
			return errors.New("watchdog: absolute paths required")
		}
	}
	if cfg.StateFile == cfg.Textfile || cfg.Log == cfg.StateFile || cfg.Log == cfg.Textfile {
		return errors.New("watchdog: distinct output paths required")
	}
	r := Result{Containers: map[string]Container{}}
	if b, e := readFile(cfg.StateFile); e == nil {
		if !bytes.HasPrefix(bytes.TrimSpace(b), []byte("{")) || json.Unmarshal(b, &r) != nil || r.Containers == nil || r.LastRun < 1 {
			return errors.New("watchdog: malformed counter ledger")
		}
		for n := range r.Containers {
			if !namePattern.MatchString(n) {
				return errors.New("watchdog: invalid ledger name")
			}
		}
	} else if !errors.Is(e, os.ErrNotExist) {
		return e
	}
	r.LastRun, r.Success, r.DockerReadOK = now, false, false
	f, e := os.OpenFile(cfg.Log, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0640)
	if e != nil {
		return e
	}
	defer f.Close()
	log := func(format string, a ...any) { fmt.Fprintf(f, format+"\n", a...) }
	log("--- %s scan ---", time.Unix(now, 0).Format(time.RFC3339))
	fail := func(err error) error {
		log("%d SCAN FAILED", now)
		if e := publish(cfg, r); e != nil {
			return e
		}
		return err
	}
	u, e := bounded(ctx, 10*time.Second, run, cfg.Docker, "ps", "--filter", "health=unhealthy", "--format", "{{.Names}}")
	if e != nil {
		return fail(errors.New("watchdog: unhealthy inventory unavailable"))
	}
	a, e := bounded(ctx, 10*time.Second, run, cfg.Docker, "ps", "-a", "--format", "{{.Names}}|{{.State}}|{{.HealthStatus}}")
	if e != nil {
		return fail(errors.New("watchdog: full inventory unavailable"))
	}
	unhealthy, e := names(u)
	if e != nil {
		return fail(e)
	}
	all, observations, e := inventory(a)
	if e != nil {
		return fail(e)
	}
	bad, present := map[string]bool{}, map[string]bool{}
	for _, n := range all {
		present[n] = true
	}
	for _, n := range unhealthy {
		if !present[n] || !observations[n].eligibleUnhealthy() {
			return fail(errors.New("watchdog: inventories changed during scan"))
		}
		bad[n] = true
	}
	for _, n := range all {
		if observations[n].eligibleUnhealthy() != bad[n] {
			return fail(errors.New("watchdog: inventories changed during scan"))
		}
	}
	states, exists := map[string]State{}, map[string]bool{}
	for _, n := range all {
		b, e := readFile(filepath.Join(cfg.StateDir, "watchdog-"+n+".state"))
		if errors.Is(e, os.ErrNotExist) {
			continue
		}
		if e != nil {
			return fail(e)
		}
		s, e := ParseState(b)
		if e != nil {
			return fail(e)
		}
		states[n], exists[n] = s, true
	}
	r.DockerReadOK = true
	observed := map[string]Container{}
	for _, n := range all {
		p := Decide(states[n], bad[n], now, cfg.Threshold, cfg.MaxRestarts, cfg.Cooldown)
		ready := observations[n].State == "running" && observations[n].Health == "healthy"
		observed[n] = Container{State: p.State, Unhealthy: bad[n], RestartFailed: !ready && r.Containers[n].RestartFailed}
	}
	r.Containers = observed
	if e = publish(cfg, r); e != nil {
		return e
	}
	notify := func(name, title, priority, tags, body string) error {
		r.Notifications++
		if e := publish(cfg, r); e != nil {
			return e
		}
		_, e := bounded(ctx, 15*time.Second, run, cfg.NotifyHelper, title, priority, tags, body)
		if e != nil {
			r.NotificationFailures++
			log("%d %s NOTIFY FAILED", now, name)
		}
		return publish(cfg, r)
	}
	for _, n := range all {
		p := Decide(states[n], bad[n], now, cfg.Threshold, cfg.MaxRestarts, cfg.Cooldown)
		c := r.Containers[n]
		if exists[n] || bad[n] {
			if e = saveLegacy(cfg, n, c.State); e != nil {
				return fail(e)
			}
		}
		if bad[n] {
			log("%d %s unhealthy (cons=%d rc=%d)", now, n, c.State.Consecutive, c.State.Restarts)
		}
		switch p.Action {
		case "budget":
			log("%d %s OVER-BUDGET (rc=%d)", now, n, c.State.Restarts)
			if p.Notify {
				e = notify(n, "Watchdog: "+n+" stuck unhealthy", "urgent", "rotating_light,docker", fmt.Sprintf("Restarted %d times in 24 h; unhealthy for %d consecutive polls. Restart budget exhausted. Inspect health logs, network endpoints and mounts. Repeat suppressed for %d hours.", c.State.Restarts, c.State.Consecutive, cfg.Cooldown/3600))
			}
		case "restart":
			r.Attempts++
			c.RestartFailed = true
			r.Containers[n] = c
			if e = publish(cfg, r); e != nil {
				return e
			}
			_, restartErr := bounded(ctx, 45*time.Second, run, cfg.Docker, "restart", n)
			if restartErr == nil {
				r.Restarts++
				c.RestartFailed = false
				c.State.Consecutive = 0
				c.State.Restarts++
				c.State.LastRestart = now
				log("%d %s RESTARTED (rc=%d/%d)", now, n, c.State.Restarts, cfg.MaxRestarts)
			} else {
				r.Failures++
				log("%d %s RESTART FAILED", now, n)
			}
			// Successful restart is a command result, not readiness proof.
			r.Containers[n] = c
			if e = saveLegacy(cfg, n, c.State); e != nil {
				return fail(e)
			}
			if e = publish(cfg, r); e != nil {
				return e
			}
			if restartErr == nil {
				e = notify(n, "Watchdog: restarted "+n, "default", "wrench,docker", fmt.Sprintf("Unhealthy %d consecutive polls; Docker restart succeeded. Budget %d/%d in 24 h. Readiness is checked on the next poll.", p.State.Consecutive, c.State.Restarts, cfg.MaxRestarts))
			} else if now-c.State.LastAlert >= cfg.Cooldown {
				// Unlike the legacy failure branch, failed commands also respect
				// the existing urgent cooldown. Durable metrics retain the fault.
				c.State.LastAlert = now
				r.Containers[n] = c
				if e = saveLegacy(cfg, n, c.State); e != nil {
					return fail(e)
				}
				e = notify(n, "Watchdog: restart failed for "+n, "urgent", "warning,docker", fmt.Sprintf("Unhealthy %d consecutive polls; Docker restart failed or exceeded 45 s. Inspect Docker and container state.", c.State.Consecutive))
			}
		}
		if e != nil {
			return e
		}
	}
	files, e := filepath.Glob(filepath.Join(cfg.StateDir, "watchdog-*.state"))
	if e != nil {
		return fail(e)
	}
	for _, file := range files {
		n := strings.TrimSuffix(strings.TrimPrefix(filepath.Base(file), "watchdog-"), ".state")
		if namePattern.MatchString(n) && !present[n] {
			if e = os.Remove(file); e != nil {
				return fail(e)
			}
			log("%d %s GC (container absent)", now, n)
		}
	}
	r.Success = true
	return publish(cfg, r)
}

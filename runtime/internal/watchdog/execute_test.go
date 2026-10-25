package watchdog

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func fixture(t *testing.T) Config {
	t.Helper()
	d := t.TempDir()
	return Config{Docker: "/fixture/docker", NotifyHelper: "/fixture/notify", StateDir: d, StateFile: filepath.Join(d, "ledger.json"), Textfile: filepath.Join(d, "watchdog.prom"), Log: filepath.Join(d, "watchdog.log"), Threshold: 3, MaxRestarts: 5, Cooldown: 21600}
}
func result(t *testing.T, c Config) Result {
	t.Helper()
	b, e := os.ReadFile(c.StateFile)
	if e != nil {
		t.Fatal(e)
	}
	var r Result
	if e = json.Unmarshal(b, &r); e != nil {
		t.Fatal(e)
	}
	return r
}

func fixtureList(args []string, names string, bad bool) []byte {
	if args[1] != "-a" {
		if bad {
			return []byte(names)
		}
		return nil
	}
	health := "healthy"
	if bad {
		health = "unhealthy"
	}
	var out strings.Builder
	for _, n := range strings.Fields(names) {
		out.WriteString(n + "|running|" + health + "\n")
	}
	return []byte(out.String())
}

func TestFailedRestartPersistsAndCoolsDown(t *testing.T) {
	c := fixture(t)
	if e := saveLegacy(c, "service", State{Consecutive: 2}); e != nil {
		t.Fatal(e)
	}
	bad := true
	notifications := 0
	run := func(ctx context.Context, path string, args ...string) ([]byte, error) {
		deadline, ok := ctx.Deadline()
		if !ok || time.Until(deadline) > 46*time.Second {
			t.Fatal("unbounded command")
		}
		if path == c.NotifyHelper {
			notifications++
			return nil, errors.New("delivery failed")
		}
		if args[0] == "restart" {
			r := result(t, c)
			if r.Attempts == 0 || !r.Containers["service"].RestartFailed {
				t.Fatal("intention not persisted before restart")
			}
			return nil, errors.New("restart failed")
		}
		if args[1] == "--filter" && !bad {
			return nil, nil
		}
		return fixtureList(args, "service\n", bad), nil
	}
	for _, now := range []int64{200000, 200300} {
		if e := Execute(context.Background(), c, now, run); e != nil {
			t.Fatal(e)
		}
	}
	r := result(t, c)
	if r.Attempts != 2 || r.Failures != 2 || r.Restarts != 0 || notifications != 1 || r.NotificationFailures != 1 || !r.Containers["service"].RestartFailed {
		t.Fatalf("bad durable result %+v notifications=%d", r, notifications)
	}
	bad = false
	if e := Execute(context.Background(), c, 200600, run); e != nil {
		t.Fatal(e)
	}
	r = result(t, c)
	if r.Containers["service"].RestartFailed || r.Failures != 2 {
		t.Fatalf("recovery lost counters or retained active failure %+v", r)
	}
	info, e := os.Stat(c.StateFile)
	if e != nil || info.Mode().Perm() != 0600 {
		t.Fatal("ledger not private")
	}
}

func TestBudgetRemainsVisibleDuringSuppression(t *testing.T) {
	c := fixture(t)
	now := int64(200000)
	if e := saveLegacy(c, "service", State{Consecutive: 8, Restarts: 5, LastRestart: now - 100, LastAlert: now - 20}); e != nil {
		t.Fatal(e)
	}
	run := func(_ context.Context, path string, args ...string) ([]byte, error) {
		if path == c.NotifyHelper || args[0] == "restart" {
			t.Fatal("budget/cooldown bypass")
		}
		return fixtureList(args, "service\n", true), nil
	}
	if e := Execute(context.Background(), c, now, run); e != nil {
		t.Fatal(e)
	}
	b, _ := os.ReadFile(c.Textfile)
	if !strings.Contains(string(b), "watchdog_budget_exhausted{container=\"service\"} 1\n") || result(t, c).Attempts != 0 {
		t.Fatal(string(b))
	}
}

func TestInventoryFailureNeverClearsStateOrGarbageCollects(t *testing.T) {
	for _, failure := range []string{"first", "second", "malformed", "racy"} {
		t.Run(failure, func(t *testing.T) {
			c := fixture(t)
			saveLegacy(c, "orphan", State{Consecutive: 9})
			calls := 0
			run := func(_ context.Context, path string, args ...string) ([]byte, error) {
				calls++
				if path == c.NotifyHelper || args[0] == "restart" {
					t.Fatal("side effect on bad inventory")
				}
				if failure == "first" || failure == "second" && calls == 2 {
					return nil, errors.New("Docker unavailable")
				}
				if failure == "malformed" {
					return []byte("../../outside\n"), nil
				}
				if failure == "racy" && calls == 1 {
					return []byte("vanished\n"), nil
				}
				return nil, nil
			}
			if e := Execute(context.Background(), c, 200000, run); e == nil {
				t.Fatal("expected failure")
			}
			if _, e := os.Stat(filepath.Join(c.StateDir, "watchdog-orphan.state")); e != nil {
				t.Fatal("orphan removed without valid inventory")
			}
			if r := result(t, c); r.Success || r.DockerReadOK {
				t.Fatalf("false success %+v", r)
			}
		})
	}
}

func TestMalformedLiveStatePreventsAllRestarts(t *testing.T) {
	c := fixture(t)
	saveLegacy(c, "a", State{Consecutive: 2})
	os.WriteFile(filepath.Join(c.StateDir, "watchdog-b.state"), []byte("bad\n"), 0600)
	run := func(_ context.Context, _ string, args ...string) ([]byte, error) {
		if args[0] == "restart" {
			t.Fatal("restart preceded complete state validation")
		}
		return fixtureList(args, "a\nb\n", true), nil
	}
	if e := Execute(context.Background(), c, 200000, run); e == nil {
		t.Fatal("accepted bad state")
	}
}

func TestPublicationFailurePreventsRestart(t *testing.T) {
	c := fixture(t)
	c.Textfile = filepath.Join(c.StateDir, "missing-directory", "watchdog.prom")
	saveLegacy(c, "service", State{Consecutive: 2})
	run := func(_ context.Context, path string, args ...string) ([]byte, error) {
		if path == c.NotifyHelper || args[0] == "restart" {
			t.Fatal("side effect before successful publication")
		}
		return fixtureList(args, "service\n", true), nil
	}
	if e := Execute(context.Background(), c, 200000, run); e == nil {
		t.Fatal("ignored publication failure")
	}
	if result(t, c).Attempts != 0 {
		t.Fatal("attempted restart without metrics")
	}
}

func TestInvalidLedgerIsNeverSilentlyReset(t *testing.T) {
	for _, data := range []string{"null", "{}", "{\"last_run\":100,\"containers\":null}"} {
		c := fixture(t)
		os.WriteFile(c.StateFile, []byte(data), 0600)
		run := func(context.Context, string, ...string) ([]byte, error) {
			t.Fatal("read or action before counter validation")
			return nil, nil
		}
		if e := Execute(context.Background(), c, 200000, run); e == nil {
			t.Fatal("silently reset invalid ledger")
		}
		actual, _ := os.ReadFile(c.StateFile)
		if string(actual) != data {
			t.Fatal("invalid ledger was overwritten")
		}
	}
}

func TestStoppedOrStartingContainerDoesNotClearRestartFailure(t *testing.T) {
	c := fixture(t)
	saveLegacy(c, "service", State{Consecutive: 2})
	state, health := "running", "unhealthy"
	run := func(_ context.Context, path string, args ...string) ([]byte, error) {
		if path == c.NotifyHelper {
			return nil, nil
		}
		if args[0] == "restart" {
			return nil, errors.New("restart failed")
		}
		if args[1] == "--filter" {
			if health == "unhealthy" {
				return []byte("service\n"), nil
			}
			return nil, nil
		}
		return []byte("service|" + state + "|" + health + "\n"), nil
	}
	if e := Execute(context.Background(), c, 200000, run); e != nil {
		t.Fatal(e)
	}
	for _, s := range []struct {
		state, health string
		now           int64
	}{{"exited", "none", 200300}, {"running", "starting", 200600}} {
		state, health = s.state, s.health
		if e := Execute(context.Background(), c, s.now, run); e != nil {
			t.Fatal(e)
		}
		if !result(t, c).Containers["service"].RestartFailed {
			t.Fatal("unproven recovery cleared failed restart")
		}
	}
	state, health = "running", "healthy"
	if e := Execute(context.Background(), c, 200900, run); e != nil {
		t.Fatal(e)
	}
	r := result(t, c)
	if r.Containers["service"].RestartFailed || r.Failures != 1 {
		t.Fatal("healthy recovery lost durable failure history")
	}
}

func TestActualCommandHonorsContextAndOutputLimit(t *testing.T) {
	d := t.TempDir()
	sleep := filepath.Join(d, "sleep")
	if e := os.WriteFile(sleep, []byte("#!/bin/sh\nexec sleep 10\n"), 0700); e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	started := time.Now()
	if _, e := command(ctx, sleep); e == nil {
		t.Fatal("expected command cancellation")
	}
	if time.Since(started) > time.Second {
		t.Fatal("context did not bound command")
	}
	var b cappedBuffer
	if _, e := b.Write(make([]byte, (1<<20)+1)); e == nil || b.Len() != 0 {
		t.Fatal("unbounded output")
	}
}

func TestSuccessfulRestartIsNotReadinessAndCountersSurviveNextPoll(t *testing.T) {
	c := fixture(t)
	saveLegacy(c, "service", State{Consecutive: 2})
	bad := true
	run := func(_ context.Context, path string, args ...string) ([]byte, error) {
		if args[0] == "restart" || path == c.NotifyHelper {
			return nil, nil
		}
		if args[1] == "--filter" && !bad {
			return nil, nil
		}
		return fixtureList(args, "service\n", bad), nil
	}
	if e := Execute(context.Background(), c, 200000, run); e != nil {
		t.Fatal(e)
	}
	r := result(t, c)
	s := r.Containers["service"]
	if r.Restarts != 1 || !s.Unhealthy || s.RestartFailed || s.State.Consecutive != 0 || s.State.Restarts != 1 {
		t.Fatalf("wrong command result %+v", r)
	}
	bad = false
	if e := Execute(context.Background(), c, 200300, run); e != nil {
		t.Fatal(e)
	}
	if r = result(t, c); r.Restarts != 1 || r.Containers["service"].Unhealthy {
		t.Fatal("lost restart counter or no observed recovery")
	}
}

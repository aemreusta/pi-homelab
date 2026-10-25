package macpresence

import (
	"errors"
	"net"
	"os"
	"strings"
	"syscall"
	"testing"
	"time"
)

const threshold = 3

func TestFreshStateBecomesReachableAndStartsSettleWindow(t *testing.T) {
	s := Decide(State{}, true, 1000, threshold)
	if !s.Reachable || s.ReachableSince != 1000 || s.LastSeen != 1000 || s.LastProbe != 1000 {
		t.Fatalf("unexpected state %+v", s)
	}
}

func TestReachableStreakKeepsItsStart(t *testing.T) {
	s := Decide(State{}, true, 1000, threshold)
	s = Decide(s, true, 1060, threshold)
	if s.ReachableSince != 1000 || s.LastSeen != 1060 {
		t.Fatalf("streak start moved: %+v", s)
	}
}

func TestSingleFailedProbesAreDebounced(t *testing.T) {
	s := Decide(State{}, true, 1000, threshold)
	for i, now := range []int64{1060, 1120} {
		s = Decide(s, false, now, threshold)
		if !s.Reachable || s.ConsecutiveFailures != i+1 || s.ReachableSince != 1000 {
			t.Fatalf("failure %d flipped state early: %+v", i+1, s)
		}
	}
	s = Decide(s, false, 1180, threshold)
	if s.Reachable || s.ReachableSince != 0 || s.LastSeen != 1000 {
		t.Fatalf("third failure must mark away: %+v", s)
	}
}

func TestBlipDoesNotRestartSettleWindow(t *testing.T) {
	s := Decide(State{}, true, 1000, threshold)
	s = Decide(s, false, 1060, threshold)
	s = Decide(s, true, 1120, threshold)
	if s.ReachableSince != 1000 || s.ConsecutiveFailures != 0 {
		t.Fatalf("a debounced blip must not look like a wake: %+v", s)
	}
}

func TestReturnAfterAwayStartsNewStreak(t *testing.T) {
	s := State{Reachable: false, LastSeen: 500, ConsecutiveFailures: 9, LastProbe: 900}
	s = Decide(s, true, 1000, threshold)
	if !s.Reachable || s.ReachableSince != 1000 || s.ConsecutiveFailures != 0 {
		t.Fatalf("wake must open a new streak: %+v", s)
	}
}

func TestUnreachableFromFreshStaysAway(t *testing.T) {
	s := Decide(State{}, false, 1000, threshold)
	if s.Reachable || s.ReachableSince != 0 || s.LastSeen != 0 {
		t.Fatalf("unexpected %+v", s)
	}
}

func TestLoadStateRejectsCorruptInput(t *testing.T) {
	for _, in := range []string{"{", "[]", `{"last_seen":-1}`, `{"reachable_since":5,"reachable":false}`, "{} {}"} {
		if _, ok := LoadState([]byte(in)); ok {
			t.Fatalf("accepted %q", in)
		}
	}
	s, ok := LoadState(nil)
	if !ok || s != (State{}) {
		t.Fatalf("missing state must be a valid fresh state: %+v %v", s, ok)
	}
	s, ok = LoadState([]byte(`{"last_seen":10,"reachable_since":5,"consecutive_failures":0,"last_probe":10,"reachable":true}`))
	if !ok || s.ReachableSince != 5 || !s.Reachable {
		t.Fatalf("round trip failed: %+v %v", s, ok)
	}
}

func TestRenderExposesAllSeries(t *testing.T) {
	text := Render(State{Reachable: true, LastSeen: 1060, ReachableSince: 1000, LastProbe: 1060}, true)
	for _, want := range []string{
		"mac_worker_reachable 1\n",
		"mac_worker_last_seen_timestamp_seconds 1060\n",
		"mac_worker_reachable_since_timestamp_seconds 1000\n",
		"mac_worker_probe_timestamp_seconds 1060\n",
		"mac_worker_state_valid 1\n",
		"# TYPE mac_worker_reachable gauge\n",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing %q in\n%s", want, text)
		}
	}
	if !strings.Contains(Render(State{}, false), "mac_worker_state_valid 0\n") {
		t.Fatal("invalid state must be reported")
	}
}

func TestProbeCountsRefusedAsPresent(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	l.Close() // nothing listens now: the kernel answers with RST
	if !Probe([]string{addr}, time.Second, nil) {
		t.Fatal("connection refused proves the host is awake")
	}
}

func TestProbeSucceedsOnListener(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	if !Probe([]string{l.Addr().String()}, time.Second, nil) {
		t.Fatal("open port must be present")
	}
}

func TestProbeTimeoutsAreAbsent(t *testing.T) {
	calls := 0
	dial := func(network, addr string, timeout time.Duration) (net.Conn, error) {
		calls++
		return nil, &net.OpError{Op: "dial", Err: errTimeout{}}
	}
	if Probe([]string{"a:1", "b:2"}, time.Second, dial) {
		t.Fatal("timeouts must count as absent")
	}
	if calls != 2 {
		t.Fatalf("every target must be tried, got %d", calls)
	}
}

func TestProbeAnyTargetSuffices(t *testing.T) {
	dial := func(network, addr string, timeout time.Duration) (net.Conn, error) {
		if addr == "b:2" {
			return nil, &net.OpError{Op: "dial", Err: syscall.ECONNREFUSED}
		}
		return nil, errors.New("no route to host")
	}
	if !Probe([]string{"a:1", "b:2"}, time.Second, dial) {
		t.Fatal("one refusing target proves presence")
	}
}

type errTimeout struct{}

func (errTimeout) Error() string   { return "i/o timeout" }
func (errTimeout) Timeout() bool   { return true }
func (errTimeout) Temporary() bool { return true }

func TestRunPersistsStateAndTextfile(t *testing.T) {
	dir := t.TempDir()
	cfg := Config{
		Targets:   []string{"x:1"},
		StatePath: dir + "/state.json",
		Textfile:  dir + "/mac_worker.prom",
		Threshold: threshold,
		Timeout:   time.Second,
		Now:       func() time.Time { return time.Unix(1000, 0) },
		Dial: func(string, string, time.Duration) (net.Conn, error) {
			return nil, &net.OpError{Op: "dial", Err: syscall.ECONNREFUSED}
		},
	}
	if err := Run(cfg); err != nil {
		t.Fatal(err)
	}
	text := readFile(t, cfg.Textfile)
	if !strings.Contains(text, "mac_worker_reachable 1\n") || !strings.Contains(text, "mac_worker_state_valid 1\n") {
		t.Fatalf("unexpected textfile\n%s", text)
	}
	s, ok := LoadState([]byte(readFile(t, cfg.StatePath)))
	if !ok || s.ReachableSince != 1000 {
		t.Fatalf("state not persisted: %+v %v", s, ok)
	}
}

func TestRunResetsCorruptStateAndReportsIt(t *testing.T) {
	dir := t.TempDir()
	cfg := Config{
		Targets:   []string{"x:1"},
		StatePath: dir + "/state.json",
		Textfile:  dir + "/mac_worker.prom",
		Threshold: threshold,
		Timeout:   time.Second,
		Now:       func() time.Time { return time.Unix(2000, 0) },
		Dial: func(string, string, time.Duration) (net.Conn, error) {
			return nil, &net.OpError{Op: "dial", Err: errTimeout{}}
		},
	}
	if err := os.WriteFile(cfg.StatePath, []byte("{garbage"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := Run(cfg); err != nil {
		t.Fatal(err)
	}
	text := readFile(t, cfg.Textfile)
	for _, want := range []string{"mac_worker_state_valid 0\n", "mac_worker_reachable 0\n", "mac_worker_probe_timestamp_seconds 2000\n"} {
		if !strings.Contains(text, want) {
			t.Fatalf("missing %q\n%s", want, text)
		}
	}
}

func TestRunRejectsEmptyTargets(t *testing.T) {
	if err := Run(Config{StatePath: "x", Textfile: "y", Threshold: 1, Timeout: time.Second}); err == nil {
		t.Fatal("empty target list must be a configuration error")
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

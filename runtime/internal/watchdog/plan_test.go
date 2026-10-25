package watchdog

import "testing"

func TestPlanPreservesBudgetAndCooldown(t *testing.T) {
	const now int64 = 200000
	for _, tt := range []struct {
		name   string
		state  State
		bad    bool
		want   State
		action string
		notify bool
	}{
		{"first poll", State{}, true, State{Consecutive: 1}, "wait", false},
		{"threshold", State{Consecutive: 2}, true, State{Consecutive: 3}, "restart", false},
		{"budget", State{Consecutive: 7, Restarts: 5, LastRestart: now - 100}, true, State{Consecutive: 8, Restarts: 5, LastRestart: now - 100, LastAlert: now}, "budget", true},
		{"cooldown", State{Consecutive: 7, Restarts: 5, LastRestart: now - 100, LastAlert: now - 10}, true, State{Consecutive: 8, Restarts: 5, LastRestart: now - 100, LastAlert: now - 10}, "budget", false},
		{"healthy", State{Consecutive: 7, Restarts: 5, LastRestart: now - 100, LastAlert: now - 10}, false, State{Restarts: 5, LastRestart: now - 100}, "none", false},
		{"decayed", State{Consecutive: 2, Restarts: 5, LastRestart: now - 86400}, true, State{Consecutive: 3, LastRestart: now - 86400}, "restart", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			p := Decide(tt.state, tt.bad, now, 3, 5, 21600)
			if p.State != tt.want || p.Action != tt.action || p.Notify != tt.notify {
				t.Fatalf("got %+v", p)
			}
		})
	}
}

func TestLegacyStateValidation(t *testing.T) {
	for _, text := range []string{"0\n0\n0\n", "1\n2\n30\n40\n"} {
		if _, err := ParseState([]byte(text)); err != nil {
			t.Fatal(err)
		}
	}
	for _, text := range []string{"", "1\n", "-1\n0\n0\n0\n", "bad\n0\n0\n0\n", "1\n2\n3\n4\n5\n"} {
		if _, err := ParseState([]byte(text)); err == nil {
			t.Fatalf("accepted %q", text)
		}
	}
}

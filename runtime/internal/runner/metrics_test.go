package runner

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMissingAndMalformedInputsStillEmitLiveness(t *testing.T) {
	d := t.TempDir()
	p := filepath.Join(d, "status")
	h := filepath.Join(d, "heartbeat")
	for _, s := range []string{"", `[]`, `null`, `{"repos":42}`, `{"idle_count":1} {}`} {
		if err := os.WriteFile(p, []byte(s), 0600); err != nil {
			t.Fatal(err)
		}
		out := Render(p, h, 600, 1800000000, "")
		for _, want := range []string{"gh_runner_heartbeat_stale 1\n", "gh_runner_metrics_last_run_unix_seconds 1800000000\n", "gh_runner_registered_count{repo=\"none\"} 0\n"} {
			if !strings.Contains(out, want) {
				t.Fatalf("input %q missing %q", s, want)
			}
		}
	}
}
func TestIntegerPrecisionThresholdAndLabels(t *testing.T) {
	d := t.TempDir()
	p := filepath.Join(d, "status")
	h := filepath.Join(d, "heartbeat")
	if err := os.WriteFile(p, []byte(`{"supervisor_heartbeat_ts":1000,"jobs_total":9007199254740993,"runner_version":"a\nb\\c\"d","idle_count":true}`), 0600); err != nil {
		t.Fatal(err)
	}
	out := Render(p, h, 600, 1600, "")
	for _, want := range []string{"gh_runner_heartbeat_stale 0\n", "gh_runner_jobs_total 9007199254740993\n", `{version="a\nb\\c\"d"}`, "gh_runner_idle_count 1\n"} {
		if !strings.Contains(out, want) {
			t.Fatalf("missing %q in %s", want, out)
		}
	}
	if err := os.WriteFile(h, []byte("999"), 0600); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(Render(p, h, 600, 1600, ""), "gh_runner_heartbeat_stale 1\n") {
		t.Fatal("heartbeat file did not override status")
	}
}

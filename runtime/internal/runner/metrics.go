// Package runner preserves the Python collector's metric names and semantics.
package runner

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
)

func number(v any) int64 {
	switch n := v.(type) {
	case bool:
		if n {
			return 1
		}
	case string:
		i, _ := strconv.ParseInt(strings.TrimSpace(n), 10, 64)
		return i
	case json.Number:
		if i, e := n.Int64(); e == nil {
			return i
		}
		if f, e := n.Float64(); e == nil && f < 9223372036854775808 && f >= -9223372036854775808 {
			return int64(f)
		}
	}
	return 0
}
func object(path string) map[string]any {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	var v map[string]any
	if d.Decode(&v) != nil {
		return nil
	}
	var trailing any
	if d.Decode(&trailing) != io.EOF {
		return nil
	}
	return v
}

var labelEscaper = strings.NewReplacer("\\", "\\\\", "\n", "\\n", "\"", "\\\"")

func label(v string) string {
	return labelEscaper.Replace(v)
}
func keys(m map[string]any) []string {
	k := make([]string, 0, len(m))
	for s := range m {
		k = append(k, s)
	}
	sort.Strings(k)
	return k
}

type metric struct{ name, help, key, kind string }

var statusMetrics = []metric{
	{"gh_runner_idle_count", "Idle ephemeral runner containers the supervisor believes are up.", "idle_count", "gauge"},
	{"gh_runner_orphan_containers", "Exited job containers awaiting cleanup.", "orphan_containers", "gauge"},
	{"gh_runner_last_job_exit_code", "Exit code of the most recent job container.", "last_job_exit_code", "gauge"},
	{"gh_runner_jobs_total", "Jobs completed since supervisor start.", "jobs_total", "counter"},
	{"gh_runner_jobs_failed_total", "Jobs that exited non-zero since supervisor start.", "jobs_failed_total", "counter"},
	{"gh_runner_github_api_auth_failures_total", "GitHub API 401/403 responses (PAT problem).", "github_api_auth_failures_total", "counter"},
	{"gh_runner_jit_last_success_timestamp_seconds", "Unix ts of the last successful JIT mint.", "jit_last_success_ts", "gauge"},
	{"gh_runner_github_api_rate_remaining", "GitHub REST rate-limit headroom.", "github_api_rate_remaining", "gauge"},
	{"gh_runner_image_build_timestamp_seconds", "When the runner image was built (age -> staleness).", "image_build_timestamp", "gauge"},
}

func Render(statusPath, heartbeatPath string, staleSeconds, now int64, rebuildPath string) string {
	status := object(statusPath)
	hb, _ := os.ReadFile(heartbeatPath)
	heartbeat := number(string(hb))
	if heartbeat == 0 {
		heartbeat = number(status["supervisor_heartbeat_ts"])
	}
	stale := int64(0)
	if heartbeat == 0 || now-heartbeat > staleSeconds {
		stale = 1
	}
	var out strings.Builder
	header := func(name, help, kind string) {
		fmt.Fprintf(&out, "# HELP %s %s\n# TYPE %s %s\n", name, help, name, kind)
	}
	emit := func(name, help, kind string, value int64, labels string) {
		header(name, help, kind)
		fmt.Fprintf(&out, "%s%s %d\n", name, labels, value)
	}
	emit("gh_runner_metrics_last_run_unix_seconds", "Unix time of the last Pi emitter run.", "gauge", now, "")
	emit("gh_runner_supervisor_heartbeat_timestamp_seconds", "Unix ts of the last Mac supervisor heartbeat (0 if missing).", "gauge", heartbeat, "")
	emit("gh_runner_heartbeat_stale", "1 when the supervisor heartbeat is missing or older than the threshold.", "gauge", stale, "")
	for _, m := range statusMetrics {
		emit(m.name, m.help, m.kind, number(status[m.key]), "")
	}
	version := "unknown"
	if v, ok := status["runner_version"]; ok {
		version = fmt.Sprint(v)
	}
	emit("gh_runner_version_info", "Constant 1; labels the pinned runner version.", "gauge", 1, `{version="`+label(version)+`"}`)
	rebuild := object(rebuildPath)
	for _, m := range []metric{
		{"gh_runner_rebuild_last_attempt_timestamp_seconds", "Unix ts of the last runner-image rebuild attempt (0 if never).", "last_attempt_ts", "gauge"},
		{"gh_runner_rebuild_last_success_timestamp_seconds", "Unix ts of the last SUCCESSFUL runner-image rebuild (0 if never).", "last_success_ts", "gauge"},
		{"gh_runner_rebuild_last_exit_code", "Exit code of the last rebuild attempt (non-zero => failed push/build).", "last_exit_code", "gauge"},
	} {
		emit(m.name, m.help, m.kind, number(rebuild[m.key]), "")
	}
	name := "gh_runner_jit_config_failures_total"
	header(name, "JIT config mint failures by HTTP status.", "counter")
	failures, _ := status["jit_config_failures"].(map[string]any)
	if len(failures) == 0 {
		fmt.Fprintf(&out, "%s{status=\"none\"} 0\n", name)
	} else {
		for _, key := range keys(failures) {
			fmt.Fprintf(&out, "%s{status=\"%s\"} %d\n", name, label(key), number(failures[key]))
		}
	}
	repos, _ := status["repos"].(map[string]any)
	repoKeys := keys(repos)
	for _, m := range []metric{
		{"gh_runner_registered_count", "Runners GitHub lists for the repo.", "registered", "gauge"},
		{"gh_runner_online_count", "Online runners for the repo.", "online", "gauge"},
		{"gh_runner_busy_count", "Busy runners (running a job) for the repo.", "busy", "gauge"},
		{"gh_runner_offline_count", "Offline runners (orphans pending reap) for the repo.", "offline", "gauge"},
		{"gh_runner_queued_jobs", "Queued homelab-build jobs awaiting a runner.", "queued_jobs", "gauge"},
	} {
		header(m.name, m.help, m.kind)
		if len(repos) == 0 {
			fmt.Fprintf(&out, "%s{repo=\"none\"} 0\n", m.name)
		} else {
			for _, repo := range repoKeys {
				info, _ := repos[repo].(map[string]any)
				fmt.Fprintf(&out, "%s{repo=\"%s\"} %d\n", m.name, label(repo), number(info[m.key]))
			}
		}
	}
	return out.String()
}

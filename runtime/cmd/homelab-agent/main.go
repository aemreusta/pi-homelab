package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/aemreusta/raspi-homelab/runtime/internal/backup"
	"github.com/aemreusta/raspi-homelab/runtime/internal/logstats"
	"github.com/aemreusta/raspi-homelab/runtime/internal/runner"
)

var version = "dev"

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run(args []string) error {
	if len(args) == 1 && args[0] == "log-count" {
		n, err := logstats.Count(os.Stdin)
		if err != nil {
			return err
		}
		_, err = fmt.Println(n)
		return err
	}
	if len(args) == 1 && args[0] == "version" {
		fmt.Println(version)
		return nil
	}
	if len(args) >= 1 && args[0] == "mac-presence" {
		return macPresence(args[1:])
	}
	if len(args) >= 1 && args[0] == "indexer-health" {
		return indexerHealth(args[1:])
	}
	if len(args) >= 1 && args[0] == "unhealthy-watchdog" {
		return unhealthyWatchdog(args[1:])
	}
	if len(args) > 0 && args[0] == "runner-metrics" && (len(args) == 5 || len(args) == 6) {
		stale, e := strconv.ParseInt(args[3], 10, 64)
		if e != nil {
			return e
		}
		now, e := strconv.ParseInt(args[4], 10, 64)
		if e != nil {
			return e
		}
		rebuild := ""
		if len(args) == 6 {
			rebuild = args[5]
		}
		_, e = fmt.Print(runner.Render(args[1], args[2], stale, now, rebuild))
		return e
	}
	if len(args) == 3 && args[0] == "backup-observe" {
		summary, e := backup.Observe(os.Stdin, args[1], args[2], time.Now)
		if e != nil {
			return e
		}
		return json.NewEncoder(os.Stdout).Encode(summary)
	}
	return fmt.Errorf("usage: homelab-agent version | log-count < RESPONSE | runner-metrics STATUS HEARTBEAT STALE_SECONDS NOW [REBUILD] | backup-observe LOG METRICS | mac-presence --targets HOST:PORT,... --state PATH --textfile PATH [--fail-threshold N] [--timeout DUR] | mac-presence --probe | indexer-health --probe | indexer-health --prowlarr-url URL --byparr-url URL --prowlarr-config FILE --state FILE --textfile FILE [--target-url URL] [--timeout D]")
}

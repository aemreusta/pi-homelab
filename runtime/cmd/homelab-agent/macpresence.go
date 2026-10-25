package main

import (
	"errors"
	"flag"
	"io"
	"strings"
	"time"

	"github.com/aemreusta/raspi-homelab/runtime/internal/macpresence"
)

// macPresence runs one Mac worker presence probe. `--probe` exits 0 so the
// Ansible consumer can prove the installed binary has this subcommand before
// installing a cron that depends on it.
func macPresence(args []string) error {
	if len(args) == 1 && args[0] == "--probe" {
		return nil
	}
	fs := flag.NewFlagSet("mac-presence", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	targets := fs.String("targets", "", "comma-separated host:port list")
	state := fs.String("state", "", "persisted state JSON path")
	textfile := fs.String("textfile", "", "node_exporter textfile path")
	threshold := fs.Int("fail-threshold", 3, "consecutive failed probes before the Mac is away")
	timeout := fs.Duration("timeout", 2*time.Second, "per-target dial timeout")
	if err := fs.Parse(args); err != nil {
		return errors.New("usage: homelab-agent mac-presence --targets HOST:PORT[,...] --state PATH --textfile PATH [--fail-threshold N] [--timeout D] | mac-presence --probe")
	}
	if fs.NArg() != 0 || *threshold < 1 {
		return errors.New("mac-presence: unexpected arguments or non-positive --fail-threshold")
	}
	var list []string
	for _, t := range strings.Split(*targets, ",") {
		if t = strings.TrimSpace(t); t != "" {
			list = append(list, t)
		}
	}
	return macpresence.Run(macpresence.Config{
		Targets:   list,
		StatePath: *state,
		Textfile:  *textfile,
		Threshold: *threshold,
		Timeout:   *timeout,
	})
}

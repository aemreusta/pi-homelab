package main

import (
	"errors"
	"flag"
	"io"
	"time"

	"github.com/aemreusta/raspi-homelab/runtime/internal/indexerhealth"
)

func indexerHealth(args []string) error {
	if len(args) == 1 && args[0] == "--probe" {
		return nil
	}
	fs := flag.NewFlagSet("indexer-health", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	prowlarr := fs.String("prowlarr-url", "", "local Prowlarr base URL")
	byparr := fs.String("byparr-url", "", "Byparr base URL")
	target := fs.String("target-url", "https://example.com/", "public browser canary")
	config := fs.String("prowlarr-config", "", "Prowlarr config.xml, read without logging secrets")
	state := fs.String("state", "", "private diagnostic JSON")
	textfile := fs.String("textfile", "", "numeric node_exporter metrics")
	timeout := fs.Duration("timeout", 35*time.Second, "per-request wall-clock bound")
	if fs.Parse(args) != nil || fs.NArg() != 0 {
		return errors.New("indexer-health: invalid arguments")
	}
	return indexerhealth.Run(indexerhealth.Config{ProwlarrURL: *prowlarr, ByparrURL: *byparr, TargetURL: *target, ConfigPath: *config, StatePath: *state, Textfile: *textfile, Timeout: *timeout})
}

package main

import (
	"context"
	"errors"
	"flag"
	"io"

	"github.com/aemreusta/raspi-homelab/runtime/internal/watchdog"
)

func unhealthyWatchdog(args []string) error {
	if len(args) == 1 && args[0] == "--probe" {
		return nil
	}
	f := flag.NewFlagSet("unhealthy-watchdog", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	docker := f.String("docker", "/usr/bin/docker", "Docker command")
	helper := f.String("notify-helper", "", "bounded notification helper")
	dir := f.String("state-dir", "", "legacy state directory")
	state := f.String("state", "", "private durable counter ledger")
	metrics := f.String("textfile", "", "numeric metrics")
	log := f.String("log", "", "watchdog log")
	threshold := f.Int64("threshold", 3, "consecutive unhealthy polls")
	budget := f.Int64("max-restarts", 5, "successful restarts per 24 hours")
	cooldown := f.Int64("alert-cooldown", 21600, "urgent notification cooldown seconds")
	if f.Parse(args) != nil || f.NArg() != 0 {
		return errors.New("watchdog: invalid arguments")
	}
	return watchdog.Run(context.Background(), watchdog.Config{Docker: *docker, NotifyHelper: *helper, StateDir: *dir, StateFile: *state, Textfile: *metrics, Log: *log, Threshold: *threshold, MaxRestarts: *budget, Cooldown: *cooldown})
}

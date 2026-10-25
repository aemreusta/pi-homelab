// Package backup streams rclone output to durable logs and bounded-memory metrics.
package backup

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/aemreusta/raspi-homelab/runtime/internal/atomicfile"
)

type Summary struct {
	Bytes  int64 `json:"bytes"`
	Errors int64 `json:"errors"`
}
type event struct {
	Level string `json:"level"`
	Stats *struct {
		Bytes      int64   `json:"bytes"`
		Errors     int64   `json:"errors"`
		TotalBytes int64   `json:"totalBytes"`
		Speed      float64 `json:"speed"`
	} `json:"stats"`
}

// Observe never holds the complete log in memory. Failure stops the producer
// through pipefail; it cannot turn a failed rclone transfer into a success.
func Observe(input io.Reader, logPath, metricsPath string, now func() time.Time) (Summary, error) {
	var result Summary
	log, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0640)
	if err != nil {
		return result, err
	}
	defer log.Close()
	var total int64
	var speed float64
	emit := func(running int) error {
		metrics := []struct {
			name, help string
			value      any
		}{
			{"gdrive_backup_progress_timestamp_seconds", "Last observer update.", now().Unix()},
			{"gdrive_backup_running", "1 while the observer is receiving sync output.", running},
			{"gdrive_backup_progress_bytes", "Bytes transferred in the current or last sync.", result.Bytes},
			{"gdrive_backup_progress_total_bytes", "Known total bytes in the sync.", total},
			{"gdrive_backup_progress_errors", "Errors observed in the sync.", result.Errors},
			{"gdrive_backup_progress_bytes_per_second", "Latest transfer rate.", speed},
		}
		var b strings.Builder
		for _, m := range metrics {
			fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s gauge\n%s %v\n", m.name, m.help, m.name, m.name, m.value)
		}
		return atomicfile.Write(metricsPath, []byte(b.String()), 0644)
	}
	if err = emit(1); err != nil {
		return result, err
	}
	scanner := bufio.NewScanner(input)
	scanner.Buffer(make([]byte, 65536), 4<<20)
	var errorLines int64
	for scanner.Scan() {
		line := scanner.Bytes()
		if _, err = log.Write(append(line, '\n')); err != nil {
			return result, err
		}
		var e event
		if json.Unmarshal(line, &e) != nil {
			continue
		} // Keep non-JSON diagnostic lines too.
		if strings.EqualFold(e.Level, "error") {
			errorLines++
		}
		if e.Stats != nil {
			result.Bytes = e.Stats.Bytes
			result.Errors = e.Stats.Errors
			total = e.Stats.TotalBytes
			speed = e.Stats.Speed
		}
		if errorLines > result.Errors {
			result.Errors = errorLines
		}
		// Avoid an fsync/rename for every object; statistics arrive at --stats cadence.
		if e.Stats != nil || strings.EqualFold(e.Level, "error") {
			if err = emit(1); err != nil {
				return result, err
			}
		}
	}
	if err = scanner.Err(); err != nil {
		return result, err
	}
	if err = log.Close(); err != nil {
		return result, err
	}
	return result, emit(0)
}

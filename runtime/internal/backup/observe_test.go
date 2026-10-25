package backup

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestStreamPersistsDiagnosticsAndFinalStatistics(t *testing.T) {
	d := t.TempDir()
	log, metrics := filepath.Join(d, "run.log"), filepath.Join(d, "run.prom")
	input := "not JSON but useful\n" + `{"level":"error","msg":"retryable failure"}` + "\n" + `{"stats":{"bytes":9007199254740993,"totalBytes":9007199254740994,"errors":2,"speed":1024}}` + "\n"
	summary, err := Observe(strings.NewReader(input), log, metrics, func() time.Time { return time.Unix(123, 0) })
	if err != nil {
		t.Fatal(err)
	}
	if summary.Bytes != 9007199254740993 || summary.Errors != 2 {
		t.Fatalf("%+v", summary)
	}
	got, _ := os.ReadFile(log)
	if string(got) != input {
		t.Fatal("log lost details")
	}
	got, _ = os.ReadFile(metrics)
	for _, want := range []string{"gdrive_backup_running 0\n", "gdrive_backup_progress_timestamp_seconds 123\n", "gdrive_backup_progress_bytes 9007199254740993\n"} {
		if !strings.Contains(string(got), want) {
			t.Fatalf("missing %s", want)
		}
	}
}
func TestPublishesProgressBeforeEOF(t *testing.T) {
	d := t.TempDir()
	log, metrics := filepath.Join(d, "log"), filepath.Join(d, "progress.prom")
	reader, writer := io.Pipe()
	done := make(chan error, 1)
	go func() { _, err := Observe(reader, log, metrics, time.Now); done <- err }()
	defer reader.Close()
	defer writer.Close()
	if _, err := io.WriteString(writer, `{"stats":{"bytes":42}}`+"\n"); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	found := false
	for time.Now().Before(deadline) {
		b, _ := os.ReadFile(metrics)
		if strings.Contains(string(b), "gdrive_backup_progress_bytes 42\n") && strings.Contains(string(b), "gdrive_backup_running 1\n") {
			found = true
			break
		}
		time.Sleep(time.Millisecond)
	}
	writer.Close()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if !found {
		t.Fatal("no progress while stream still open")
	}
}
func TestMetricWriteFailureKeepsOldFile(t *testing.T) {
	d := t.TempDir()
	target := filepath.Join(d, "directory")
	if err := os.Mkdir(target, 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := Observe(strings.NewReader(""), filepath.Join(d, "log"), target, time.Now); err == nil {
		t.Fatal("accepted failed metric publish")
	}
	entries, _ := os.ReadDir(d)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".homelab-") {
			t.Fatal("temporary file leaked")
		}
	}
}

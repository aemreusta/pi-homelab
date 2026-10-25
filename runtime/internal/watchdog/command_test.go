package watchdog

import (
	"context"
	"os/exec"
	"testing"
	"time"
)

func TestRealSubprocessCannotBypassOutputCap(t *testing.T) {
	sh, err := exec.LookPath("sh")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	b, err := command(ctx, sh, "-c", "dd if=/dev/zero bs=1048577 count=1 2>/dev/null")
	if err == nil || len(b) > 1<<20 {
		t.Fatalf("output=%d err=%v", len(b), err)
	}
}

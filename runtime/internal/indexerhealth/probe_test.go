package indexerhealth

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testConfig(server *httptest.Server) Config {
	return Config{ProwlarrURL: server.URL, ByparrURL: server.URL, TargetURL: "https://example.com/", Timeout: time.Second}
}

func TestCollectDistinguishesAppHealthFromBrowserSuccess(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/health":
			if r.Header.Get("X-Api-Key") != "fixture-secret" {
				t.Error("API key missing")
			}
			fmt.Fprint(w, `[{"source":"IndexerProxyStatusCheck","type":"error","message":"private detail"},{"source":"IndexerLongTermStatusCheck","type":"warning"}]`)
		case "/api/v1/indexerstatus":
			if r.Header.Get("X-Api-Key") != "fixture-secret" {
				t.Error("API key missing from status read")
			}
			fmt.Fprintf(w, `[{"indexerId":4,"disabledTill":%q}]`, time.Now().Add(time.Hour).UTC().Format(time.RFC3339))
		case "/v1":
			if r.Method != http.MethodPost || r.Header.Get("X-Api-Key") != "" {
				t.Error("wrong request or secret sent to browser")
			}
			var payload struct {
				Cmd        string `json:"cmd"`
				URL        string `json:"url"`
				MaxTimeout int    `json:"maxTimeout"`
			}
			if json.NewDecoder(r.Body).Decode(&payload) != nil || payload.Cmd != "request.get" || payload.URL != "https://example.com/" || payload.MaxTimeout < 1 || payload.MaxTimeout > 30000 {
				t.Error("invalid bounded browser request")
			}
			fmt.Fprint(w, `{"status":"ok","solution":{"status":200,"response":"<html>canary</html>"}}`)
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
		}
	}))
	defer s.Close()
	r := Collect(context.Background(), testConfig(s), "fixture-secret")
	if !r.ProwlarrReadOK || !r.StatusReadOK || r.IndexersInBackoff != 1 || r.ProxyProblems != 1 || r.IndexerProblems != 1 || !r.BrowserOK || r.BrowserHTTPStatus != 200 || r.BrowserBytes == 0 {
		t.Fatalf("unexpected result %+v", r)
	}
	metrics := Render(r)
	for _, want := range []string{"indexer_health_prowlarr_read_success 1", "indexer_health_proxy_problems 1", "indexer_health_browser_success 1", "indexer_health_indexers_in_backoff 1"} {
		if !strings.Contains(metrics, want) {
			t.Errorf("missing %s", want)
		}
	}
	if strings.Contains(metrics, "fixture-secret") || strings.Contains(metrics, "private detail") {
		t.Fatal("private material leaked into metrics")
	}
}

func TestBrowserFailureVerdicts(t *testing.T) {
	for _, tc := range []struct {
		name, body, code string
		status           int
	}{
		{"error envelope", `{"status":"error","message":"private upstream detail"}`, "browser_error", 200},
		{"upstream error", `{"status":"ok","solution":{"status":525,"response":"error page"}}`, "upstream_http", 200},
		{"empty response", `{"status":"ok","solution":{"status":200,"response":""}}`, "empty_response", 200},
		{"missing solution", `{"status":"ok"}`, "upstream_http", 200},
		{"invalid JSON", `not JSON`, "invalid_json", 200},
		{"oversized response", strings.Repeat("x", maxBodyBytes+1), "body_too_large", 200},
		{"HTTP failure", `private gateway detail`, "http_status", 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasPrefix(r.URL.Path, "/api/v1/") {
					fmt.Fprint(w, `[]`)
					return
				}
				w.WriteHeader(tc.status)
				fmt.Fprint(w, tc.body)
			}))
			defer s.Close()
			r := Collect(context.Background(), testConfig(s), "fixture-secret")
			if r.BrowserOK || r.BrowserError != tc.code || !r.ProwlarrReadOK {
				t.Fatalf("unexpected result %+v", r)
			}
		})
	}
}

func TestHealthRequiresArrayAndDoesNotLeakRedirectedKey(t *testing.T) {
	for _, body := range []string{`null`, `{}`, `[] trailing`, `malformed`} {
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, body) }))
		r := Collect(context.Background(), testConfig(s), "fixture-secret")
		s.Close()
		if r.ProwlarrReadOK || r.ProwlarrError != "invalid_json" {
			t.Fatalf("accepted invalid health %q: %+v", body, r)
		}
	}
	for _, body := range []string{`[null]`, `[{}]`, `[{"source":"IndexerProxyStatusCheck"}]`} {
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, body) }))
		r := Collect(context.Background(), testConfig(s), "fixture-secret")
		s.Close()
		if r.ProwlarrReadOK || r.ProwlarrError != "invalid_health_record" {
			t.Fatalf("accepted invalid health record %q: %+v", body, r)
		}
	}
	called := false
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true; t.Error("followed API redirect") }))
	defer destination.Close()
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, destination.URL, http.StatusFound) }))
	defer source.Close()
	r := Collect(context.Background(), testConfig(source), "fixture-secret")
	if called || r.ProwlarrReadOK || r.ProwlarrError != "http_status" {
		t.Fatalf("unsafe redirect: %+v", r)
	}
}

func TestIndexerNoticesAreNotPromotedToFailures(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/health" {
			fmt.Fprint(w, `[{"source":"IndexerLongTermStatusCheck","type":"notice"}]`)
			return
		}
		if r.URL.Path == "/api/v1/indexerstatus" {
			fmt.Fprint(w, `[]`)
			return
		}
		fmt.Fprint(w, `{"status":"ok","solution":{"status":200,"response":"canary"}}`)
	}))
	defer s.Close()
	r := Collect(context.Background(), testConfig(s), "fixture-secret")
	if !r.ProwlarrReadOK || r.IndexerProblems != 0 || r.ProxyProblems != 0 || !r.BrowserOK {
		t.Fatalf("notice promoted to failure: %+v", r)
	}
}

func TestBackoffCountsOnlyUniqueFutureProviders(t *testing.T) {
	now := time.Date(2026, 10, 11, 8, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		body  string
		count int
		code  string
	}{
		{`[]`, 0, ""},
		{`[{"indexerId":1},{"indexerId":2,"disabledTill":null}]`, 0, ""},
		{`[{"indexerId":1,"disabledTill":"2026-10-11T07:59:00Z"},{"indexerId":2,"disabledTill":"2026-10-11T08:00:00Z"}]`, 0, ""},
		{`[{"indexerId":1,"disabledTill":"2026-10-11T11:01:00+03:00"},{"indexerId":1,"disabledTill":"2026-10-11T08:02:00Z"},{"indexerId":2,"disabledTill":"2026-10-11T08:03:00Z"}]`, 2, ""},
		{`null`, 0, "invalid_json"},
		{`{}`, 0, "invalid_json"},
		{`[] trailing`, 0, "invalid_json"},
		{`[{"indexerId":1,"disabledTill":"broken"}]`, 0, "invalid_json"},
		{`[null]`, 0, "invalid_status_record"},
		{`[{}]`, 0, "invalid_status_record"},
		{`[{"indexerId":0}]`, 0, "invalid_status_record"},
	} {
		count, code := parseBackoff([]byte(tc.body), now)
		if count != tc.count || code != tc.code {
			t.Errorf("body %s: count=%d code=%s", tc.body, count, code)
		}
	}
}

func TestStatusFailureCannotBeHiddenByHealthyApplicationAndBrowser(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/health":
			fmt.Fprint(w, `[]`)
		case "/api/v1/indexerstatus":
			w.WriteHeader(http.StatusServiceUnavailable)
		case "/v1":
			fmt.Fprint(w, `{"status":"ok","solution":{"status":200,"response":"canary"}}`)
		}
	}))
	defer s.Close()
	r := Collect(context.Background(), testConfig(s), "fixture-secret")
	if !r.ProwlarrReadOK || !r.BrowserOK || r.StatusReadOK || r.StatusError != "http_status" {
		t.Fatalf("status failure hidden: %+v", r)
	}
}

func TestCanceledProbeIsBounded(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
	defer s.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	start := time.Now()
	r := Collect(ctx, testConfig(s), "fixture-secret")
	if r.ProwlarrReadOK || r.BrowserOK || r.ProwlarrError != "request_failed" || time.Since(start) > time.Second {
		t.Fatalf("unbounded/canceled result %+v", r)
	}
}

func TestRunPublishesFailureAndKeepsSecretsPrivate(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		fmt.Fprint(w, "private response")
	}))
	defer s.Close()
	cfg := testConfig(s)
	dir := t.TempDir()
	cfg.ConfigPath = filepath.Join(dir, "config.xml")
	cfg.StatePath = filepath.Join(dir, "status.json")
	cfg.Textfile = filepath.Join(dir, "indexer_health.prom")
	if err := os.WriteFile(cfg.ConfigPath, []byte(`<Config><ApiKey>fixture-secret</ApiKey></Config>`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := Run(cfg); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(cfg.Textfile)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), "indexer_health_browser_success 0") || !strings.Contains(string(data), "indexer_health_prowlarr_read_success 0") {
		t.Fatal("failure hidden by collector")
	}
	state, err := os.ReadFile(cfg.StatePath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(state), "fixture-secret") || strings.Contains(string(state), "private response") {
		t.Fatal("private data leaked into state")
	}
	st, err := os.Stat(cfg.StatePath)
	if err != nil || st.Mode().Perm() != 0600 {
		t.Fatal("state is not private")
	}
	if err := os.Remove(cfg.ConfigPath); err != nil {
		t.Fatal(err)
	}
	if err := Run(cfg); err != nil {
		t.Fatal(err)
	}
	state, _ = os.ReadFile(cfg.StatePath)
	if !strings.Contains(string(state), "config_unreadable") {
		t.Fatal("missing config did not publish a degraded result")
	}
}

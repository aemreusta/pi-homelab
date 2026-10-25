// Package indexerhealth observes Prowlarr and a bounded real Byparr browser
// request. It does not reset indexer backoff, change settings or grab releases.
package indexerhealth

import (
	"bytes"
	"context"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/aemreusta/raspi-homelab/runtime/internal/atomicfile"
)

const maxBodyBytes = 1 << 20

type Config struct {
	ProwlarrURL, ByparrURL, TargetURL string
	ConfigPath, StatePath, Textfile   string
	Timeout                           time.Duration
}

// Result contains numeric observations and bounded error categories only.
// API keys, fetched pages, cookies and Prowlarr's free-text messages stay private.
type Result struct {
	Timestamp         int64  `json:"checked_at"`
	DurationMillis    int64  `json:"duration_milliseconds"`
	ProwlarrReadOK    bool   `json:"prowlarr_read_ok"`
	ProxyProblems     int    `json:"proxy_problems"`
	IndexerProblems   int    `json:"indexer_problems"`
	ProwlarrError     string `json:"prowlarr_error,omitempty"`
	StatusReadOK      bool   `json:"status_read_ok"`
	IndexersInBackoff int    `json:"indexers_in_backoff"`
	StatusError       string `json:"status_error,omitempty"`
	BrowserOK         bool   `json:"browser_ok"`
	BrowserHTTPStatus int    `json:"browser_http_status"`
	BrowserBytes      int    `json:"browser_bytes"`
	BrowserError      string `json:"browser_error,omitempty"`
}

func validURL(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && (u.Scheme == "http" || u.Scheme == "https") && u.Host != "" && u.User == nil && u.RawQuery == "" && u.Fragment == ""
}

func (cfg Config) validate() error {
	if !validURL(cfg.ProwlarrURL) || !validURL(cfg.ByparrURL) || !validURL(cfg.TargetURL) {
		return errors.New("indexer-health: invalid HTTP URL")
	}
	if cfg.Timeout < time.Second || cfg.Timeout > time.Minute {
		return errors.New("indexer-health: timeout must be between 1s and 1m")
	}
	if cfg.ConfigPath == "" || cfg.StatePath == "" || cfg.Textfile == "" || filepath.Clean(cfg.StatePath) == filepath.Clean(cfg.Textfile) {
		return errors.New("indexer-health: config and distinct state/textfile paths required")
	}
	return nil
}

func readAPIKey(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxBodyBytes+1))
	if err != nil || len(data) > maxBodyBytes {
		return ""
	}
	var config struct {
		APIKey string `xml:"ApiKey"`
	}
	if xml.Unmarshal(data, &config) != nil {
		return ""
	}
	key := strings.TrimSpace(config.APIKey)
	if len(key) > 4096 || strings.ContainsAny(key, "\r\n") {
		return ""
	}
	return key
}

func request(client *http.Client, req *http.Request) ([]byte, string) {
	r, err := client.Do(req)
	if err != nil {
		return nil, "request_failed"
	}
	defer r.Body.Close()
	if r.StatusCode < 200 || r.StatusCode >= 300 {
		return nil, "http_status"
	}
	data, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes+1))
	if err != nil {
		return nil, "read_failed"
	}
	if len(data) > maxBodyBytes {
		return nil, "body_too_large"
	}
	return data, ""
}

// Collect completes both observations even when one fails. All HTTP requests
// have a wall-clock timeout, no redirects and a bounded response body.
func Collect(ctx context.Context, cfg Config, key string) Result {
	start := time.Now()
	transport := &http.Transport{
		DialContext:           (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
		TLSHandshakeTimeout:   5 * time.Second,
		ResponseHeaderTimeout: cfg.Timeout,
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: cfg.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	result := Result{}
	if key == "" {
		result.ProwlarrError = "config_unreadable"
		result.StatusError = "config_unreadable"
	} else {
		apiContext, cancelAPI := context.WithTimeout(ctx, min(5*time.Second, cfg.Timeout))
		req, err := http.NewRequestWithContext(apiContext, http.MethodGet, strings.TrimRight(cfg.ProwlarrURL, "/")+"/api/v1/health", nil)
		if err != nil {
			result.ProwlarrError = "invalid_request"
		} else {
			req.Header.Set("X-Api-Key", key)
			data, code := request(client, req)
			result.ProwlarrError = code
			if code == "" {
				var health []struct {
					Source string `json:"source"`
					Type   string `json:"type"`
				}
				if !bytes.HasPrefix(bytes.TrimSpace(data), []byte("[")) || json.Unmarshal(data, &health) != nil {
					result.ProwlarrError = "invalid_json"
				} else {
					result.ProwlarrReadOK = true
					for _, h := range health {
						if h.Source == "" || h.Type == "" {
							result.ProwlarrReadOK = false
							result.ProwlarrError = "invalid_health_record"
							result.ProxyProblems, result.IndexerProblems = 0, 0
							break
						}
						if h.Type != "error" && h.Type != "warning" {
							continue
						}
						if h.Source == "IndexerProxyStatusCheck" {
							result.ProxyProblems++
						} else if strings.HasPrefix(h.Source, "Indexer") {
							result.IndexerProblems++
						}
					}
				}
			}
		}
		cancelAPI()

		statusContext, cancelStatus := context.WithTimeout(ctx, min(5*time.Second, cfg.Timeout))
		statusRequest, err := http.NewRequestWithContext(statusContext, http.MethodGet, strings.TrimRight(cfg.ProwlarrURL, "/")+"/api/v1/indexerstatus", nil)
		if err != nil {
			result.StatusError = "invalid_request"
		} else {
			statusRequest.Header.Set("X-Api-Key", key)
			data, code := request(client, statusRequest)
			result.StatusError = code
			if code == "" {
				result.IndexersInBackoff, result.StatusError = parseBackoff(data, time.Now())
				result.StatusReadOK = result.StatusError == ""
			}
		}
		cancelStatus()
	}

	payload, _ := json.Marshal(struct {
		Cmd        string `json:"cmd"`
		URL        string `json:"url"`
		MaxTimeout int64  `json:"maxTimeout"`
		BlockMedia bool   `json:"blockMedia"`
	}{"request.get", cfg.TargetURL, min(int64(30000), cfg.Timeout.Milliseconds()), true})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(cfg.ByparrURL, "/")+"/v1", bytes.NewReader(payload))
	if err != nil {
		result.BrowserError = "invalid_request"
	} else {
		req.Header.Set("Content-Type", "application/json")
		data, code := request(client, req)
		result.BrowserError = code
		if code == "" {
			var response struct {
				Status   string `json:"status"`
				Solution struct {
					Status   int    `json:"status"`
					Response string `json:"response"`
				} `json:"solution"`
			}
			if json.Unmarshal(data, &response) != nil {
				result.BrowserError = "invalid_json"
			} else {
				result.BrowserHTTPStatus = response.Solution.Status
				result.BrowserBytes = len(response.Solution.Response)
				switch {
				case response.Status != "ok":
					result.BrowserError = "browser_error"
				case response.Solution.Status < 200 || response.Solution.Status >= 300:
					result.BrowserError = "upstream_http"
				case strings.TrimSpace(response.Solution.Response) == "":
					result.BrowserError = "empty_response"
				default:
					result.BrowserOK = true
				}
			}
		}
	}
	result.Timestamp = time.Now().Unix()
	result.DurationMillis = time.Since(start).Milliseconds()
	return result
}

func parseBackoff(data []byte, now time.Time) (int, string) {
	var records []struct {
		IndexerID    int        `json:"indexerId"`
		DisabledTill *time.Time `json:"disabledTill"`
	}
	if !bytes.HasPrefix(bytes.TrimSpace(data), []byte("[")) || json.Unmarshal(data, &records) != nil {
		return 0, "invalid_json"
	}
	blocked := make(map[int]bool)
	for _, r := range records {
		if r.IndexerID <= 0 {
			return 0, "invalid_status_record"
		}
		if r.DisabledTill != nil && r.DisabledTill.After(now) {
			blocked[r.IndexerID] = true
		}
	}
	return len(blocked), ""
}

func Render(r Result) string {
	var b strings.Builder
	flag := func(v bool) int64 {
		if v {
			return 1
		}
		return 0
	}
	for _, metric := range []struct {
		name, help string
		value      int64
	}{
		{"indexer_health_last_run_timestamp_seconds", "Unix time the bounded indexer observation completed, including unhealthy results.", r.Timestamp},
		{"indexer_health_duration_milliseconds", "Elapsed observer time; two local API reads are bounded to 5s each and browser to at most 60s.", r.DurationMillis},
		{"indexer_health_prowlarr_read_success", "1 when the authenticated Prowlarr health array was read and parsed.", flag(r.ProwlarrReadOK)},
		{"indexer_health_status_read_success", "1 when the authenticated Prowlarr indexerstatus array was read and parsed.", flag(r.StatusReadOK)},
		{"indexer_health_indexers_in_backoff", "Unique indexers whose native disabledTill remains in the future; no reset is performed.", int64(r.IndexersInBackoff)},
		{"indexer_health_proxy_problems", "Proxy warning/error health records; excludes unrelated Prowlarr notices.", int64(r.ProxyProblems)},
		{"indexer_health_indexer_problems", "Indexer warning/error health records; availability is not release acceptance.", int64(r.IndexerProblems)},
		{"indexer_health_browser_success", "1 when a real bounded Byparr browser request returned nonempty 2xx content; not protected-indexer acceptance.", flag(r.BrowserOK)},
		{"indexer_health_browser_http_status", "HTTP status of the canary response reported by Byparr, or 0 without a solution.", int64(r.BrowserHTTPStatus)},
	} {
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s gauge\n%s %d\n", metric.name, metric.help, metric.name, metric.name, metric.value)
	}
	return b.String()
}

// Run publishes a complete private diagnostic and numeric public metrics.
// Failed observations are data, not permission to mutate indexer state.
func Run(cfg Config) error {
	if err := cfg.validate(); err != nil {
		return err
	}
	r := Collect(context.Background(), cfg, readAPIKey(cfg.ConfigPath))
	data, err := json.Marshal(r)
	if err != nil {
		return err
	}
	if err = atomicfile.Write(cfg.StatePath, append(data, '\n'), 0600); err != nil {
		return errors.New("indexer-health: status publication failed")
	}
	if err = atomicfile.Write(cfg.Textfile, []byte(Render(r)), 0644); err != nil {
		return errors.New("indexer-health: metric publication failed")
	}
	return nil
}

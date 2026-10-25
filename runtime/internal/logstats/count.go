// Package logstats validates VictoriaLogs count responses before alert decisions.
package logstats

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"strconv"
)

// Count accepts the canonical count(*) field and explicit legacy aliases.
// A failed/changed response must never masquerade as a valid zero count.
func Count(input io.Reader) (int64, error) {
	d := json.NewDecoder(input)
	d.UseNumber()
	var total int64
	rows := 0
	for {
		var row map[string]any
		if err := d.Decode(&row); err == io.EOF {
			break
		} else if err != nil {
			return 0, fmt.Errorf("invalid log statistics: %w", err)
		}
		var value any
		found := false
		for _, key := range []string{"count(*)", "count()", "cnt", "rows"} {
			if v, ok := row[key]; ok {
				if found {
					return 0, fmt.Errorf("ambiguous log count fields")
				}
				value, found = v, true
			}
		}
		if !found {
			return 0, fmt.Errorf("missing log count field")
		}
		var raw string
		switch v := value.(type) {
		case string:
			raw = v
		case json.Number:
			raw = v.String()
		default:
			return 0, fmt.Errorf("invalid log count type")
		}
		n, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || n < 0 || total > math.MaxInt64-n {
			return 0, fmt.Errorf("invalid or overflowing log count")
		}
		total += n
		rows++
	}
	if rows == 0 {
		return 0, fmt.Errorf("empty log statistics response")
	}
	return total, nil
}

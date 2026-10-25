package logstats

import (
	"strings"
	"testing"
)

func TestCounts(t *testing.T) {
	for _, tc := range []struct {
		in   string
		want int64
	}{
		{`{"count(*)":"169"}`, 169}, {`{"count(*)":0}`, 0},
		{`{"count()":"12"}`, 12}, {"{\"cnt\":3}\n{\"rows\":4}", 7},
	} {
		got, err := Count(strings.NewReader(tc.in))
		if err != nil || got != tc.want {
			t.Fatalf("%s: got %d, %v", tc.in, got, err)
		}
	}
}

func TestFailuresAreNotZero(t *testing.T) {
	for _, in := range []string{"", " ", "<html>error</html>", `{"error":"timeout"}`, `[]`, `null`, `{"cnt":null}`, `{"cnt":-1}`, `{"cnt":1.5}`, `{"cnt":true}`, `{"cnt":0,"rows":4}`, `{"cnt":0}garbage`, "{\"cnt\":9223372036854775807}\n{\"cnt\":1}"} {
		if _, err := Count(strings.NewReader(in)); err == nil {
			t.Errorf("accepted %q", in)
		}
	}
}

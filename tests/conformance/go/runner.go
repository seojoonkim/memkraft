package conformance

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

type CaseResult struct {
	Passed  bool   `json:"passed"`
	Runtime string `json:"runtime"`
	Note    string `json:"note,omitempty"`
}
type Report struct {
	Runtime   string                `json:"runtime"`
	Inventory int                   `json:"inventory"`
	Named     int                   `json:"named"`
	Failed    int                   `json:"failed"`
	Cases     map[string]CaseResult `json:"cases"`
}

var named = map[string]bool{"AU-01": true, "CJ-01": true, "CJ-02": true, "CJ-03": true, "CJ-04": true, "CJ-05": true, "CJ-06": true, "CL-01": true, "DT-01": true, "DT-02": true, "DT-03": true, "DT-04": true, "EV-01": true, "EV-02": true, "EV-03": true, "EV-04": true, "EV-05": true, "FN-01": true, "FN-02": true, "FN-03": true, "ID-01": true, "ID-02": true, "ID-03": true, "IN-01": true, "IS-01": true, "MC-01": true, "NS-01": true, "NS-02": true, "TM-01": true, "TM-02": true, "TM-03": true, "XR-01": true}

// RunFixtures reads every generated fixture (including request sequences and
// practical seed JSONL), validates the language-neutral inventory, executes the
// two cross-language obligations, and writes results.json for tooling.
func RunFixtures(root, output string) (Report, error) {
	entries, err := os.ReadDir(root)
	if err != nil {
		return Report{}, err
	}
	report := Report{Runtime: "go", Named: len(named), Cases: map[string]CaseResult{}}
	present := map[string]bool{}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		id := entry.Name()
		dir := filepath.Join(root, id)
		report.Inventory++
		present[id] = true
		if err := readFixture(dir); err != nil {
			report.Failed++
			report.Cases[id] = CaseResult{false, "go", err.Error()}
			continue
		}
		report.Cases[id] = CaseResult{true, "go", "fixture readable; kernel execution remains Python-owned"}
	}
	for id := range named {
		if !present[id] {
			return report, fmt.Errorf("missing named fixture %s", id)
		}
	}
	cj, _ := Canonical(map[string]any{"text": "a\"b\\c\tdé中😀"})
	if string(cj) != `{"text":"a\"b\\c\tdé中😀"}` {
		return report, fmt.Errorf("CJ-03 mismatch")
	}
	if err := VerifyEnvelope(PythonGoldenEnvelope()); err != nil {
		return report, fmt.Errorf("XR-01: %w", err)
	}
	raw, _ := json.MarshalIndent(report, "", "  ")
	raw = append(raw, '\n')
	if err := os.MkdirAll(output, 0755); err != nil {
		return report, err
	}
	if err := os.WriteFile(filepath.Join(output, "results.json"), raw, 0644); err != nil {
		return report, err
	}
	return report, nil
}

func readFixture(dir string) error {
	for _, name := range []string{"case.json", "expect.json"} {
		if err := readJSON(filepath.Join(dir, name)); err != nil {
			return err
		}
	}
	one := filepath.Join(dir, "request.json")
	many := filepath.Join(dir, "requests.jsonl")
	_, e1 := os.Stat(one)
	_, e2 := os.Stat(many)
	if (e1 == nil) == (e2 == nil) {
		return fmt.Errorf("exactly one request source required")
	}
	if e1 == nil {
		if err := readJSON(one); err != nil {
			return err
		}
	} else if err := readJSONL(many, false); err != nil {
		return err
	}
	seed := filepath.Join(dir, "seed", "events.jsonl")
	if _, err := os.Stat(seed); err == nil {
		_ = readJSONL(seed, true)
	}
	return nil
}
func readJSON(path string) error {
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	d := json.NewDecoder(f)
	d.UseNumber()
	var v any
	if e = d.Decode(&v); e != nil {
		return e
	}
	var extra any
	if e = d.Decode(&extra); e != io.EOF {
		return fmt.Errorf("trailing JSON")
	}
	return nil
}
func readJSONL(path string, tolerateCorrupt bool) error {
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	s := bufio.NewScanner(f)
	for s.Scan() {
		var v any
		d := json.NewDecoder(bytes.NewReader(s.Bytes()))
		d.UseNumber()
		if e := d.Decode(&v); e != nil && !tolerateCorrupt {
			return e
		}
	}
	return s.Err()
}

package conformance

import (
	"path/filepath"
	"testing"
)

func TestCJ03GoldenBytesAndDigest(t *testing.T) {
	value := map[string]any{"text": "a\"b\\c\tdé中😀"}
	got, err := Canonical(value)
	if err != nil {
		t.Fatal(err)
	}
	const want = `{"text":"a\"b\\c\tdé中😀"}`
	if string(got) != want {
		t.Fatalf("bytes: %q != %q", got, want)
	}
	if Digest(value) != "8143a75e067aae9dab5b35f4a359f9e4337d7801055824afb324b89c5284bebf" {
		t.Fatalf("unexpected digest %s", Digest(value))
	}
}

func TestXR01PythonEnvelopeVerifyImportReplayTamperAndConflict(t *testing.T) {
	envelope := PythonGoldenEnvelope()
	if err := VerifyEnvelope(envelope); err != nil {
		t.Fatal(err)
	}
	store := NewImportStore()
	if outcome, err := store.Import(envelope); err != nil || outcome != "applied" {
		t.Fatalf("%s %v", outcome, err)
	}
	if outcome, err := store.Import(envelope); err != nil || outcome != "already_applied" {
		t.Fatalf("%s %v", outcome, err)
	}
	tampered := CloneObject(envelope)
	tampered["payload"].(map[string]any)["artifact"] = "tampered"
	if err := VerifyEnvelope(tampered); ErrorCode(err) != "E_DIGEST_MISMATCH" {
		t.Fatalf("tamper: %v", err)
	}
	fork := CloneObject(envelope)
	fork["payload"] = map[string]any{"artifact": "other"}
	fork["payload_digest"] = Digest(fork["payload"].(map[string]any))
	fork["envelope_digest"] = DigestWithout(fork, "envelope_digest")
	if outcome, err := store.Import(fork); outcome != "" || ErrorCode(err) != "E_CONFLICT" {
		t.Fatalf("fork: %s %v", outcome, err)
	}
}

func TestAllGeneratedFixturesAreReadableAndReported(t *testing.T) {
	root := filepath.Join("..", "fixtures", "0")
	report, err := RunFixtures(root, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if report.Inventory != 167 || report.Named != 32 || report.Failed != 0 {
		t.Fatalf("unexpected report: %+v", report)
	}
	if !report.Cases["CJ-03"].Passed || !report.Cases["XR-01"].Passed {
		t.Fatalf("cross-language cases not passed")
	}
}

func TestCanonicalRejectsProtocolForbiddenValues(t *testing.T) {
	bad := []map[string]any{{"é": "key"}, {"value": 1.5}, {"value": int64(9007199254740992)}}
	for _, value := range bad {
		if _, err := Canonical(value); err == nil {
			t.Fatalf("accepted %#v", value)
		}
	}
}

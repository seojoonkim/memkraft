package conformance

import (
	"bytes"
	"encoding/json"
	"fmt"
)

func PythonGoldenEnvelope() map[string]any {
	payload := map[string]any{
		"artifact": "sha256:abc",
		"parents":  []any{"build-linux", "build-macos"},
		"verifier": "independent",
	}
	return map[string]any{
		"envelope_schema":    "memkraft.handoff/1",
		"origin_instance_id": "00000000000000000000000000000001",
		"goal_id":            "conformance/xr-goal",
		"handoff_id":         "11111111111111111111111111111111",
		"payload_schema":     "memkraft.handoff.context/1",
		"payload":            payload,
		"payload_digest":     "b9827a64887565c8f59f8f7dd14bfc6b48a250226887122c7687e4e7d311c16e",
		"expires_at":         nil,
		"exported_at":        "2026-08-04T10:00:00Z",
		"envelope_digest":    "bd36e1a52c0cf108daa80bbc4eb1ed31e892875024d4569e6f41198bbbea54e6",
	}
}

func VerifyEnvelope(envelope map[string]any) error {
	payload, ok := envelope["payload"].(map[string]any)
	if !ok {
		return &ProtocolError{"E_TYPE", "payload must be an object"}
	}
	if DigestWithout(envelope, "envelope_digest") != envelope["envelope_digest"] {
		return &ProtocolError{"E_DIGEST_MISMATCH", "envelope digest mismatch"}
	}
	if Digest(payload) != envelope["payload_digest"] {
		return &ProtocolError{"E_DIGEST_MISMATCH", "payload digest mismatch"}
	}
	return nil
}

type ImportStore struct{ payloads map[string]string }

func NewImportStore() *ImportStore { return &ImportStore{map[string]string{}} }
func (s *ImportStore) Import(envelope map[string]any) (string, error) {
	if err := VerifyEnvelope(envelope); err != nil {
		return "", err
	}
	key := fmt.Sprintf("%s/%s", envelope["origin_instance_id"], envelope["handoff_id"])
	payload := envelope["payload_digest"].(string)
	if old, exists := s.payloads[key]; exists {
		if old == payload {
			return "already_applied", nil
		}
		return "", &ProtocolError{"E_CONFLICT", "origin handoff has another payload"}
	}
	s.payloads[key] = payload
	return "applied", nil
}

func CloneObject(value map[string]any) map[string]any {
	raw, _ := json.Marshal(value)
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var clone map[string]any
	_ = decoder.Decode(&clone)
	return clone
}

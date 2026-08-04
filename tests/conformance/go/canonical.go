package conformance

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"
)

const maxSafeInteger int64 = 9007199254740991

var keyPattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)

type ProtocolError struct {
	Code    string
	Message string
}

func (e *ProtocolError) Error() string { return e.Code + ": " + e.Message }
func ErrorCode(err error) string {
	var p *ProtocolError
	if errors.As(err, &p) {
		return p.Code
	}
	return ""
}
func fail(code, format string, args ...any) error {
	return &ProtocolError{code, fmt.Sprintf(format, args...)}
}

// Canonical implements the MKCJSON/1 subset used by the language-neutral kit:
// object roots, ASCII keys, integer-only safe numbers, UTF-8 strings, sorted
// keys, compact JSON, and no HTML escaping.
func Canonical(value map[string]any) ([]byte, error) {
	if err := validate(value, 1); err != nil {
		return nil, err
	}
	value = normalizeObject(value)
	var out bytes.Buffer
	enc := json.NewEncoder(&out)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(out.Bytes(), []byte("\n")), nil
}

func normalizeObject(value map[string]any) map[string]any {
	out := make(map[string]any, len(value))
	for key, child := range value {
		out[key] = normalize(child)
	}
	return out
}

func normalize(value any) any {
	switch v := value.(type) {
	case string:
		return norm.NFC.String(v)
	case map[string]any:
		return normalizeObject(v)
	case []any:
		out := make([]any, len(v))
		for i, child := range v {
			out[i] = normalize(child)
		}
		return out
	default:
		return value
	}
}

func validate(value any, depth int) error {
	if depth > 8 {
		return fail("E_LIMIT_EXCEEDED", "nesting deeper than 8")
	}
	switch v := value.(type) {
	case nil, bool, string:
		if s, ok := v.(string); ok && !utf8.ValidString(s) {
			return fail("E_TYPE", "invalid UTF-8")
		}
		return nil
	case int:
		return validate(int64(v), depth)
	case int64:
		if v < -maxSafeInteger || v > maxSafeInteger {
			return fail("E_LIMIT_EXCEEDED", "integer outside safe range")
		}
		return nil
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") || string(v) == "-0" {
			return fail("E_TYPE", "integer-only number required")
		}
		n, err := v.Int64()
		if err != nil {
			return fail("E_TYPE", "invalid integer")
		}
		return validate(n, depth)
	case float32, float64:
		return fail("E_TYPE", "floats are not representable in MKCJSON/1")
	case map[string]any:
		if len(v) > 64 {
			return fail("E_LIMIT_EXCEEDED", "more than 64 keys")
		}
		for key, child := range v {
			if !keyPattern.MatchString(key) {
				return fail("E_PATTERN", "invalid key %q", key)
			}
			if err := validate(child, depth+1); err != nil {
				return err
			}
		}
		return nil
	case []any:
		if len(v) > 32 {
			return fail("E_LIMIT_EXCEEDED", "array longer than 32")
		}
		for _, child := range v {
			if err := validate(child, depth+1); err != nil {
				return err
			}
		}
		return nil
	default:
		return fail("E_TYPE", "unsupported type %T", value)
	}
}

func Digest(value map[string]any) string {
	body, err := Canonical(value)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}
func DigestWithout(value map[string]any, key string) string {
	copy := make(map[string]any, len(value)-1)
	for k, v := range value {
		if k != key {
			copy[k] = v
		}
	}
	return Digest(copy)
}

// Keep behavior explicit: MKCJSON rejects native floats, even when integral.
var _ = math.Trunc

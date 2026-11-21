package transcription

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/zzttzzmyswy/tingsu/internal/media"
	"github.com/zzttzzmyswy/tingsu/internal/store"
)

func writePCM(t *testing.T, dir string, pcm []byte) string {
	t.Helper()
	f, e := os.CreateTemp(dir, "test-*.wav")
	if e != nil {
		t.Fatal(e)
	}
	if e = media.WriteWAV(f, pcm); e != nil {
		t.Fatal(e)
	}
	if e = f.Close(); e != nil {
		t.Fatal(e)
	}
	return f.Name()
}
func testStore(t *testing.T) (*store.Store, string) {
	t.Helper()
	s, e := store.Open(filepath.Join(t.TempDir(), "db"))
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Close() })
	mid, e := s.Create("test", "", "", 0, "", "")
	if e != nil {
		t.Fatal(e)
	}
	return s, mid
}
func await[T any](t *testing.T, c <-chan T) T {
	t.Helper()
	select {
	case v := <-c:
		return v
	case <-time.After(12 * time.Second):
		t.Fatal("timed out")
	}
	var z T
	return z
}
func upload(t *testing.T, r *http.Request) []byte {
	t.Helper()
	f, h, e := r.FormFile("file")
	if e != nil {
		t.Error(e)
		return nil
	}
	defer f.Close()
	if h.Header.Get("Content-Type") != "audio/wav" {
		t.Error("missing WAV MIME")
	}
	p, e := io.ReadAll(f)
	if e != nil {
		t.Error(e)
	}
	return p
}
func TestClosePreservesInflightAndQueuedTailForRetry(t *testing.T) {
	s, mid := testStore(t)
	dir := t.TempDir()
	pcm := bytes.Repeat([]byte{90, 1}, media.Rate*2)
	jobs := []string{}
	for i := 0; i < 2; i++ {
		p := writePCM(t, dir, pcm)
		jobs = append(jobs, p)
		if e := s.Queue(mid, store.Job{Path: p, Start: float64(i * 2), End: float64(i*2 + 2)}, media.Peaks(pcm)); e != nil {
			t.Fatal(e)
		}
	}
	entered := make(chan struct{})
	release := make(chan struct{})
	var block sync.Once
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upload(t, r)
		block.Do(func() { close(entered); <-release })
		io.WriteString(w, `{"text":"保存发言"}`)
	}))
	defer server.Close()
	t.Setenv("TINGSU_SILICONFLOW_URL", server.URL)
	cfg := Config{Provider: "siliconflow", Model: "fixture-asr", APIKey: "fixture"}
	a := New(s, dir, Callbacks{})
	if e := a.Retry(mid, cfg); e != nil {
		t.Fatal(e)
	}
	await(t, entered)
	if e := a.Retry(mid, cfg); e == nil {
		t.Fatal("duplicate worker started")
	}
	a.Close()
	close(release)
	if s.PendingCount(mid) != 2 || len(s.Events(mid, 0, 0)) != 0 {
		t.Fatal("close lost or committed in-flight jobs")
	}
	done := make(chan struct{}, 1)
	b := New(s, dir, Callbacks{Completed: func(string) { done <- struct{}{} }})
	defer b.Close()
	if e := b.Retry(mid, cfg); e != nil {
		t.Fatal(e)
	}
	await(t, done)
	if s.PendingCount(mid) != 0 || len(s.Events(mid, 0, 0)) != 2 {
		t.Fatal("queue was not drained exactly once")
	}
	for _, p := range jobs {
		if _, e := os.Stat(p); !os.IsNotExist(e) {
			t.Fatal("committed file is still present")
		}
	}
}
func TestRecordStopsAndDrainsTailAfterEmptyFirstResult(t *testing.T) {
	s, mid := testStore(t)
	dir := t.TempDir()
	pcm := bytes.Repeat([]byte{4, 1}, media.Rate*9)
	p := writePCM(t, dir, pcm)
	first := make(chan struct{}, 1)
	done := make(chan struct{}, 1)
	var requests atomic.Int32
	var sizesMu sync.Mutex
	var sizes []int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data := upload(t, r)
		sizesMu.Lock()
		sizes = append(sizes, len(data)-44)
		sizesMu.Unlock()
		if requests.Add(1) == 1 {
			io.WriteString(w, `{"text":""}`)
		} else {
			io.WriteString(w, `{"text":"尾部发言"}`)
		}
	}))
	defer server.Close()
	t.Setenv("TINGSU_SILICONFLOW_URL", server.URL)
	a := New(s, dir, Callbacks{Transcribed: func(_ string, _ float64, has bool) {
		if !has {
			first <- struct{}{}
		}
	}, Completed: func(string) { done <- struct{}{} }, Failed: func(_ string, msg string) { t.Error(msg) }})
	defer a.Close()
	if e := a.Record(mid, media.Source{ID: p, Kind: "file"}, Config{Provider: "siliconflow", APIKey: "fixture", Model: "fixture-asr"}, 12, 0); e != nil {
		t.Fatal(e)
	}
	await(t, first)
	// Wait until the producer has captured part of the second chunk, then stop.
	deadline := time.Now().Add(3 * time.Second)
	for {
		pos, _ := a.Position()
		if pos > 3.5 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("capture stalled")
		}
		time.Sleep(10 * time.Millisecond)
	}
	a.Stop()
	await(t, done)
	rs := s.Events(mid, 0, 0)
	if a.Status() != "idle" || s.PendingCount(mid) != 0 || len(rs) != 1 || rs[0]["at"] != float64(3) {
		t.Fatalf("tail %v", rs)
	}
	sizesMu.Lock()
	defer sizesMu.Unlock()
	if len(sizes) != 2 || sizes[0] != 3*bytesPerSecond || sizes[1] < bytesPerSecond/2 || sizes[1] >= 6*bytesPerSecond {
		t.Fatalf("chunk sizes %v", sizes)
	}
	if rs[0]["end"] != float64(sizes[0]+sizes[1])/bytesPerSecond {
		t.Fatal("sample time lost across stop")
	}
}
func TestDiarizationReferenceBoundary(t *testing.T) {
	dir := t.TempDir()
	p := writePCM(t, dir, bytes.Repeat([]byte{3, 2}, media.Rate*5))
	cfg := Config{Provider: "siliconflow", APIKey: "fixture", Model: diarize}
	type turn = map[string]any
	line := func(a, b float64, sp, text string) turn {
		return turn{"start": a, "end": b, "speaker": sp, "text": text}
	}
	responses := [][]turn{{line(0, 3, "A", "first")}, {line(0, 3, "B", "first"), line(4, 6, "B", "second"), line(6.5, 9, "A", "other")}, {line(0, 10, "C", "contaminated")}, {line(0, 3, "C", "clean")}}
	var idx atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upload(t, r)
		i := int(idx.Add(1)) - 1
		if i >= len(responses) {
			t.Error("extra request")
			return
		}
		json.NewEncoder(w).Encode(turn{"segments": responses[i]})
	}))
	defer server.Close()
	t.Setenv("TINGSU_SILICONFLOW_URL", server.URL)
	a := defaultClient()
	track := &speakerTracker{next: 1}
	one, e := a.recognize(context.Background(), p, cfg, 0, track)
	if e != nil || len(one) != 1 {
		t.Fatal(one, e)
	}
	two, e := a.recognize(context.Background(), p, cfg, 5, track)
	if e != nil || len(two) != 2 || two[0].Start != 5 || two[0].End != 7 || two[0].Speaker != one[0].Speaker || two[1].Speaker == one[0].Speaker {
		t.Fatal(two, e)
	}
	three, e := a.recognize(context.Background(), p, cfg, 10, track)
	if e != nil || len(three) != 1 || three[0].Text != "clean" || three[0].Start != 10 || idx.Load() != 4 {
		t.Fatal(three, e)
	}
}
func TestRecognitionFailureRetainsJob(t *testing.T) {
	db, mid := testStore(t)
	dir := t.TempDir()
	pcm := bytes.Repeat([]byte{8, 1}, media.Rate)
	p := writePCM(t, dir, pcm)
	if e := db.Queue(mid, store.Job{Path: p, Start: 2, End: 3}, media.Peaks(pcm)); e != nil {
		t.Fatal(e)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, "private-error-body", 401) }))
	defer server.Close()
	t.Setenv("TINGSU_SILICONFLOW_URL", server.URL)
	failed := make(chan string, 1)
	a := New(db, dir, Callbacks{Failed: func(_ string, msg string) { failed <- msg }})
	defer a.Close()
	if e := a.Retry(mid, Config{Provider: "siliconflow", APIKey: "secret"}); e != nil {
		t.Fatal(e)
	}
	msg := await(t, failed)
	if !strings.Contains(msg, "401") || strings.Contains(msg, "secret") || strings.Contains(msg, "private-error-body") {
		t.Fatal("invalid error masking")
	}
	if db.PendingCount(mid) != 1 {
		t.Fatal("lost failed job")
	}
	if _, e := os.Stat(p); e != nil {
		t.Fatal(e)
	}
}

// Opt-in test: provide a 16 kHz mono PCM speech WAV and its provider credential.
func TestLiveProvider(t *testing.T) {
	path := os.Getenv("TINGSU_LIVE_ASR_WAV")
	if path == "" {
		t.Skip("requires a consented audio sample")
	}
	provider := os.Getenv("TINGSU_LIVE_PROVIDER")
	cfg, e := keyConfig(Config{Provider: provider, Model: os.Getenv("TINGSU_LIVE_MODEL")})
	if e != nil {
		t.Fatal(e)
	}
	r, e := defaultClient().recognize(context.Background(), path, cfg, 300, &speakerTracker{next: 1})
	if e != nil {
		t.Fatal(e)
	}
	if len(r) == 0 || r[0].Text == "" || r[0].Start < 300 {
		t.Fatal("no timestamped speech")
	}
	t.Logf("%s returned %d segments, first timestamp %.2f, last timestamp %.2f; diarization=%v", provider, len(r), r[0].Start, r[len(r)-1].End, r[0].Speaker != "")
}

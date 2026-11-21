package transcription

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math"
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
func TestDiscardPrevious(t *testing.T) {
	db, mid := testStore(t)
	root := t.TempDir()
	dir := filepath.Join(root, mid)
	os.Mkdir(dir, 0700)
	chunk := filepath.Join(dir, "audio-old.wav")
	other := filepath.Join(dir, "notes.txt")
	for _, p := range []string{chunk, other} {
		if err := os.WriteFile(p, []byte("fixture"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := db.Queue(mid, store.Job{Path: chunk, End: 2}); err != nil {
		t.Fatal(err)
	}
	service := New(db, root, Callbacks{})
	defer service.Close()
	if err := service.DiscardPrevious(); err != nil {
		t.Fatal(err)
	}
	if db.PendingCount(mid) != 0 {
		t.Fatal("stale job")
	}
	if _, err := os.Stat(chunk); !os.IsNotExist(err) {
		t.Fatal("stale WAV")
	}
	if _, err := os.Stat(other); err != nil {
		t.Fatal(err)
	}
}
func TestRecordStopsAndDrainsTailAfterEmptyFirstResult(t *testing.T) {
	s, mid := testStore(t)
	dir := t.TempDir()
	pcm := bytes.Repeat([]byte{4, 1}, media.Rate*15)
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
	a.capture = replayPCM
	if e := a.Record(mid, media.Source{ID: p}, Config{Provider: "siliconflow", APIKey: "fixture", Model: "fixture-asr"}, 12, 0); e != nil {
		t.Fatal(e)
	}
	await(t, first)
	// Wait until the producer has captured part of the second chunk, then stop.
	deadline := time.Now().Add(3 * time.Second)
	for {
		pos, _ := a.Position()
		if pos > 6.5 {
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
	if a.Status() != "idle" || s.PendingCount(mid) != 0 || len(rs) != 1 || rs[0]["at"] != float64(6) {
		t.Fatalf("tail %v", rs)
	}
	sizesMu.Lock()
	defer sizesMu.Unlock()
	if len(sizes) != 2 || sizes[0] != 6*bytesPerSecond || sizes[1] < bytesPerSecond/2 || sizes[1] >= 6*bytesPerSecond {
		t.Fatalf("chunk sizes %v", sizes)
	}
	if math.Abs(rs[0]["end"].(float64)-float64(sizes[0]+sizes[1])/bytesPerSecond) > 1e-9 {
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
func TestRecognitionFailureDiscardsJob(t *testing.T) {
	db, mid := testStore(t)
	dir := t.TempDir()
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if requests.Add(1) == 1 {
			http.Error(w, "private-error-body", 401)
			return
		}
		io.WriteString(w, `{"text":"new recording"}`)
	}))
	defer server.Close()
	t.Setenv("TINGSU_SILICONFLOW_URL", server.URL)
	failed := make(chan string, 1)
	done := make(chan string, 1)
	a := New(db, dir, Callbacks{Completed: func(id string) { done <- id }, Failed: func(_ string, msg string) { failed <- msg }})
	defer a.Close()
	a.capture = func(ctx context.Context, _ media.Source, dir string, chunk, offset float64, progress func(float64, float64), enqueue media.Enqueue) error {
		return media.CapturePCM(ctx, bytes.NewReader(bytes.Repeat([]byte{90, 8}, media.Rate*2)), dir, chunk, offset, progress, enqueue)
	}
	if err := a.Record(mid, media.Source{}, Config{Provider: "siliconflow", APIKey: "secret"}, 4, 0); err != nil {
		t.Fatal(err)
	}
	if msg := await(t, failed); !strings.Contains(msg, "401") || strings.Contains(msg, "secret") || strings.Contains(msg, "private-error-body") {
		t.Fatal("error masking")
	}
	deadline := time.Now().Add(5 * time.Second)
	for a.Status() != "idle" && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if db.PendingCount(mid) != 0 {
		t.Fatal("failed upload retained")
	}
	files, _ := filepath.Glob(filepath.Join(dir, mid, "*.wav"))
	if len(files) != 0 {
		t.Fatal(files)
	}
	if err := a.Record(mid, media.Source{}, Config{Provider: "siliconflow", APIKey: "replacement"}, 4, 0); err != nil {
		t.Fatal(err)
	}
	if await(t, done) != mid || requests.Load() != 2 {
		t.Fatal("new capture did not complete independently of the failed audio")
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

func TestCloseDuringCaptureDiscardsPartialWAVAcrossProcessRestart(t *testing.T) {
	dir := t.TempDir()
	dbPath := filepath.Join(dir, "db.sqlite3")
	db, e := store.Open(dbPath)
	if e != nil {
		t.Fatal(e)
	}
	mid, e := db.Create("capture recovery", "", "", 0, "", "")
	if e != nil {
		t.Fatal(e)
	}
	pcm := bytes.Repeat([]byte{12, 5}, media.Rate*7)
	input := writePCM(t, dir, pcm)
	cfg := Config{Provider: "siliconflow", APIKey: "fixture", Model: "fixture"}
	a := New(db, filepath.Join(dir, "audio"), Callbacks{})
	a.capture = replayPCM
	if e = a.Record(mid, media.Source{ID: input}, cfg, 12, 0); e != nil {
		t.Fatal(e)
	}
	deadline := time.Now().Add(4 * time.Second)
	for {
		pos, _ := a.Position()
		if pos >= .7 {
			break
		}
		if time.Now().After(deadline) {
			a.Close()
			t.Fatal("capture did not start")
		}
		time.Sleep(10 * time.Millisecond)
	}
	a.Close()
	if e = db.Close(); e != nil {
		t.Fatal(e)
	}
	db, e = store.Open(dbPath)
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	if db.PendingCount(mid) != 0 {
		t.Fatal("abandoned audio retained on restart")
	}
}

func TestSophnetMultipartAndPolledMillisecondTimestamps(t *testing.T) {
	dir := t.TempDir()
	p := writePCM(t, dir, bytes.Repeat([]byte{8, 2}, media.Rate*3))
	const model = "abcdefghijklmnopqrstuv"
	var gets atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer fixture" {
			t.Error("missing provider authorization")
		}
		switch r.Method {
		case "POST":
			if e := r.ParseMultipartForm(1 << 20); e != nil {
				t.Error(e)
				return
			}
			f, h, e := r.FormFile("audio_file")
			if e != nil {
				t.Error(e)
				return
			}
			f.Close()
			if h.Header.Get("Content-Type") != "audio/wav" {
				t.Error("audio MIME")
			}
			var params map[string]any
			if e = json.Unmarshal([]byte(r.FormValue("data")), &params); e != nil || params["easyllm_id"] != model {
				t.Error("deployment missing", e)
			}
			io.WriteString(w, `{"taskId":"task-1"}`)
		case "GET":
			if r.URL.Path != "/task-1" {
				t.Error("invalid task path")
			}
			if gets.Add(1) == 1 {
				io.WriteString(w, `{"status":"PROCESSING"}`)
			} else {
				io.WriteString(w, `{"status":"SUCCEEDED","results":[{"transcripts":[{"text":"早安","sentences":[{"begin_time":200,"end_time":2100,"text":"早安","speaker_id":"spk0"}]}]}]}`)
			}
		default:
			t.Error("unexpected method")
		}
	}))
	defer srv.Close()
	a := defaultClient()
	a.sophnet = srv.URL
	ts, e := a.recognize(context.Background(), p, Config{Provider: "sophnet", Model: model, APIKey: "fixture"}, 8, &speakerTracker{next: 4})
	if e != nil || len(ts) != 1 || ts[0].Text != "早安" || ts[0].Speaker != "发言人 04" || ts[0].Start != 8.2 || ts[0].End != 10.1 {
		t.Fatal(ts, e)
	}
}

func replayPCM(ctx context.Context, src media.Source, dir string, chunk, offset float64, position func(float64, float64), enqueue media.Enqueue) error {
	pcm, err := media.ReadWAV(src.ID)
	if err != nil {
		return err
	}
	r, w := io.Pipe()
	defer r.Close()
	go func() {
		defer w.Close()
		t := time.NewTicker(20 * time.Millisecond)
		defer t.Stop()
		for len(pcm) > 0 {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
			}
			n := min(len(pcm), media.Rate*2/50)
			if _, err := w.Write(pcm[:n]); err != nil {
				return
			}
			pcm = pcm[n:]
		}
	}()
	return media.CapturePCM(ctx, r, dir, chunk, offset, position, enqueue)
}

func TestMeterTracksPCMAndExpiresWithoutNewSamples(t *testing.T) {
	db, mid := testStore(t)
	s := New(db, t.TempDir(), Callbacks{})
	defer s.Close()
	report := make(chan func(float64, float64), 1)
	s.capture = func(ctx context.Context, _ media.Source, _ string, _, _ float64, position func(float64, float64), _ media.Enqueue) error {
		report <- position
		<-ctx.Done()
		return nil
	}
	if err := s.Record(mid, media.Source{}, Config{Provider: "siliconflow", APIKey: "fixture"}, 12, 2); err != nil {
		t.Fatal(err)
	}
	update := await(t, report)
	check := func(at, level float64, receiving bool) {
		t.Helper()
		seenAt, seenLevel, ok := s.Meter()
		if seenAt != at || seenLevel != level || ok != receiving {
			t.Fatalf("meter = %v, %v, %v; want %v, %v, %v", seenAt, seenLevel, ok, at, level, receiving)
		}
	}
	check(2, 0, false)
	update(2.1, 0.6)
	update(2.2, 0.2)
	check(2.2, 0.6, true)
	update(2.3, 0)
	check(2.3, 0, true)
	time.Sleep(600 * time.Millisecond)
	check(2.3, 0, false)
	s.Stop()
	check(0, 0, false)
}

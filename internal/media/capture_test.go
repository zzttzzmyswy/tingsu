package media

import (
	"bytes"
	"context"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"testing"
)

func TestCaptureShortFirstThenNormalChunksAndExactTail(t *testing.T) {
	if _, e := exec.LookPath("ffmpeg"); e != nil {
		t.Skip("requires ffmpeg")
	}
	dir := t.TempDir()
	n := Rate*19 + 31
	pcm := make([]byte, n*2)
	for i := range pcm {
		pcm[i] = byte(i*241 + 17)
	}
	input := filepath.Join(dir, "input.wav")
	f, e := os.Create(input)
	if e != nil {
		t.Fatal(e)
	}
	if e = WriteWAV(f, pcm); e != nil {
		t.Fatal(e)
	}
	f.Close()
	var got []byte
	var sizes []int
	clock := 0.
	e = Capture(context.Background(), Source{ID: input, Kind: "file"}, filepath.Join(dir, "chunks"), 12, 0, false, func(float64) {}, func(path string, a, b float64, peaks []float64) error {
		p, e := ReadWAV(path)
		if e != nil {
			return e
		}
		if a != clock || math.Abs(b-a-float64(len(p))/bytesPerSecond) > 1e-9 {
			t.Fatal("timestamp discontinuity")
		}
		clock = b
		if len(peaks) == 0 || peaks[0] <= 0 {
			t.Fatal("missing waveform")
		}
		sizes = append(sizes, len(p))
		got = append(got, p...)
		return nil
	})
	if e != nil {
		t.Fatal(e)
	}
	if want := []int{6 * bytesPerSecond, 12 * bytesPerSecond, bytesPerSecond + 62}; !slices.Equal(sizes, want) {
		t.Fatalf("chunks %v", sizes)
	}
	if !bytes.Equal(got, pcm) {
		t.Fatal("missing or changed PCM samples")
	}
}

func TestOpeningChoosesPauseAndBoundsContinuousSpeech(t *testing.T) {
	pcm := bytes.Repeat([]byte{0, 2}, Rate*6)
	for i := bytesPerSecond * 3; i < bytesPerSecond*4; i++ {
		pcm[i] = 0
	}
	c := openingCut{}
	for n := bytesPerSecond / 100; n <= len(pcm); n += bytesPerSecond / 100 {
		cut := c.boundary(pcm[:n], 12*bytesPerSecond)
		if cut == 0 {
			continue
		}
		if cut != bytesPerSecond*32/10 {
			t.Fatalf("first pause ends at %.3f, want 3.2s", float64(cut)/bytesPerSecond)
		}
		return
	}
	t.Fatal("opening phrase not emitted")
}

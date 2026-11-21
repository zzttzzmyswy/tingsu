package media

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"time"
)

const Rate = 16000
const bytesPerSecond = 2 * Rate
const FirstChunkSeconds = 3
const FirstChunkMaxSeconds = 6

// openingCut locates a short quiet spell after the first phrase. Checking each
// 20 ms window once allows real-time capture to respond as a pause occurs,
// while bounding latency during uninterrupted speech or an empty room.
type openingCut struct{ examined, firstVoice, quiet int }

func (c *openingCut) boundary(pcm []byte, steady int) int {
	const window = bytesPerSecond / 50
	limit := min(steady, FirstChunkMaxSeconds*bytesPerSecond)
	for c.examined+window <= min(len(pcm), limit) {
		start := c.examined
		c.examined += window
		var energy int64
		for i := start; i < c.examined; i += 2 {
			v := int64(int16(binary.LittleEndian.Uint16(pcm[i:])))
			energy += v * v
		}
		const quietRMS = 120
		if energy > int64(window/2)*quietRMS*quietRMS {
			if c.firstVoice == 0 {
				c.firstVoice = start + 1
			}
			c.quiet = 0
		} else {
			c.quiet += window
		}
		if c.examined >= FirstChunkSeconds*bytesPerSecond && c.firstVoice != 0 && c.examined-c.firstVoice >= 2*bytesPerSecond && c.quiet >= bytesPerSecond/5 {
			return c.examined
		}
	}
	if len(pcm) >= limit {
		return limit
	}
	return 0
}

func WriteWAV(w io.Writer, pcm []byte) error {
	var header [44]byte
	copy(header[:], "RIFF")
	binary.LittleEndian.PutUint32(header[4:], uint32(len(pcm)+36))
	copy(header[8:], "WAVEfmt ")
	binary.LittleEndian.PutUint32(header[16:], 16)
	binary.LittleEndian.PutUint16(header[20:], 1)
	binary.LittleEndian.PutUint16(header[22:], 1)
	binary.LittleEndian.PutUint32(header[24:], Rate)
	binary.LittleEndian.PutUint32(header[28:], bytesPerSecond)
	binary.LittleEndian.PutUint16(header[32:], 2)
	binary.LittleEndian.PutUint16(header[34:], 16)
	copy(header[36:], "data")
	binary.LittleEndian.PutUint32(header[40:], uint32(len(pcm)))
	if _, e := w.Write(header[:]); e != nil {
		return e
	}
	_, e := w.Write(pcm)
	return e
}

// ReadWAV accepts the mono PCM chunks emitted by the native capture pipeline.
func ReadWAV(path string) ([]byte, error) {
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	var h [12]byte
	if _, e = io.ReadFull(f, h[:]); e != nil || string(h[:4]) != "RIFF" || string(h[8:]) != "WAVE" {
		return nil, errors.New("无法读取 PCM WAV 音频分片")
	}
	var format bool
	for {
		var tag [8]byte
		if _, e = io.ReadFull(f, tag[:]); e != nil {
			return nil, e
		}
		n := binary.LittleEndian.Uint32(tag[4:])
		if n > 48_000_000 {
			return nil, errors.New("音频分片超过 48 MB")
		}
		switch string(tag[:4]) {
		case "fmt ":
			if n < 16 {
				return nil, errors.New("WAV 格式错误")
			}
			var fmt [16]byte
			if _, e = io.ReadFull(f, fmt[:]); e != nil {
				return nil, e
			}
			format = binary.LittleEndian.Uint16(fmt[:]) == 1 && binary.LittleEndian.Uint16(fmt[2:]) == 1 && binary.LittleEndian.Uint32(fmt[4:]) == Rate && binary.LittleEndian.Uint16(fmt[14:]) == 16
			n -= 16
		case "data":
			if !format || n%2 != 0 {
				return nil, errors.New("仅支持 16 kHz 单声道 16-bit PCM 分片")
			}
			b := make([]byte, n)
			_, e = io.ReadFull(f, b)
			return b, e
		}
		if _, e = f.Seek(int64(n+n%2), io.SeekCurrent); e != nil {
			return nil, e
		}
	}
}
func Peaks(pcm []byte) []float64 {
	p := make([]float64, (len(pcm)+bytesPerSecond-1)/bytesPerSecond)
	for i := 0; i+1 < len(pcm); i += 2 {
		v := math.Abs(float64(int16(binary.LittleEndian.Uint16(pcm[i:])))) / 32768
		if v > p[i/bytesPerSecond] {
			p[i/bytesPerSecond] = v
		}
	}
	return p
}
func writeChunk(dir string, pcm []byte) (string, error) {
	f, e := os.CreateTemp(dir, "audio-*.wav")
	if e != nil {
		return "", e
	}
	name := f.Name()
	if e = WriteWAV(f, pcm); e == nil {
		e = f.Sync()
	}
	closeErr := f.Close()
	if e == nil {
		e = closeErr
	}
	if e != nil {
		os.Remove(name)
		return "", e
	}
	return name, nil
}

// Capture uses media time for files and preserves pauses in live streams. A
// stopped producer flushes complete PCM samples before returning to the caller.
func Capture(ctx context.Context, source Source, dir string, chunk, offset float64, realtime bool, position func(float64), enqueue func(path string, start, end float64, peaks []float64) error) error {
	if math.IsNaN(chunk) || math.IsInf(chunk, 0) || chunk < .05 || chunk > 600 || math.IsNaN(offset) || math.IsInf(offset, 0) || offset < 0 {
		return errors.New("音频分片长度或起始时间无效")
	}
	if e := os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	child, stop := context.WithCancel(ctx)
	defer stop()
	cmd, e := audioCommand(child, source, offset, realtime)
	if e != nil {
		return e
	}
	pipe, e := cmd.StdoutPipe()
	if e != nil {
		return e
	}
	var stderr limitedBuffer
	cmd.Stderr = &stderr
	if e = cmd.Start(); e != nil {
		return e
	}
	const sampleRate = float64(bytesPerSecond)
	opening := &openingCut{}
	steady := int(math.Round(chunk*Rate)) * 2
	pending := []byte{}
	cursor := offset
	epoch := time.Now()
	flush := func(n int) error {
		if n == 0 {
			return nil
		}
		part := pending[:n]
		path, e := writeChunk(dir, part)
		if e != nil {
			return e
		}
		end := cursor + float64(n)/sampleRate
		if e = enqueue(path, cursor, end, Peaks(part)); e != nil {
			return e
		}
		pending = pending[n:]
		cursor = end
		opening = nil
		return nil
	}
	block := make([]byte, 32*1024)
	var readErr error
	for {
		before := time.Now()
		n, err := pipe.Read(block)
		if n > 0 {
			if source.Kind != "file" && time.Since(before) >= 150*time.Millisecond {
				arrival := offset + time.Since(epoch).Seconds()
				expected := cursor + float64(len(pending)+n)/sampleRate
				if arrival-expected > .8 {
					if e = flush(len(pending) &^ 1); e != nil {
						break
					}
					cursor = arrival - float64(len(pending)+n)/sampleRate
				}
			}
			pending = append(pending, block[:n]...)
			position(cursor + float64(len(pending))/sampleRate)
			for {
				target := steady
				if opening != nil {
					target = opening.boundary(pending, steady)
				}
				if target == 0 || len(pending) < target {
					break
				}
				if e = flush(target); e != nil {
					break
				}
			}
			if e != nil {
				break
			}
		}
		if err != nil {
			if !errors.Is(err, io.EOF) {
				readErr = err
			}
			break
		}
	}
	if e == nil {
		e = flush(len(pending) &^ 1)
	} else {
		stop()
	}
	waitErr := cmd.Wait()
	if e != nil {
		return e
	}
	if ctx.Err() != nil {
		return nil
	}
	if readErr != nil {
		return readErr
	}
	if waitErr != nil || source.Kind != "file" {
		detail := stderr.String()
		if detail == "" {
			detail = "音源已断开，请刷新音源并重新连接"
		}
		return fmt.Errorf("采集音频失败：%s", detail)
	}
	return nil
}

type limitedBuffer struct{ b bytes.Buffer }

func (w *limitedBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if len(p) > 2048 {
		p = p[len(p)-2048:]
	}
	w.b.Write(p)
	if w.b.Len() > 2048 {
		w.b.Next(w.b.Len() - 2048)
	}
	return n, nil
}
func (w *limitedBuffer) String() string { return w.b.String() }

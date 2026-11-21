package transcription

import (
	"fmt"
	"github.com/zzttzzmyswy/tingsu/internal/media"
	"github.com/zzttzzmyswy/tingsu/internal/store"
	"math"
	"slices"
)

type voice struct {
	name string
	pcm  []byte
}
type reference struct {
	start, end float64
	name       string
}
type speakerTracker struct {
	next   int
	voices []voice
}

func (t *speakerTracker) prepare(pcm []byte) ([]byte, []reference, float64) {
	if len(t.voices) == 0 {
		return pcm, nil, 0
	}
	var upload []byte
	var refs []reference
	silence := make([]byte, bytesPerSecond)
	for _, v := range t.voices {
		start := seconds(upload)
		upload = append(upload, v.pcm...)
		refs = append(refs, reference{start, seconds(upload), v.name})
		upload = append(upload, silence...)
	}
	return append(upload, pcm...), refs, seconds(upload)
}
func (t *speakerTracker) assign(pcm []byte, turns []store.Segment, refs []reference, prefix float64) []store.Segment {
	// Cross-request labels are reusable only when prior voice clips give an unambiguous match in both directions.
	linked := map[string]map[string]bool{}
	reverse := map[string]map[string]bool{}
	for _, s := range turns {
		if s.Speaker == "" || s.Start >= prefix {
			continue
		}
		for _, r := range refs {
			overlap := min(r.end, s.End) - max(r.start, s.Start)
			if overlap < min(.65, (r.end-r.start)/2) {
				continue
			}
			if linked[s.Speaker] == nil {
				linked[s.Speaker] = map[string]bool{}
			}
			if reverse[r.name] == nil {
				reverse[r.name] = map[string]bool{}
			}
			linked[s.Speaker][r.name] = true
			reverse[r.name][s.Speaker] = true
		}
	}
	names := map[string]string{}
	for k, v := range linked {
		if len(v) == 1 {
			for name := range v {
				if len(reverse[name]) == 1 {
					names[k] = name
				}
			}
		}
	}
	best := map[string]store.Segment{}
	var order []string
	out := []store.Segment{}
	for _, s := range turns {
		if s.End <= prefix {
			continue
		}
		s.Start = max(0., s.Start-prefix)
		s.End = min(seconds(pcm), s.End-prefix)
		if s.End <= s.Start {
			continue
		}
		if local := s.Speaker; local != "" {
			name := names[local]
			if name == "" {
				name = fmt.Sprintf("发言人 %02d", t.next)
				t.next++
				names[local] = name
			}
			s.Speaker = name
			old, ok := best[name]
			if !ok {
				order = append(order, name)
			}
			if s.End-s.Start > old.End-old.Start {
				best[name] = s
			}
		}
		out = append(out, s)
	}
	for _, name := range order {
		s := best[name]
		length := s.End - s.Start
		if length < 1.25 {
			continue
		}
		a := s.Start + max(0., (length-7)/2)
		b := min(s.End, a+7)
		from := int(math.Round(a*media.Rate)) * 2
		to := min(len(pcm), int(math.Round(b*media.Rate))*2)
		clip := pcm[from:to]
		for i, v := range t.voices {
			if v.name == name {
				if float64(len(clip)) < float64(len(v.pcm))*.9 {
					clip = v.pcm
				}
				t.voices = slices.Delete(t.voices, i, i+1)
				break
			}
		}
		t.voices = append(t.voices, voice{name, slices.Clone(clip)})
		if len(t.voices) > 4 {
			t.voices = slices.Delete(t.voices, 0, 1)
		}
	}
	return out
}

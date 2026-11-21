package transcript

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParse(t *testing.T) {
	cases := []struct {
		name, data string
		cues       []Cue
	}{
		{"session.SRT", "\ufeff1\r\n00:01:02,050 --> 00:01:03,500\r\n张三：第一行\r\n第二行\r\n", []Cue{{62.05, 63.5, true, "张三", "第一行\n第二行"}}},
		{"review.vtt", "WEBVTT\n\nNOTE metadata\nnot speech\n\nslide\n01:42.125 --> 01:44.000 align:start\n<v 小王>报告 &amp; 数据</v>\n", []Cue{{102.125, 104, true, "小王", "报告 & 数据"}}},
		{"notes.txt", "李：无时间\n[02:10] Amy: Review\n", []Cue{{0, 0, false, "李", "无时间"}, {130, 130, true, "Amy", "Review"}}},
	}
	for _, c := range cases {
		r, e := Parse(c.name, c.data)
		if e != nil || len(r.Cues) != len(c.cues) {
			t.Fatalf("%s: %v %v", c.name, r, e)
		}
		for i, w := range c.cues {
			if r.Cues[i] != w {
				t.Fatalf("%s: got %#v, want %#v", c.name, r.Cues[i], w)
			}
		}
	}
	for _, s := range []string{"", "1\n00:00:03,000 --> 00:00:01,000\nfoo", "1\n00:00:01,000 --> ?\nfoo"} {
		if _, e := Parse("input.srt", s); e == nil {
			t.Fatalf("accepted %q", s)
		}
	}
}
func TestUTF16File(t *testing.T) {
	p := filepath.Join(t.TempDir(), "log.txt")
	if err := os.WriteFile(p, []byte{0xff, 0xfe, 0x2d, 0x4e}, 0600); err != nil {
		t.Fatal(err)
	}
	d, err := Read(p)
	if err != nil || d.Cues[0].Text != "中" {
		t.Fatal(d, err)
	}
}

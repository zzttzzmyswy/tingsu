// Package transcript reads user-selected meeting transcripts without running ASR.
package transcript

import (
	"encoding/binary"
	"fmt"
	"html"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

type Cue struct {
	At, End       float64
	Timed         bool
	Speaker, Text string
}
type Document struct {
	Title string
	Cues  []Cue
}

var timing = regexp.MustCompile(`^(\S+)\s+-->\s+(\S+)(?:\s+.*)?$`)
var timestamp = regexp.MustCompile(`^(?:(\d+):)?(\d{1,2}):([0-5]\d)(?:[.,](\d{1,3}))?$`)
var txtTime = regexp.MustCompile(`^\[?((?:\d+:)?\d{1,2}:[0-5]\d(?:[.,]\d{1,3})?)\]?(?:\s+|$)(.*)$`)
var voice = regexp.MustCompile(`(?i)^<v(?:\.[^ >]+)?\s+([^>]+)>`)
var markup = regexp.MustCompile(`(?i)</?(?:b|i|u|ruby|rt|v|c|lang)(?:[.\s][^>]*)?>|<\d[0-9:.]*>`)
var speakerPrefix = regexp.MustCompile(`(?s)^([^：:\n]{1,32})[：:]\s*(.+)$`)

func seconds(s string) (float64, bool) {
	m := timestamp.FindStringSubmatch(s)
	if m == nil {
		return 0, false
	}
	h, _ := strconv.Atoi(m[1])
	min, _ := strconv.Atoi(m[2])
	sec, _ := strconv.Atoi(m[3])
	frac, _ := strconv.Atoi((m[4] + "000")[:3])
	return float64(h*3600+min*60+sec) + float64(frac)/1000, m[1] == "" || min < 60
}
func Read(path string) (Document, error) {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".txt", ".srt", ".vtt":
	default:
		return Document{}, fmt.Errorf("请选择 TXT、SRT 或 VTT 逐字稿")
	}
	info, err := os.Stat(path)
	if err != nil {
		return Document{}, err
	}
	if !info.Mode().IsRegular() {
		return Document{}, fmt.Errorf("请选择普通文本文件")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return Document{}, err
	}
	s := string(raw)
	if len(raw) >= 2 && ((raw[0] == 0xff && raw[1] == 0xfe) || (raw[0] == 0xfe && raw[1] == 0xff)) {
		if len(raw)%2 != 0 {
			return Document{}, fmt.Errorf("UTF-16 文本不完整")
		}
		var order binary.ByteOrder = binary.LittleEndian
		if raw[0] == 0xfe {
			order = binary.BigEndian
		}
		r := make([]uint16, 0, len(raw)/2)
		for i := 2; i < len(raw); i += 2 {
			r = append(r, order.Uint16(raw[i:]))
		}
		s = string(utf16.Decode(r))
	}
	if !utf8.ValidString(s) || strings.ContainsRune(s, 0) {
		return Document{}, fmt.Errorf("请使用 UTF-8 或含 BOM 的 UTF-16 逐字稿")
	}
	return Parse(filepath.Base(path), s)
}
func Parse(name, s string) (Document, error) {
	ext := strings.ToLower(filepath.Ext(name))
	d := Document{Title: strings.TrimSuffix(name, filepath.Ext(name))}
	s = strings.TrimPrefix(s, "\uFEFF")
	s = strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n")
	lines := strings.Split(s, "\n")
	switch ext {
	case ".txt":
		var last float64
		for _, line := range lines {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			c := Cue{At: last, End: last, Speaker: "逐字稿"}
			if m := txtTime.FindStringSubmatch(line); m != nil {
				if t, ok := seconds(m[1]); ok {
					c.At = t
					c.End = t
					c.Timed = true
					last = t
					line = m[2]
				}
			}
			if m := speakerPrefix.FindStringSubmatch(line); m != nil {
				c.Speaker = strings.TrimSpace(m[1])
				line = m[2]
			}
			c.Text = strings.TrimSpace(line)
			if c.Text != "" {
				d.Cues = append(d.Cues, c)
			}
		}
	case ".srt", ".vtt":
		for i := 0; i < len(lines); {
			start := i
			for i < len(lines) && strings.TrimSpace(lines[i]) != "" {
				i++
			}
			block := lines[start:i]
			i++
			if len(block) == 0 {
				continue
			}
			first := strings.TrimSpace(block[0])
			if ext == ".vtt" && (strings.HasPrefix(first, "WEBVTT") || first == "NOTE" || strings.HasPrefix(first, "NOTE ") || first == "STYLE" || first == "REGION") {
				continue
			}
			if !strings.Contains(block[0], "-->") {
				block = block[1:]
			}
			if len(block) < 2 {
				return d, fmt.Errorf("第 %d 行附近缺少字幕时间或正文", start+1)
			}
			m := timing.FindStringSubmatch(strings.TrimSpace(block[0]))
			if m == nil {
				return d, fmt.Errorf("第 %d 行附近的字幕时间无效", start+1)
			}
			a, ok := seconds(m[1])
			b, ok2 := seconds(m[2])
			if !ok || !ok2 || b < a {
				return d, fmt.Errorf("第 %d 行附近的时间范围无效", start+1)
			}
			body := strings.TrimSpace(strings.Join(block[1:], "\n"))
			who := "逐字稿"
			if v := voice.FindStringSubmatch(body); v != nil {
				who = html.UnescapeString(v[1])
				body = body[len(v[0]):]
			} else if v := speakerPrefix.FindStringSubmatch(body); v != nil {
				who = strings.TrimSpace(v[1])
				body = v[2]
			}
			body = strings.TrimSpace(html.UnescapeString(markup.ReplaceAllString(body, "")))
			if body != "" {
				d.Cues = append(d.Cues, Cue{At: a, End: b, Timed: true, Speaker: who, Text: body})
			}
		}
	default:
		return d, fmt.Errorf("请选择 TXT、SRT 或 VTT 逐字稿")
	}
	if len(d.Cues) == 0 {
		return d, fmt.Errorf("逐字稿没有可导入的发言")
	}
	return d, nil
}

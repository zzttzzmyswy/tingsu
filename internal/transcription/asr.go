// Package transcription captures and recognizes meeting audio in timestamp order.
package transcription

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/zzttzzmyswy/tingsu/internal/media"
	"github.com/zzttzzmyswy/tingsu/internal/store"
)

type Config struct{ Provider, APIKey, Model string }

const diarize = "XingChenAGI/XingChenASR-Diarize-V3.0"
const siliconURL = "https://api.siliconflow.cn/v1/audio/transcriptions"
const sophnetURL = "https://www.sophnet.com/api/open-apis/projects/easyllms/speechtotext/transcriptions"

var deployment = regexp.MustCompile(`^[A-Za-z0-9]{22}$`)

type asrClient struct {
	client           *http.Client
	silicon, sophnet string
}

func defaultClient() *asrClient {
	endpoint := os.Getenv("TINGSU_SILICONFLOW_URL")
	if endpoint == "" {
		endpoint = siliconURL
	}
	return &asrClient{&http.Client{Timeout: 45 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, endpoint, sophnetURL}
}
func wait(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func (a *asrClient) request(ctx context.Context, method, url, key, ct string, data []byte) (map[string]any, error) {
	for attempt := 0; attempt < 2; attempt++ {
		req, e := http.NewRequestWithContext(ctx, method, url, bytes.NewReader(data))
		if e != nil {
			return nil, e
		}
		req.Header.Set("Authorization", "Bearer "+key)
		req.Header.Set("Accept", "application/json")
		if ct != "" {
			req.Header.Set("Content-Type", ct)
		}
		res, e := a.client.Do(req)
		if e != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, errors.New("无法连接语音识别服务，请检查网络或稍后重试")
		}
		if attempt == 0 && (res.StatusCode == 429 || res.StatusCode == 503) {
			res.Body.Close()
			delay, e := strconv.ParseFloat(res.Header.Get("Retry-After"), 64)
			if e != nil || math.IsNaN(delay) {
				delay = 1
			}
			if e = wait(ctx, time.Duration(min(3., max(.5, delay))*float64(time.Second))); e != nil {
				return nil, e
			}
			continue
		}
		if res.StatusCode < 200 || res.StatusCode >= 300 {
			res.Body.Close()
			message := map[int]string{400: "请求参数或模型有误", 401: "密钥无效", 402: "账户余额不足", 403: "无权访问此模型", 404: "项目或模型不存在", 422: "服务无法处理音频或模型参数", 429: "请求频率或配额已达上限"}[res.StatusCode]
			if message == "" {
				message = "服务请求失败"
			}
			return nil, fmt.Errorf("语音识别%s（HTTP %d）", message, res.StatusCode)
		}
		var r map[string]any
		dec := json.NewDecoder(io.LimitReader(res.Body, 16<<20))
		dec.UseNumber()
		e = dec.Decode(&r)
		res.Body.Close()
		if e != nil || r == nil {
			return nil, errors.New("语音识别服务返回了无法解析的响应")
		}
		return r, nil
	}
	return nil, errors.New("语音识别服务暂不可用")
}
func (a *asrClient) result(ctx context.Context, pcm []byte, cfg Config) (map[string]any, error) {
	var body bytes.Buffer
	w := multipart.NewWriter(&body)
	endpoint, field := a.silicon, "file"
	if cfg.Provider == "siliconflow" {
		w.WriteField("model", cfg.Model)
		w.WriteField("response_format", "verbose_json")
	} else {
		endpoint, field = a.sophnet, "audio_file"
		m := cfg.Model
		params := map[string]any{}
		if deployment.MatchString(m) {
			params["easyllm_id"] = m
			m = "Fun-ASR"
		}
		params["speech_recognition_param"] = map[string]any{"model": m, "diarization_enabled": true}
		h := textproto.MIMEHeader{}
		h.Set("Content-Disposition", `form-data; name="data"`)
		h.Set("Content-Type", "application/json")
		part, e := w.CreatePart(h)
		if e != nil {
			return nil, e
		}
		if e = json.NewEncoder(part).Encode(params); e != nil {
			return nil, e
		}
	}
	h := textproto.MIMEHeader{}
	h.Set("Content-Disposition", fmt.Sprintf(`form-data; name="%s"; filename="chunk.wav"`, field))
	h.Set("Content-Type", "audio/wav")
	part, e := w.CreatePart(h)
	if e != nil {
		return nil, e
	}
	if e = media.WriteWAV(part, pcm); e != nil {
		return nil, e
	}
	if e = w.Close(); e != nil {
		return nil, e
	}
	r, e := a.request(ctx, http.MethodPost, endpoint, cfg.APIKey, w.FormDataContentType(), body.Bytes())
	if e != nil || cfg.Provider != "sophnet" {
		return r, e
	}
	task := asString(r["taskId"])
	if task == "" {
		task = asString(r["task_id"])
	}
	if task == "" {
		return r, nil
	}
	poll, cancel := context.WithTimeout(ctx, 75*time.Second)
	defer cancel()
	endpoint += "/" + url.PathEscape(task)
	for {
		if e := wait(poll, time.Second); e != nil {
			return nil, fmt.Errorf("等待 Sophnet 转写任务：%w", e)
		}
		r, e = a.request(poll, http.MethodGet, endpoint, cfg.APIKey, "", nil)
		if e != nil {
			return nil, e
		}
		switch strings.ToUpper(asString(r["status"])) {
		case "SUCCEEDED", "SUCCESS", "COMPLETED":
			return r, nil
		case "FAILED", "CANCELED", "CANCELLED", "ERROR":
			return nil, errors.New("Sophnet 未能完成此次转写")
		case "PENDING", "RUNNING", "WAITING", "DOING", "QUEUED", "PROCESSING":
		default:
			return nil, errors.New("Sophnet 返回了无法识别的任务状态")
		}
	}
}
func asString(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
func timed(v any, dur float64, ms bool) (store.Segment, bool) {
	r, ok := v.(map[string]any)
	if !ok {
		return store.Segment{}, false
	}
	txt, _ := r["text"].(string)
	txt = strings.TrimSpace(txt)
	a, b := "start", "end"
	if ms {
		a, b = "begin_time", "end_time"
	}
	st, e := strconv.ParseFloat(asString(r[a]), 64)
	end, ee := strconv.ParseFloat(asString(r[b]), 64)
	if e != nil || ee != nil || math.IsNaN(st) || math.IsNaN(end) || math.IsInf(st, 0) || math.IsInf(end, 0) {
		return store.Segment{}, false
	}
	if ms {
		st /= 1000
		end /= 1000
	}
	st = max(0., st)
	end = min(dur, end)
	sp := r["speaker"]
	if sp == nil {
		sp = r["speaker_id"]
	}
	return store.Segment{Start: st, End: end, Speaker: strings.TrimSpace(asString(sp)), Text: txt}, txt != "" && end > st
}
func decode(r map[string]any, dur float64) ([]store.Segment, bool, error) {
	turns := []store.Segment{}
	if rows, ok := r["segments"].([]any); ok && len(rows) > 0 {
		all := true
		for _, x := range rows {
			v, ok := timed(x, dur, false)
			if !ok {
				all = false
				break
			}
			turns = append(turns, v)
		}
		if all {
			return turns, true, nil
		}
	}
	var text string
	var ok bool
	text, ok = r["text"].(string)
	if !ok {
		text, ok = r["result"].(string)
	}
	if results, has := r["results"].([]any); has {
		turns = nil
		parts := []string{}
		all := true
		for _, item := range results {
			x, good := item.(map[string]any)
			if !good {
				return nil, false, errors.New("Sophnet 返回了无法解析的结果")
			}
			if strings.EqualFold(asString(x["subtask_status"]), "FAILED") {
				return nil, false, errors.New("Sophnet 未能识别音频分片")
			}
			rows, _ := x["transcripts"].([]any)
			for _, raw := range rows {
				t, good := raw.(map[string]any)
				if !good {
					return nil, false, errors.New("Sophnet 返回了无法解析的转写")
				}
				text, _ := t["text"].(string)
				parts = append(parts, text)
				sentences, _ := t["sentences"].([]any)
				if len(sentences) == 0 && text != "" {
					all = false
				}
				for _, sentence := range sentences {
					if s, good := timed(sentence, dur, true); good {
						turns = append(turns, s)
					} else {
						all = false
					}
				}
			}
		}
		if all && len(turns) > 0 {
			return turns, true, nil
		}
		text = strings.Join(parts, "\n")
		ok = true
	}
	if !ok {
		return nil, false, errors.New("语音识别服务未返回可读取的转写结果")
	}
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, false, nil
	}
	return []store.Segment{{Start: 0, End: dur, Text: text}}, false, nil
}

const bytesPerSecond = media.Rate * 2

func seconds(p []byte) float64 { return float64(len(p)) / bytesPerSecond }
func keyConfig(cfg Config) (Config, error) {
	cfg.Provider = strings.ToLower(strings.TrimSpace(cfg.Provider))
	if cfg.Provider != "siliconflow" && cfg.Provider != "sophnet" {
		return cfg, errors.New("未知的语音识别供应商")
	}
	if cfg.APIKey = strings.TrimSpace(cfg.APIKey); cfg.APIKey == "" {
		cfg.APIKey = strings.TrimSpace(os.Getenv(strings.ToUpper(cfg.Provider) + "_API_KEY"))
	}
	if cfg.APIKey == "" {
		return cfg, errors.New("请先在设置中填写转写供应商的 API 密钥")
	}
	if cfg.Model = strings.TrimSpace(cfg.Model); cfg.Model == "" {
		cfg.Model = diarize
		if cfg.Provider == "sophnet" {
			cfg.Model = "Fun-ASR"
		}
	}
	return cfg, nil
}
func (a *asrClient) recognize(ctx context.Context, path string, cfg Config, offset float64, tracker *speakerTracker) ([]store.Segment, error) {
	pcm, e := media.ReadWAV(path)
	if e != nil {
		return nil, e
	}
	silent := true
	for i := 0; i+1 < len(pcm); i += 2 {
		v := int16(binary.LittleEndian.Uint16(pcm[i:]))
		if v > 8 || v < -8 {
			silent = false
			break
		}
	}
	if silent {
		return nil, nil
	}
	upload := pcm
	var refs []reference
	var prefix float64
	track := tracker != nil && (cfg.Provider == "sophnet" || cfg.Model == diarize)
	if track {
		upload, refs, prefix = tracker.prepare(pcm)
	}
	request := func(p []byte) ([]store.Segment, bool, error) {
		r, e := a.result(ctx, p, cfg)
		if e != nil {
			return nil, false, e
		}
		return decode(r, seconds(p))
	}
	ts, hasTime, e := request(upload)
	if e != nil {
		return nil, e
	}
	if prefix > 0 && len(ts) > 0 {
		merged := !hasTime
		for _, t := range ts {
			if t.Start < prefix-.25 && t.End > prefix {
				merged = true
				break
			}
		}
		if merged {
			ts, _, e = request(pcm)
			if e != nil {
				return nil, e
			}
			refs = nil
			prefix = 0
		}
	}
	if track {
		ts = tracker.assign(pcm, ts, refs, prefix)
	} else {
		for i := range ts {
			if ts[i].Speaker != "" {
				ts[i].Speaker = "发言人 " + ts[i].Speaker
			}
		}
	}
	for i := range ts {
		ts[i].Start += offset
		ts[i].End += offset
	}
	return ts, nil
}

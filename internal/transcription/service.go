package transcription

import (
	"context"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sync"

	"github.com/zzttzzmyswy/tingsu/internal/media"
	"github.com/zzttzzmyswy/tingsu/internal/store"
)

type Callbacks struct {
	Changed     func()
	Transcribed func(mid string, end float64, hasSpeech bool)
	Failed      func(mid, message string)
	Completed   func(mid string)
}
type run struct {
	mid         string
	config      Config
	tracker     *speakerTracker
	captureStop context.CancelFunc
	signal      chan struct{}
	done        chan struct{}
	// Fields below are protected by Service.mu.
	draining, ended, inputFailed, hasPosition bool
	position                                  float64
}
type Service struct {
	db         *store.Store
	root       string
	cb         Callbacks
	client     *asrClient
	mu         sync.Mutex
	ctx        context.Context
	cancel     context.CancelFunc
	active     *run
	tracker    *speakerTracker
	trackerKey [3]string
}

func New(db *store.Store, audioRoot string, cb Callbacks) *Service {
	ctx, cancel := context.WithCancel(context.Background())
	return &Service{db: db, root: audioRoot, cb: cb, client: defaultClient(), ctx: ctx, cancel: cancel}
}
func (s *Service) changed() {
	if s.cb.Changed != nil {
		s.cb.Changed()
	}
}
func (s *Service) failed(mid string, e error) {
	if s.ctx.Err() == nil && s.cb.Failed != nil {
		s.cb.Failed(mid, e.Error())
	}
}
func (s *Service) Status() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.active == nil {
		return "idle"
	}
	if s.active.draining {
		return "draining"
	}
	return "recording"
}
func (s *Service) Position() (float64, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.active != nil && s.active.hasPosition {
		return s.active.position, true
	}
	return 0, false
}
func (s *Service) idle() error {
	if s.ctx.Err() != nil {
		return errors.New("转写服务已关闭")
	}
	if s.active != nil {
		return errors.New("请先停止采集，并等待已录音频完成转写")
	}
	return nil
}
func (s *Service) EnsureIdle() error { s.mu.Lock(); defer s.mu.Unlock(); return s.idle() }
func (s *Service) ForgetSpeakers() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e := s.idle(); e != nil {
		return e
	}
	s.tracker = nil
	return nil
}
func (s *Service) start(mid string, cfg Config) (*run, error) {
	if e := s.idle(); e != nil {
		return nil, e
	}
	cfg, e := keyConfig(cfg)
	if e != nil {
		return nil, e
	}
	key := [3]string{mid, cfg.Provider, cfg.Model}
	if s.tracker == nil || key != s.trackerKey {
		next := 1
		for _, name := range s.db.SpeechSpeakers(mid) {
			var num int
			if _, e := fmt.Sscanf(name, "发言人 %d", &num); e == nil && num >= next {
				next = num + 1
			}
		}
		s.tracker = &speakerTracker{next: next}
		s.trackerKey = key
	}
	r := &run{mid: mid, config: cfg, tracker: s.tracker, signal: make(chan struct{}, 1), done: make(chan struct{})}
	s.active = r
	return r, nil
}
func (r *run) wake() {
	select {
	case r.signal <- struct{}{}:
	default:
	}
}
func (s *Service) Record(mid string, src media.Source, cfg Config, chunkSeconds, startSeconds float64) error {
	if math.IsNaN(chunkSeconds) || math.IsInf(chunkSeconds, 0) || chunkSeconds < .05 || chunkSeconds > 600 || math.IsInf(startSeconds, 0) || math.IsNaN(startSeconds) || startSeconds < 0 {
		return errors.New("音频分片长度或起始时间无效")
	}
	s.mu.Lock()
	if s.db.PendingCount(mid) > 0 {
		s.mu.Unlock()
		return errors.New("请先重试尚未转写的音频")
	}
	r, e := s.start(mid, cfg)
	if e != nil {
		s.mu.Unlock()
		return e
	}
	recordCtx, cancel := context.WithCancel(s.ctx)
	r.captureStop = cancel
	r.position = startSeconds
	r.hasPosition = true
	s.mu.Unlock()
	go s.consume(r)
	go func() {
		defer cancel()
		dir := filepath.Join(s.root, mid)
		err := media.Capture(recordCtx, src, dir, chunkSeconds, startSeconds, true, func(pos float64) { s.mu.Lock(); r.position = pos; s.mu.Unlock() }, func(path string, start, end float64, peaks []float64) error {
			if e := s.db.Queue(mid, store.Job{Path: path, Start: start, End: end}, peaks); e != nil {
				return e
			}
			r.wake()
			s.changed()
			return nil
		})
		if err != nil {
			s.failed(mid, err)
		}
		s.mu.Lock()
		r.inputFailed = err != nil
		r.ended = true
		r.draining = true
		s.mu.Unlock()
		r.wake()
		s.changed()
	}()
	s.changed()
	return nil
}
func (s *Service) Retry(mid string, cfg Config) error {
	s.mu.Lock()
	r, e := s.start(mid, cfg)
	if e != nil {
		s.mu.Unlock()
		return e
	}
	r.ended = true
	r.draining = true
	s.mu.Unlock()
	go s.consume(r)
	s.changed()
	return nil
}
func (s *Service) consume(r *run) {
	// The database is the queue. A single buffered wake-up is sufficient even if
	// recognition falls behind for hours; audio lives in files rather than RAM.
	var failed bool
	defer close(r.done)
	for {
		s.mu.Lock()
		ended := r.ended
		s.mu.Unlock()
		if !failed && s.ctx.Err() == nil {
			jobs, e := s.db.PendingJobs(r.mid)
			if e == nil && len(jobs) > 0 {
				job := jobs[0]
				var segments []store.Segment
				segments, e = s.client.recognize(s.ctx, job.Path, r.config, job.Start, r.tracker)
				if e == nil && s.ctx.Err() == nil {
					e = s.db.FinishJob(r.mid, job.Path, segments)
					if e == nil {
						if err := os.Remove(job.Path); err != nil && !os.IsNotExist(err) {
							s.failed(r.mid, fmt.Errorf("转写已保存，清理临时音频失败：%w", err))
						}
						if s.cb.Transcribed != nil {
							s.cb.Transcribed(r.mid, job.End, len(segments) > 0)
						}
						s.changed()
						continue
					}
				}
			}
			if e != nil {
				failed = true
				if r.captureStop != nil {
					r.captureStop()
				}
				s.failed(r.mid, e)
			}
		}
		if ended {
			break
		}
		<-r.signal
	}
	s.mu.Lock()
	s.active = nil
	inputFailed := r.inputFailed
	s.mu.Unlock()
	s.changed()
	if !failed && !inputFailed && s.ctx.Err() == nil && s.cb.Completed != nil {
		s.cb.Completed(r.mid)
	}
}
func (s *Service) Stop() {
	s.mu.Lock()
	r := s.active
	if r == nil || r.draining || r.captureStop == nil {
		s.mu.Unlock()
		return
	}
	r.draining = true
	r.captureStop()
	s.mu.Unlock()
	s.changed()
}
func (s *Service) Close() {
	s.mu.Lock()
	s.cancel()
	r := s.active
	if r != nil && r.captureStop != nil {
		r.captureStop()
	}
	s.mu.Unlock()
	if r != nil {
		r.wake()
		<-r.done
	}
}

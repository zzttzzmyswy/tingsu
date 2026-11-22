package agent

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestSharedFrameReferencesAndExplicitImages(t *testing.T) {
	root := t.TempDir()
	lib := library{root, "meeting"}
	frame := filepath.Join(root, "screen.png")
	file := filepath.Join(root, "用户选择.png")
	if err := os.WriteFile(frame, []byte("one"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(file, []byte("two"), 0600); err != nil {
		t.Fatal(err)
	}
	files, err := lib.stageAll(context.Background(), []string{file})
	if err != nil {
		t.Fatal(err)
	}
	in, key, err := lib.blocks("codex", "分析", files, frame, M{}, "")
	if err != nil || len(in) != 3 || key == "" {
		t.Fatal(len(in), key, err)
	}
	in, next, err := lib.blocks("codex", "再看", files, frame, M{}, key)
	if err != nil || next != key || len(in) != 2 {
		t.Fatal(len(in), next, err)
	}
	if err := os.WriteFile(frame, []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	in, next, err = lib.blocks("codex", "继续", nil, frame, M{}, key)
	if err != nil || len(in) != 2 || next == key {
		t.Fatal(len(in), next, err)
	}
}

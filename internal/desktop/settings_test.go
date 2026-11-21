package desktop

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRemovingOnlineAgentPreservesLocalModelAndASR(t *testing.T) {
	root := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", root)
	t.Setenv("LOCALAPPDATA", root)
	dir := appDir("config")
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, "settings.json")
	old := `{"agent":"openai","openai_key":"fixture","model":"retired","provider":"sophnet","local_agent_options":{"codex":{"model":"gpt-6.2-sol","effort":"high"}}}`
	if err := os.WriteFile(file, []byte(old), 0600); err != nil {
		t.Fatal(err)
	}
	s, err := readSettings()
	if err != nil {
		t.Fatal(err)
	}
	if s.values["agent"] != "codex" || s.values["provider"] != "sophnet" || s.values["model"] != nil || s.values["openai_key"] != nil {
		t.Fatal("legacy configuration was not updated")
	}
	o, err := prefs(obj(s.values["local_agent_options"])["codex"])
	if err != nil || o["model"] != "gpt-6.2-sol" || o["effort"] != "high" {
		t.Fatal(o, err)
	}
}

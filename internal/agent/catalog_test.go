package agent

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

// Child mode emulates the subset of the installed app-server used by the settings
// dialog. It has a model directory but no account or conversation methods.
func TestMain(m *testing.M) {
	if len(os.Args) > 1 && os.Args[1] == "app-server" {
		input := bufio.NewScanner(os.Stdin)
		output := json.NewEncoder(os.Stdout)
		for input.Scan() {
			var req M
			if err := json.Unmarshal(input.Bytes(), &req); err != nil {
				os.Exit(2)
			}
			reply := M{"id": req["id"], "result": M{}}
			switch req["method"] {
			case "initialized":
				continue
			case "initialize":
			case "model/list":
				if obj(req["params"])["cursor"] == nil {
					reply["result"] = M{"data": []M{{"model": "next-sol", "isDefault": true}, {"model": "internal", "hidden": true}}, "nextCursor": "second-page"}
				} else if obj(req["params"])["cursor"] == "second-page" {
					reply["result"] = M{"data": []M{{"id": "next-astra", "supportedReasoningEfforts": []M{{"reasoningEffort": "high"}}}}}
				} else {
					os.Exit(3)
				}
			default:
				delete(reply, "result")
				reply["error"] = M{"code": -32601, "message": "unavailable offline"}
			}
			if err := output.Encode(reply); err != nil {
				os.Exit(4)
			}
		}
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func TestInstalledCatalogWithoutSession(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	models, err := CodexModels(ctx, Config{Provider: "codex", Executable: exe, Workspace: t.TempDir()})
	if err != nil {
		t.Fatal(err)
	}
	if len(models) != 2 || models[0]["id"] != "next-sol" || models[0]["default"] != true || models[1]["id"] != "next-astra" || !reflect.DeepEqual(models[1]["effort"], []string{"high"}) {
		t.Fatalf("unexpected model catalog: %#v", models)
	}
}

package desktop

var noteScopes = []choice{{"meeting", "本场便签"}, {"topic", "主题共享"}}
var boardScopes = append([]choice{{"", "全部便签"}}, noteScopes...)

func noteScope(note M) string {
	if str(note["source_meeting"]) != "" {
		return "topic"
	}
	return "meeting"
}

// A shared card and its source can evolve independently. The combined view
// shows the live shared card; the meeting scope still exposes the original.
func boardCards(local, shared []M, scope, kind, status string) []M {
	var cards []M
	sharedSources := map[string]bool{}
	if scope != "meeting" {
		for _, n := range shared {
			sharedSources[str(n["source_note"])] = true
			state := str(n["status"])
			if scope == "topic" && status != "" && status != state && !(status == "pending" && state != "done") {
				continue
			}
			if kind == "" || kind == str(n["kind"]) {
				cards = append(cards, n)
			}
		}
	}
	if scope != "topic" {
		for _, n := range local {
			if scope == "" && sharedSources[str(n["id"])] {
				continue
			}
			if kind == "" || kind == str(n["kind"]) {
				cards = append(cards, n)
			}
		}
	}
	return cards
}

package desktop

import "testing"

func TestRecordViewHoldsHistoryAcrossSnapshots(t *testing.T) {
	snapshot := func(start int) []M {
		var events []M
		for i := start; i < start+120; i++ {
			events = append(events, M{"seq": i, "at": i / 2, "kind": "speech"})
		}
		return events
	}
	var v recordView
	v.replace(snapshot(1), "", true, false)
	v.list.ScrollTo(24)
	v.list.Position.Offset = 17
	for _, first := range []int{40, 130, 252} {
		v.replace(snapshot(first), "", true, false)
		pos := v.list.Position
		if !pos.BeforeEnd || v.ids[pos.First] != "24" || pos.Offset != 17 {
			t.Fatalf("reading position changed after snapshot %d: %+v", first, pos)
		}
	}
	v.seek(1, 200)
	v.replace(snapshot(300), "", true, false)
	if v.list.Position.BeforeEnd || len(v.ids) != 121 || v.ids[120] != "419" {
		t.Fatal("returning to the bottom did not follow the latest snapshot")
	}
}

func TestRecordThumbSeeksWithinTallRow(t *testing.T) {
	var v recordView
	v.replace([]M{{"seq": 1}, {"seq": 2}, {"seq": 3}}, "", false, false)
	v.heights = map[string]int{"1": 30, "2": 1030, "3": 40}
	v.measure(68)
	for _, proportion := range []float32{0, .5, .75, .25, 1} {
		v.seek(proportion, 100)
		if got := v.fraction(100); got != proportion {
			t.Errorf("seek(%v) positioned the thumb at %v", proportion, got)
		}
		if proportion == .75 && (v.list.Position.First != 1 || v.list.Position.Offset != 720) {
			t.Errorf("thumb skipped within tall row: %+v", v.list.Position)
		}
	}
}

func TestRecordStreamCompletesWhileReading(t *testing.T) {
	events := []M{{"seq": 71, "kind": "speech"}}
	var v recordView
	v.replace(events, "", false, true)
	v.list.ScrollTo(1)
	v.list.Position.Offset = 64
	events = append(events, M{"seq": 72, "kind": "speech"}, M{"seq": 73, "kind": "assistant"})
	v.replace(events, "", false, false)
	if pos := v.list.Position; v.ids[pos.First] != "73" || pos.Offset != 64 || !pos.BeforeEnd {
		t.Fatalf("completed reply moved reader: %+v", pos)
	}
}

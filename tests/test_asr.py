"""Regression checks for request-local diarization and timeline alignment."""

import json
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from meeting_agent.asr import ASRConfig, SpeakerTracker, _decode, transcribe


class SpeakerTimeTests(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.wav = Path(self.dir.name) / "speech.wav"
        with wave.open(str(self.wav), "wb") as w:
            w.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
            w.writeframes(bytes([4, 1]) * (16_000 * 5))
        self.config = ASRConfig("siliconflow", "test")
        self.tracker = SpeakerTracker()

    @staticmethod
    def result(*turns):
        return {
            "segments": [
                {"start": start, "end": end, "speaker": speaker, "text": text}
                for start, end, speaker, text in turns
            ]
        }

    def test_renumbered_speaker_and_new_speaker(self):
        with patch(
            "meeting_agent.asr._get_result",
            side_effect=[
                self.result((0, 3, "1", "第一句")),
                self.result((0, 3, "2", "第一句"), (4, 6, "2", "第二句"), (6.5, 9, "1", "插话")),
            ],
        ):
            first = transcribe(self.wav, self.config, tracker=self.tracker)
            second = transcribe(self.wav, self.config, tracker=self.tracker, offset=5.0)
        self.assertEqual([t.text for t in second], ["第二句", "插话"])
        self.assertEqual(first[0].speaker, second[0].speaker)
        self.assertNotEqual(second[0].speaker, second[1].speaker)
        self.assertEqual([(t.start, t.end) for t in second], [(5.0, 7.0), (7.5, 10.0)])

    def test_merged_reference_is_retried_without_reference(self):
        with patch(
            "meeting_agent.asr._get_result",
            side_effect=[
                self.result((0, 3, "1", "第一句")),
                self.result((0, 7, "1", "第一句和第二句")),
                self.result((0, 3, "1", "第二句")),
            ],
        ) as request:
            transcribe(self.wav, self.config, tracker=self.tracker)
            fresh = transcribe(self.wav, self.config, tracker=self.tracker, offset=5)
        self.assertEqual(request.call_count, 3)
        self.assertEqual([(t.start, t.end, t.text) for t in fresh], [(5.0, 8.0, "第二句")])

    def test_sophnet_uses_milliseconds_and_accepts_zero_speaker(self):
        value = {
            "results": [
                {
                    "transcripts": [
                        {
                            "text": "你好",
                            "sentences": [
                                {"begin_time": 80, "end_time": 1700, "speaker_id": 0, "text": "你好"}
                            ],
                        }
                    ]
                }
            ]
        }
        turns, timed = _decode(value, 6)
        self.assertTrue(timed)
        self.assertEqual((turns[0].start, turns[0].end, turns[0].speaker), (0.08, 1.7, "0"))

    def test_sophnet_public_endpoint_handles_model_names_and_previous_deployment_ids(self):
        endpoint = "https://www.sophnet.com/api/open-apis/projects/easyllms/speechtotext/transcriptions"
        deployment = "a" * 22
        for model, expected in (("", "Fun-ASR"), ("Fun-ASR-Nano-2512", "Fun-ASR-Nano-2512"),
                                (deployment, "Fun-ASR")):
            with self.subTest(model=model), patch("meeting_agent.asr._request", side_effect=[
                {"taskId": "example"}, {"status": "SUCCEEDED", "text": "hello"}
            ]) as request, patch("meeting_agent.asr._wait"):
                result = transcribe(self.wav, ASRConfig("sophnet", "test", model))
                upload, poll = request.call_args_list
                self.assertEqual(upload.args[1:3], ("POST", endpoint))
                fields = json.loads(upload.kwargs["files"]["data"][1])
                options = {"speech_recognition_param": {"model": expected, "diarization_enabled": True}}
                if model == deployment:
                    options["easyllm_id"] = deployment
                self.assertEqual(fields, options)
                self.assertEqual(poll.args[1:3], ("GET", endpoint + "/example"))
                self.assertEqual([s.text for s in result], ["hello"])

    def test_untimed_result_preserves_chunk_and_does_not_invent_speaker(self):
        turns, timed = _decode({"text": "纯文本"}, 4.0)
        self.assertFalse(timed)
        self.assertEqual((turns[0].start, turns[0].end, turns[0].speaker), (0.0, 4.0, ""))


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import AsyncMock

from meeting_agent.agent_codex import CodexBackend
from meeting_agent.agent_deepseek import DeepSeekBackend
from meeting_agent.agent_options import codex_catalog, preferences
from meeting_agent.agent_materials import AgentError


class InferenceOptionsTests(unittest.IsolatedAsyncioTestCase):
    def test_codex_uses_advertised_tiers_and_filters_hidden_models(self):
        rows = [{"model": "demo", "displayName": "Demo", "isDefault": True,
                 "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                 "serviceTiers": [{"id": "priority"}, {"id": "ultrafast"}]},
                {"id": "legacy", "additionalSpeedTiers": ["fast"]}, {"id": "hidden", "hidden": True}]
        catalog = codex_catalog(rows)
        self.assertEqual([row["id"] for row in catalog], ["demo", "legacy"])
        self.assertEqual(catalog[0], dict(id="demo", label="Demo", effort=["high"],
                                         speed=["priority", "ultrafast"], default=True))
        self.assertEqual(catalog[1]["speed"], ["priority"])

    async def test_codex_turn_preserves_host_defaults_and_applies_overrides(self):
        for opts in ({}, {"model": "demo", "effort": "high", "speed": "priority"}):
            with self.subTest(options=opts):
                backend = CodexBackend(**({"model": ""} | opts))
                async def reply(method, params):
                    self.assertEqual(method, "turn/start")
                    sent = {k: v for k, v in params.items() if k in ("model", "effort", "serviceTier")}
                    self.assertEqual(sent, {"model":"demo","effort":"high","serviceTier":"priority"} if opts else {})
                    for name, extra in (("item/completed", {"item": {"type": "agentMessage", "id": "i", "text": "{}"}}),
                                        ("turn/completed", {"turn": {"id": "t", "status": "completed"}})):
                        backend.queue.put_nowait({"method": name, "params": {"threadId":"s","turnId":"t", **extra}})
                    return {"turn":{"id":"t"}}
                backend.rpc = reply
                self.assertEqual(await backend._turn("s", [], lambda _: None), {})

    async def test_harness_reloads_dependent_options_after_model_change(self):
        model = {"id":"model", "options":[{"group":"provider", "options":[
            {"value":'["provider","test-model"]',"name":"Test Model"}]}], "currentValue":"old"}
        effort = {"id": "reasoning_effort", "category":"thought_level",
                  "options":[{"value":"deep","name":"Deep"}], "currentValue":"auto"}
        speed = {"id":"speed","options":[{"value":"turbo","name":"Turbo"}],"currentValue":"normal"}
        agent = DeepSeekBackend("test-model", "", "deep", "turbo")
        agent.rpc = AsyncMock(return_value={"configOptions":[model, effort, speed]})
        await agent._configure("session", [model])
        calls = [call.args[1] for call in agent.rpc.await_args_list]
        self.assertEqual([c["value"] for c in calls], ['["provider","test-model"]', "deep", "turbo"])
        self.assertEqual(agent.session_options["speed"], [{"id":"turbo", "label":"Turbo"}])
        agent = DeepSeekBackend(speed="turbo")
        with self.assertRaisesRegex(AgentError, "速率"):
            await agent._configure("session", [model])

    def test_preference_validation(self):
        for raw in ({"effort": 12}, {"model":"x\ny"}, {"unknown":True}):
            with self.assertRaises(ValueError):
                preferences(raw)
        self.assertEqual(preferences({"model":" demo "}), {"model":"demo","effort":"","speed":""})

import os
import unittest
from unittest import mock

from agent_windows import livekit_agent


class LiveKitAgentEntrypointTests(unittest.TestCase):
    def test_main_uses_defaults_and_loads_dotenv(self):
        adapter = mock.Mock()
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(livekit_agent, "load_dotenv") as load_dotenv, \
             mock.patch.object(livekit_agent, "LiveKitSessionAdapter", return_value=adapter) as adapter_type:
            livekit_agent.main()

        load_dotenv.assert_called_once_with(".env")
        adapter_type.assert_called_once_with(enabled=True)
        adapter.run.assert_called_once_with(
            agent_name="ai-aharon",
            instructions=livekit_agent.DEFAULT_INSTRUCTIONS,
            stt_model="deepgram/nova-3-general",
            llm_model="google/gemma-4-31b-it",
            tts_model="inworld/inworld-tts-2",
            tts_voice="Ashley",
        )

    def test_main_honors_environment_and_blank_voice(self):
        adapter = mock.Mock()
        env = {
            "AGENT_DOTENV": "custom.env",
            "LIVEKIT_AGENT_NAME": "custom-agent",
            "LIVEKIT_AGENT_INSTRUCTIONS": "custom instructions",
            "LIVEKIT_STT_MODEL": "custom-stt",
            "LIVEKIT_LLM_MODEL": "custom-llm",
            "LIVEKIT_TTS_MODEL": "custom-tts",
            "LIVEKIT_TTS_VOICE": "",
        }
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(livekit_agent, "load_dotenv") as load_dotenv, \
             mock.patch.object(livekit_agent, "LiveKitSessionAdapter", return_value=adapter):
            livekit_agent.main()

        load_dotenv.assert_called_once_with("custom.env")
        adapter.run.assert_called_once_with(
            agent_name="custom-agent",
            instructions="custom instructions",
            stt_model="custom-stt",
            llm_model="custom-llm",
            tts_model="custom-tts",
            tts_voice=None,
        )


if __name__ == "__main__":
    unittest.main()

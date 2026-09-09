from __future__ import annotations

import unittest

from agent_windows.settings_ui import (
    KEY_ACTIONS,
    SECRET_FIELDS,
    _cancel_settings,
    has_primary_provider_key,
)


class SettingsLinkTests(unittest.TestCase):
    def test_primary_provider_key_detection_ignores_local_url_and_placeholders(self):
        self.assertFalse(
            has_primary_provider_key({"LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434/v1"})
        )
        self.assertFalse(has_primary_provider_key({"GROQ_API_KEY": "your-key-here"}))
        self.assertTrue(has_primary_provider_key({"GEMINI_API_KEY": "real-secret"}))

    def test_cancel_settings_destroys_window_and_notifies_owner(self):
        class Window:
            destroyed = False

            def destroy(self):
                self.destroyed = True

        window = Window()
        called = []
        _cancel_settings(window, lambda: called.append(True))
        self.assertTrue(window.destroyed)
        self.assertEqual(called, [True])

    def test_every_secret_field_has_clickable_action(self):
        secret_names = {key for key, _label in SECRET_FIELDS}
        self.assertEqual(secret_names, set(KEY_ACTIONS))
        for label, url in KEY_ACTIONS.values():
            self.assertTrue(label)
            self.assertTrue(url.startswith("https://"))


if __name__ == "__main__":
    unittest.main()

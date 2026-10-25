from html.parser import HTMLParser
from pathlib import Path
import unittest


class ChatInterfaceMarkupTests(unittest.TestCase):
    def test_message_composer_is_outside_hidable_welcome_stage(self):
        class StructureParser(HTMLParser):
            void_elements = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

            def __init__(self):
                super().__init__()
                self.stack = []
                self.composer_inside_welcome = None
                self.chat_forms = 0

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                element_id = attributes.get("id")
                if element_id == "chatForm":
                    self.chat_forms += 1
                if element_id == "composerCard":
                    self.composer_inside_welcome = any(
                        ancestor_id == "welcome" for _, ancestor_id in self.stack
                    )
                if tag not in self.void_elements:
                    self.stack.append((tag, element_id))

            def handle_endtag(self, tag):
                for index in range(len(self.stack) - 1, -1, -1):
                    if self.stack[index][0] == tag:
                        del self.stack[index]
                        break

        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        parser = StructureParser()
        parser.feed(source)

        self.assertEqual(parser.chat_forms, 1)
        self.assertFalse(parser.composer_inside_welcome)

    def test_provider_badge_uses_only_a_selected_model(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        badge_expression = next(
            line.strip() for line in source.splitlines() if "const display = customModel ?" in line
        )

        self.assertTrue(badge_expression.endswith(": providerLabel;"))

    def test_chat_ui_exposes_message_allowance_and_compact_scrollable_composer(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="messageCounter" role="status" aria-live="polite"', source)
        self.assertIn('id="newChat"', source)
        self.assertIn("max-height: 160px", source)
        self.assertIn('input.style.overflowY = input.scrollHeight > maximum ? "auto" : "hidden";', source)
        self.assertIn('if (e.key === "Enter" && !e.shiftKey)', source)
        self.assertIn("messages_used", source)
        self.assertIn('messagesUsed >= MESSAGE_LIMIT', source)

    def test_message_surfaces_have_no_heavy_card_borders_and_focus_boundary_remains(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/* Keep the conversation open: separate turns with spacing and type, not cards. */", source)
        self.assertIn(".user-message-panel {\n  background: transparent;\n  border: 0;", source)
        self.assertIn(".assistant-message-panel {\n  background: transparent;\n  border: 0;", source)
        self.assertIn(".composer-container:focus-within", source)

    def test_stale_server_not_found_is_mapped_to_restart_recovery(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('const outdatedServerMessage = "The running BraincellGPT server is out of date.', source)
        self.assertIn('data.error === "Conversation not found."', source)
        self.assertIn('if (response.status === 404) throw new Error(outdatedServerMessage);', source)

    def test_onboarding_and_local_conversation_storage_are_present(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="onboardingDialog"', source)
        self.assertIn('id="chooseOwnKey"', source)
        self.assertIn('id="chooseSharedKey"', source)
        self.assertIn('indexedDB.open("braincellgpt_conversations", 1)', source)
        self.assertIn('objectStore("conversations").put(record)', source)
        self.assertIn('objectStore("conversations").delete(record.id)', source)
        self.assertIn('localStorage.setItem(profileStoreKey', source)
        self.assertIn('await saveLocalConversation(localRecord)', source)

    def test_immediate_onboarding_and_wording(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Let's get BrainCellGPT running.", source)
        self.assertIn("Choose how you want to power your chats.", source)
        self.assertIn("Use my API key", source)
        self.assertIn("Use BraincellGPT's free access", source)
        self.assertIn("Limit of 10 accepted message attempts per installation", source)
        self.assertIn("hasValidConfig", source)
        self.assertIn("onboardingDialog.showModal", source)
        self.assertIn("onboardingDialog.addEventListener", source)

    def test_settings_active_config_and_confirmation(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="activeConfigPanel"', source)
        self.assertIn('id="activeConfigMode"', source)
        self.assertIn('id="activeConfigProvider"', source)
        self.assertIn('id="activeConfigModel"', source)
        self.assertIn('id="activeConfigStatusText"', source)
        self.assertIn('id="activeConfigBadge"', source)
        self.assertIn('id="settingsConfirmation"', source)
        self.assertIn('showSettingsConfirmation("Settings saved.', source)

    def test_chat_request_synchronizes_provider_and_mode(self):
        source = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('provider: credentialMode === "shared_groq" ? "groq" : providerSelect.value', source)
        self.assertIn('apiKeyInput.value = "";', source)


if __name__ == "__main__":
    unittest.main()

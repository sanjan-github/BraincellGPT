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


if __name__ == "__main__":
    unittest.main()

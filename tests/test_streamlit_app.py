from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest


class FeedOnlyButtonVisibilityTests(unittest.TestCase):
    def load_app(self) -> AppTest:
        app = AppTest.from_file("streamlit_app.py", default_timeout=15).run()
        self.assertEqual([item.value for item in app.exception], [])
        return app

    def test_locked_owner_sees_password_instruction_not_build_button(self) -> None:
        app = self.load_app()
        button_labels = [button.label for button in app.button]
        warnings = [warning.value.casefold() for warning in app.warning]

        self.assertNotIn("Build from Automated Feed Only", button_labels)
        self.assertTrue(
            any(
                "owner password" in warning
                and "button will then appear" in warning
                for warning in warnings
            )
        )

    def test_unlocked_owner_sees_feed_only_build_button(self) -> None:
        app = self.load_app()
        app.session_state["owner_authenticated"] = True
        app.run()

        self.assertEqual([item.value for item in app.exception], [])
        self.assertIn(
            "Build from Automated Feed Only",
            [button.label for button in app.button],
        )

    def test_visible_title_is_preserved_without_old_subtitle(self) -> None:
        app = self.load_app()

        self.assertIn(
            "Advanced Transportation News Update",
            [title.value for title in app.title],
        )
        rendered_text = "\n".join(
            item.value
            for collection in (app.title, app.caption, app.markdown)
            for item in collection
        )
        self.assertNotIn("UAS, C-UAS, and Advanced Transportation", rendered_text)


if __name__ == "__main__":
    unittest.main()

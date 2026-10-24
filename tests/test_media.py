import base64
import unittest
from unittest.mock import patch

from backend.media import MAX_IMAGES, anthropic_content, normalize_images, openai_content


def data_url(mime_type="image/png", data=b"\x89PNG\r\n\x1a\nimage"):
    return f"data:{mime_type};base64," + base64.b64encode(data).decode("ascii")


class ImageInputTests(unittest.TestCase):
    def test_accepts_supported_image_data_urls(self):
        for mime_type, data in (
            ("image/png", b"\x89PNG\r\n\x1a\nimage"),
            ("image/jpeg", b"\xff\xd8\xffimage"),
            ("image/gif", b"GIF89aimage"),
            ("image/webp", b"RIFFxxxxWEBPimage"),
        ):
            with self.subTest(mime_type=mime_type):
                self.assertEqual(normalize_images([data_url(mime_type, data)])[0]["mime_type"], mime_type)

    def test_rejects_malformed_or_mismatched_data(self):
        for value in ("https://example.test/image.png", "data:image/png;base64,%%%", data_url("image/png", b"not png")):
            with self.subTest(value=value[:40]), self.assertRaises(ValueError):
                normalize_images([value])

    def test_rejects_too_many_images(self):
        with self.assertRaises(ValueError):
            normalize_images([data_url()] * (MAX_IMAGES + 1))

    def test_rejects_combined_decoded_size_over_eight_megabytes(self):
        large_png = b"\x89PNG\r\n\x1a\n" + b"x" * 2_800_000
        with patch("backend.media.MAX_IMAGE_BYTES", 3_000_000):
            with self.assertRaisesRegex(ValueError, "Combined image data"):
                normalize_images([data_url(data=large_png)] * 3)

    def test_provider_converters_keep_text_and_encode_images_natively(self):
        image = normalize_images([data_url()])
        openai = openai_content("describe", image)
        anthropic = anthropic_content("describe", image)
        assert isinstance(openai, list)
        assert isinstance(anthropic, list)
        self.assertEqual(openai[0], {"type": "text", "text": "describe"})
        self.assertTrue(openai[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(anthropic[1]["type"], "image")
        self.assertEqual(anthropic[1]["source"]["media_type"], "image/png")


if __name__ == "__main__":
    unittest.main()

import base64
import struct
import unittest
import zlib
from unittest.mock import patch

from backend.media import MAX_IMAGES, anthropic_content, normalize_images, openai_content


def tiny_image(mime_type):
    if mime_type == "image/png":
        return png_image(1, 1)
    if mime_type == "image/jpeg":
        return b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
    if mime_type == "image/gif":
        return b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff;"
    return b"RIFF\x16\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00" + b"\x00" * 10


def png_image(width, height):
    def chunk(name, payload):
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
            + chunk(b"IEND", b""))


def data_url(mime_type="image/png", data=None):
    data = tiny_image(mime_type) if data is None else data
    return f"data:{mime_type};base64," + base64.b64encode(data).decode("ascii")


class ImageInputTests(unittest.TestCase):
    def test_accepts_supported_image_data_urls(self):
        for mime_type in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            data = tiny_image(mime_type)
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
        with patch("backend.media.MAX_TOTAL_IMAGE_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "Combined image data"):
                normalize_images([data_url()] * 4)

    def test_rejects_truncated_headers_and_oversized_dimensions(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            normalize_images([data_url(data=b"\x89PNG\r\n\x1a\nimage")])
        huge = png_image(9000, 1)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            normalize_images([data_url(data=huge)])

    def test_rejects_png_with_corrupt_chunk_crc(self):
        broken = bytearray(tiny_image("image/png"))
        broken[29] ^= 0x01
        with self.assertRaisesRegex(ValueError, "corrupt chunk"):
            normalize_images([data_url(data=bytes(broken))])

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

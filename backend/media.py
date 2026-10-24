"""Validation and provider-format conversion for bounded inline images."""
from __future__ import annotations

import base64
import binascii
import re
import zlib
from typing import Any


MAX_IMAGES = 4
MAX_IMAGE_BYTES = 3_000_000
MAX_TOTAL_IMAGE_BYTES = 8_000_000
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 40_000_000
_IMAGE_DATA_URI_RE = re.compile(r"^data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/]*={0,2})$")


def normalize_images(images: Any) -> list[dict[str, str]]:
    """Validate image data URIs and return provider-neutral MIME/base64 parts."""
    if images is None:
        return []
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError(f"Images must be a list of at most {MAX_IMAGES} data URLs.")
    normalized = []
    total_bytes = 0
    for value in images:
        if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 256:
            raise ValueError("Each image must be a bounded base64 data URL.")
        match = _IMAGE_DATA_URI_RE.fullmatch(value)
        if not match:
            raise ValueError("Images must use PNG, JPEG, GIF, or WebP base64 data URLs.")
        mime_type, encoded = match.groups()
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Image data is not valid base64.") from exc
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Each decoded image must be between 1 byte and 3 MB.")
        if not _matches_image_signature(mime_type, data):
            raise ValueError("Image MIME type does not match its file contents.")
        width, height = _image_dimensions(mime_type, data)
        if width < 1 or height < 1 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION or width * height > MAX_IMAGE_PIXELS:
            raise ValueError("Image dimensions exceed the 8,192-pixel side or 40-megapixel limit.")
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("Combined image data must not exceed 8 MB.")
        normalized.append({"mime_type": mime_type, "data": encoded})
    return normalized


def _matches_image_signature(mime_type: str, data: bytes) -> bool:
    if mime_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if mime_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if mime_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _image_dimensions(mime_type: str, data: bytes) -> tuple[int, int]:
    """Read bounded dimensions from image headers without decompressing pixels."""
    if mime_type == "image/png":
        if len(data) < 45:
            raise ValueError("PNG image data is truncated or malformed.")
        offset = 8
        dimensions = None
        saw_idat = False
        saw_iend = False
        while offset + 12 <= len(data):
            chunk_length = int.from_bytes(data[offset:offset + 4], "big")
            chunk_end = offset + 12 + chunk_length
            if chunk_end > len(data):
                break
            chunk_type = data[offset + 4:offset + 8]
            chunk_data = data[offset + 8:offset + 8 + chunk_length]
            expected_crc = int.from_bytes(data[offset + 8 + chunk_length:chunk_end], "big")
            if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
                raise ValueError("PNG image data contains a corrupt chunk.")
            if dimensions is None:
                if chunk_type != b"IHDR" or chunk_length != 13:
                    raise ValueError("PNG image data has an invalid header.")
                dimensions = (int.from_bytes(chunk_data[:4], "big"), int.from_bytes(chunk_data[4:8], "big"))
            elif chunk_type == b"IHDR":
                raise ValueError("PNG image data contains a duplicate header.")
            if chunk_type == b"IDAT":
                saw_idat = True
            if chunk_type == b"IEND":
                saw_iend = chunk_length == 0
                offset = chunk_end
                break
            offset = chunk_end
        if dimensions is None or not saw_idat or not saw_iend or offset != len(data):
            raise ValueError("PNG image data is truncated or malformed.")
        return dimensions
    if mime_type == "image/gif":
        if len(data) < 14 or data[-1:] != b";":
            raise ValueError("GIF image data is truncated or malformed.")
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if mime_type == "image/jpeg":
        if len(data) < 4 or not data.endswith(b"\xff\xd9"):
            raise ValueError("JPEG image data is truncated or malformed.")
        offset = 2
        frame_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset + 4 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7 or marker == 0x01:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                break
            if marker in frame_markers:
                if segment_length < 7:
                    break
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                return width, height
            offset += segment_length
        raise ValueError("JPEG image data is truncated or has no valid frame header.")
    if mime_type == "image/webp":
        if len(data) < 30 or int.from_bytes(data[4:8], "little") + 8 != len(data):
            raise ValueError("WebP image data is truncated or malformed.")
        chunk = data[12:16]
        payload = data[20:]
        if chunk == b"VP8X" and len(payload) >= 10:
            return 1 + int.from_bytes(payload[4:7], "little"), 1 + int.from_bytes(payload[7:10], "little")
        if chunk == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            return int.from_bytes(payload[6:8], "little") & 0x3FFF, int.from_bytes(payload[8:10], "little") & 0x3FFF
        if chunk == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
            bits = int.from_bytes(payload[1:5], "little")
            return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
        raise ValueError("WebP image data is truncated or has an unsupported frame header.")
    raise ValueError("Unsupported image type.")


def openai_content(text: str, images: list[dict[str, str]]) -> str | list[dict]:
    if not images:
        return text
    return [
        {"type": "text", "text": text},
        *[
            {"type": "image_url", "image_url": {"url": f"data:{image['mime_type']};base64,{image['data']}"}}
            for image in images
        ],
    ]


def anthropic_content(text: str, images: list[dict[str, str]]) -> str | list[dict]:
    if not images:
        return text
    return [
        {"type": "text", "text": text},
        *[
            {"type": "image", "source": {"type": "base64", "media_type": image["mime_type"], "data": image["data"]}}
            for image in images
        ],
    ]

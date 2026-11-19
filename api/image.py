"""Resize public LOST112 photos for the list and detail views."""

from http.server import BaseHTTPRequestHandler
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SOURCE_BYTES = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000


def allowed_photo(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == "minwon24.police.go.kr"
        and parts.port is None
        and parts.username is None
        and parts.password is None
        and parts.path.startswith("/lost112/find/getOpenapiAttachFileImage/")
        and parts.path.endswith(".do")
    )


def resize_photo(raw: bytes, width: int, quality: int) -> bytes:
    with Image.open(BytesIO(raw)) as source:
        source.draft("RGB", (width, width))
        photo = ImageOps.exif_transpose(source)
        photo.thumbnail((width, width), Image.Resampling.LANCZOS)
        if photo.mode not in ("RGB", "RGBA"):
            photo = photo.convert("RGB")
        output = BytesIO()
        photo.save(output, format="WEBP", quality=quality, method=4)
        return output.getvalue()


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str, cache: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
        if cache:
            self.send_header("Vercel-CDN-Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        url = query.get("url", [""])[0]
        try:
            width = int(query.get("w", [""])[0])
            quality = int(query.get("q", [""])[0])
            if (width, quality) not in ((128, 65), (640, 75)) or not allowed_photo(url):
                raise ValueError("Invalid photo request")
        except (ValueError, TypeError):
            return self._send(400, b"Invalid photo request", "text/plain; charset=utf-8")

        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=30) as response:
                if not allowed_photo(response.geturl()):
                    raise ValueError("Unexpected photo redirect")
                raw = response.read(MAX_SOURCE_BYTES + 1)
            if len(raw) > MAX_SOURCE_BYTES:
                return self._send(413, b"Photo too large", "text/plain; charset=utf-8")
            optimized = resize_photo(raw, width, quality)
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            self.log_error("Photo conversion failed: %s", type(error).__name__)
            reason = getattr(error, "reason", "")
            detail = f"{type(error).__name__}: {type(reason).__name__}: {str(reason)[:120]}"
            return self._send(502, detail.encode(), "text/plain; charset=utf-8")

        self._send(200, optimized, "image/webp", cache=True)

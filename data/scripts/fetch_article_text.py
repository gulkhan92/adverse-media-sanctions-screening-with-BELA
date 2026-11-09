"""Fetch article text for harvested URLs, extract it, and detect the language.

Safety and etiquette:
  - robots.txt is checked per domain and respected.
  - A per-domain minimum delay is enforced.
  - Private, loopback, link-local, and reserved addresses are blocked (SSRF).
  - Responses are limited in size and time, and redirects are re-validated.
  - Only a snippet of at most 300 characters is stored. Full article bodies are
    returned to the caller of fetch_article() for in-memory processing and are
    never written to disk. Always link to the original source.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from common import PROCESSED_DIR, RAW_DIR, USER_AGENT, get_logger

SNIPPET_CHARS = 300
MAX_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 4
DOMAIN_DELAY_SECONDS = 5.0
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
ALLOWED_TYPES = ("text/html", "application/xhtml+xml")

log = get_logger("fetch_article_text")


class BlockedUrl(Exception):
    """Raised when a URL is refused for safety reasons."""


@dataclass
class FetchResult:
    url: str
    status: str  # ok, blocked, robots, http_error, no_text, too_large, error
    language: str | None = None
    title: str | None = None
    snippet: str | None = None
    text_chars: int = 0
    fetched_at: str = ""
    detail: str | None = None


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified
    )


def validate_url(url: str) -> None:
    """Raise BlockedUrl unless the URL is http(s) and resolves only to public addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BlockedUrl("unsupported scheme or missing host")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedUrl(f"dns failure: {exc}") from exc
    for info in infos:
        if not is_public_address(str(info[4][0])):
            raise BlockedUrl("address is not public")


class DomainLimiter:
    """Enforces a minimum delay between requests to the same domain."""

    def __init__(self, delay: float = DOMAIN_DELAY_SECONDS) -> None:
        self.delay = delay
        self._last: dict[str, float] = {}

    def wait(self, domain: str) -> None:
        elapsed = time.monotonic() - self._last.get(domain, 0.0)
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last[domain] = time.monotonic()


class RobotsCache:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self._parsers: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._parsers:
            parser = RobotFileParser()
            try:
                validate_url(f"{origin}/robots.txt")
                response = self.client.get(f"{origin}/robots.txt")
                if response.status_code >= 500:
                    self._parsers[origin] = None  # unreachable: be conservative
                    return False
                parser.parse(response.text.splitlines() if response.status_code == 200 else [])
                self._parsers[origin] = parser
            except (httpx.HTTPError, BlockedUrl):
                self._parsers[origin] = None
        parser_or_none = self._parsers[origin]
        return bool(parser_or_none and parser_or_none.can_fetch(USER_AGENT, url))


def read_limited(response: httpx.Response) -> bytes:
    body = bytearray()
    for block in response.iter_bytes():
        body.extend(block)
        if len(body) > MAX_BYTES:
            raise ValueError("response too large")
    return bytes(body)


def make_snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 1].rstrip() + "…"


_detector = None


def detect_language(text: str) -> str | None:
    """Return an ISO 639-1 code from lingua, or None when confidence is unavailable."""
    global _detector
    from lingua import LanguageDetectorBuilder

    if _detector is None:
        _detector = LanguageDetectorBuilder.from_all_languages().build()
    language = _detector.detect_language_of(text[:2000])
    return language.iso_code_639_1.name.lower() if language else None


def fetch_article(
    url: str, client: httpx.Client, robots: RobotsCache, limiter: DomainLimiter
) -> tuple[FetchResult, str | None]:
    """Fetch one article. Returns (result, full_text); full_text is for in-memory use only."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    result = FetchResult(url=url, status="error", fetched_at=now)
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            validate_url(current)
            if not robots.allowed(current):
                result.status = "robots"
                return result, None
            limiter.wait(urlparse(current).netloc)
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    current = urljoin(current, response.headers.get("location", ""))
                    continue
                if response.status_code >= 400:
                    result.status, result.detail = "http_error", str(response.status_code)
                    return result, None
                ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in ALLOWED_TYPES:
                    result.status, result.detail = "no_text", f"content-type {ctype}"
                    return result, None
                html = read_limited(response).decode(response.encoding or "utf-8", "replace")
            break
        else:
            result.status, result.detail = "error", "too many redirects"
            return result, None
    except BlockedUrl as exc:
        result.status, result.detail = "blocked", str(exc)
        return result, None
    except ValueError as exc:
        result.status, result.detail = "too_large", str(exc)
        return result, None
    except httpx.HTTPError as exc:
        result.status, result.detail = "error", type(exc).__name__
        return result, None

    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text:
        result.status = "no_text"
        return result, None
    meta = trafilatura.extract_metadata(html)
    result.status = "ok"
    result.title = meta.title if meta else None
    result.language = detect_language(text)
    result.snippet = make_snippet(text)
    result.text_chars = len(text)
    return result, text


def is_transient(result: FetchResult) -> bool:
    if result.status == "error":
        return True
    return result.status == "http_error" and (result.detail or "") in {"429", "500", "502", "503"}


def fetch_with_retry(
    url: str,
    client: httpx.Client,
    robots: RobotsCache,
    limiter: DomainLimiter,
    attempts: int = 3,
) -> FetchResult:
    """Retry transient failures with exponential backoff (2, 4, ... seconds)."""
    result, _ = fetch_article(url, client, robots, limiter)
    for attempt in range(1, attempts):
        if not is_transient(result):
            break
        time.sleep(2**attempt)
        result, _ = fetch_article(url, client, robots, limiter)
    return result


def iter_urls(paths: list[Path]) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                url = json.loads(line).get("url") if line.strip() else None
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", help="harvest day YYYY-MM-DD (default: all files)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=DOMAIN_DELAY_SECONDS)
    args = parser.parse_args()

    gdelt_dir = RAW_DIR / "news" / "gdelt"
    files = sorted(gdelt_dir.glob(f"{args.day or '*'}.jsonl"))
    urls = iter_urls(files)
    out_path = PROCESSED_DIR / "articles" / "article_snippets.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out_path.exists():
        with out_path.open(encoding="utf-8") as handle:
            done = {json.loads(line)["url"] for line in handle if line.strip()}
    todo = [u for u in urls if u not in done]
    if args.limit:
        todo = todo[: args.limit]

    limiter = DomainLimiter(args.delay)
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=False
    ) as client, out_path.open("a", encoding="utf-8") as sink:
        robots = RobotsCache(client)
        for url in todo:
            result = fetch_with_retry(url, client, robots, limiter)
            sink.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
            sink.flush()
            log.info(f"{result.status} {url}")


if __name__ == "__main__":
    main()

"""HTTP client using curl_cffi for browser-grade TLS fingerprinting.

curl_cffi impersonates real browser TLS/JA3/HTTP2 fingerprints, which bypasses
basic Cloudflare bot detection that blocks plain requests/httpx.

For sites with full Cloudflare JS challenges, the optional nodriver-based
challenge solver can be used to obtain cf_clearance cookies.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession, Response

logger = logging.getLogger("pyackett.http")

# Browser to impersonate — Chrome is the safest default
DEFAULT_IMPERSONATE = "chrome"

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


@dataclass
class CfClearance:
    """Cached Cloudflare clearance cookies for a domain."""

    cookies: dict[str, str] = field(default_factory=dict)
    user_agent: str = ""

    def to_dict(self) -> dict:
        return {"cookies": self.cookies, "user_agent": self.user_agent}

    @classmethod
    def from_dict(cls, data: dict) -> "CfClearance":
        return cls(cookies=data.get("cookies", {}), user_agent=data.get("user_agent", ""))


class HttpClient:
    """Async HTTP client with browser TLS fingerprinting and CF bypass.

    Uses curl_cffi to impersonate real browser TLS fingerprints.
    Optionally uses nodriver to solve Cloudflare JS challenges.
    """

    def __init__(
        self,
        proxy: str | None = None,
        timeout: float = 30.0,
        connect_timeout: float = 5.0,
        impersonate: str = DEFAULT_IMPERSONATE,
    ):
        self._proxy = proxy
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._impersonate = impersonate
        self._session: AsyncSession | None = None
        self._cf_cache: dict[str, CfClearance] = {}
        self._cf_failed: set[str] = set()  # domains where CF solve failed this session
        self._cf_cache_path: Path | None = None  # set by Pyackett to enable persistence

    async def _ensure_session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(
                impersonate=self._impersonate,
                proxy=self._proxy,
                timeout=(self._connect_timeout, self._timeout),
                headers=DEFAULT_HEADERS,
            )
        return self._session

    async def _get_session_for_domain(self, url: str) -> AsyncSession:
        """Get session with correct browser impersonation for the domain.

        If we have CF cookies from Camoufox (Firefox), we must impersonate
        Firefox — CF ties cf_clearance to the TLS fingerprint.
        """
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        cf = self._cf_cache.get(domain)
        if cf and cf.user_agent and "Firefox" in cf.user_agent:
            # Need a Firefox-impersonating session for this domain
            if not hasattr(self, '_ff_session') or self._ff_session is None:
                self._ff_session = AsyncSession(
                    impersonate="firefox",
                    proxy=self._proxy,
                    timeout=(self._connect_timeout, self._timeout),
                    headers=DEFAULT_HEADERS,
                )
                # Copy cookies
                for name, value in cf.cookies.items():
                    self._ff_session.cookies.set(name, value, domain=domain)
            return self._ff_session
        return await self._ensure_session()

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        follow_redirects: bool = True,
        cf_retry: bool = True,
    ) -> Response:
        """GET request with automatic CF challenge retry."""
        session = await self._get_session_for_domain(url)
        merged = self._merge_cf_cookies(url, headers)
        resp = await session.get(
            url,
            headers=merged,
            params=params,
            allow_redirects=follow_redirects,
        )
        if cf_retry and self._is_cf_challenge(resp) and self._should_try_cf(url):
            solved = await self._solve_cf_challenge(url)
            if solved:
                session = await self._get_session_for_domain(url)
                merged = self._merge_cf_cookies(url, headers)
                # Always follow redirects on CF retry — the clearance flow may redirect
                resp = await session.get(
                    url,
                    headers=merged,
                    params=params,
                    allow_redirects=True,
                )
                logger.debug(f"CF retry: {resp.status_code} len={len(resp.text)}")
            else:
                from urllib.parse import urlparse
                self._cf_failed.add(urlparse(url).netloc)
        return resp

    async def post(
        self,
        url: str,
        data: dict | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        follow_redirects: bool = True,
        cf_retry: bool = True,
    ) -> Response:
        """POST request with automatic CF challenge retry."""
        session = await self._get_session_for_domain(url)
        merged = self._merge_cf_cookies(url, headers)
        resp = await session.post(
            url,
            data=data,
            headers=merged,
            params=params,
            allow_redirects=follow_redirects,
        )
        if cf_retry and self._is_cf_challenge(resp) and self._should_try_cf(url):
            solved = await self._solve_cf_challenge(url)
            if solved:
                session = await self._get_session_for_domain(url)
                merged = self._merge_cf_cookies(url, headers)
                resp = await session.post(
                    url,
                    data=data,
                    headers=merged,
                    params=params,
                    allow_redirects=True,
                )
            else:
                from urllib.parse import urlparse
                self._cf_failed.add(urlparse(url).netloc)
        return resp

    def _merge_cf_cookies(self, url: str, headers: dict[str, str] | None) -> dict[str, str]:
        """Merge CF clearance cookies and User-Agent into request headers."""
        merged = dict(headers or {})
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        cf = self._cf_cache.get(domain)
        if cf:
            # Set cookies via header
            existing = merged.get("Cookie", "")
            cf_cookie_str = "; ".join(f"{k}={v}" for k, v in cf.cookies.items())
            if existing:
                merged["Cookie"] = existing + "; " + cf_cookie_str
            else:
                merged["Cookie"] = cf_cookie_str
            # MUST use the same User-Agent that solved the challenge
            if cf.user_agent:
                merged["User-Agent"] = cf.user_agent

            # Also inject cookies into the curl_cffi session cookie jar
            if self._session:
                for name, value in cf.cookies.items():
                    self._session.cookies.set(name, value, domain=domain)

        return merged

    def _should_try_cf(self, url: str) -> bool:
        """Check if we should attempt CF solve for this URL."""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if domain in self._cf_failed:
            return False
        # If we have cached cookies but got 403, they're stale — clear and retry
        if domain in self._cf_cache:
            logger.debug(f"Clearing stale CF cookies for {domain}")
            del self._cf_cache[domain]
            # Also clear the Firefox session so it gets new cookies
            if hasattr(self, '_ff_session') and self._ff_session:
                self._ff_session = None
        return True

    @staticmethod
    def _is_cf_challenge(resp: Response) -> bool:
        """Detect Cloudflare challenge responses."""
        if resp.status_code == 403:
            ct = resp.headers.get("server", "")
            if "cloudflare" in ct.lower():
                return True
            # Check body for CF challenge markers
            body = resp.text[:2000] if resp.text else ""
            if "cf-browser-verification" in body or "cf_clearance" in body:
                return True
            if "Checking if the site connection is secure" in body:
                return True
            if "challenges.cloudflare.com" in body:
                return True
        if resp.status_code == 503:
            body = resp.text[:2000] if resp.text else ""
            if "cloudflare" in body.lower():
                return True
        return False

    async def _solve_cf_challenge(self, url: str) -> bool:
        """Solve a Cloudflare challenge using Camoufox.

        Camoufox is an anti-detect Firefox browser that handles Cloudflare
        Turnstile and JS challenges automatically. It injects realistic
        fingerprints at the C++ level, making detection very difficult.

        Falls back to nodriver if camoufox is not installed.
        """
        from urllib.parse import urlparse
        domain = urlparse(url).netloc

        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            logger.warning(
                "Cloudflare challenge detected but camoufox is not installed. "
                "Install with: pip install pyackett[cloudflare]"
            )
            return False

        logger.info(f"Solving Cloudflare challenge for {domain} via Camoufox...")

        # Build proxy config for Playwright/Camoufox
        # Browsers don't support SOCKS5 auth — use local HTTP CONNECT proxy
        proxy_config = None
        local_forwarder = None
        if self._proxy:
            pp = urlparse(self._proxy)
            if pp.username or "socks" in (pp.scheme or ""):
                # Start local HTTP proxy that tunnels through SOCKS5
                local_forwarder = await _start_http_proxy_over_socks(self._proxy)
                if local_forwarder:
                    proxy_config = {"server": local_forwarder["url"]}
            else:
                proxy_config = {"server": self._proxy}

        try:
            logger.debug(f"Launching Camoufox with proxy={proxy_config}")
            async with AsyncCamoufox(
                headless=True,
                proxy=proxy_config,
                geoip=True,
            ) as browser:
                page = await browser.new_page()
                # Navigate to the actual URL to solve CF for that specific path.
                # Some sites have different CF security per path.
                logger.info(f"Navigating to {url} to solve CF challenge")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as nav_err:
                    logger.error(f"Navigation failed: {nav_err}")
                    return False

                # Wait for CF to resolve (up to 45s)
                turnstile_clicked = False
                for attempt in range(90):
                    await asyncio.sleep(0.5)
                    try:
                        content = await page.content()
                    except Exception:
                        # Page may be navigating after CF solve
                        continue
                    if attempt % 5 == 0:
                        title = await page.title()
                        has_cf = "challenges.cloudflare.com" in content
                        logger.debug(f"CF solve [{attempt}] cf={has_cf} title={title[:40]}")

                    # Click the Turnstile widget if visible
                    if not turnstile_clicked and attempt >= 3 and "challenges.cloudflare.com" in content:
                        try:
                            for frame in page.frames:
                                if "challenges.cloudflare.com" in (frame.url or ""):
                                    body = await frame.query_selector("body")
                                    if body:
                                        await body.click()
                                        turnstile_clicked = True
                                        logger.info("Clicked Turnstile challenge frame")
                                        break
                        except Exception as click_err:
                            logger.debug(f"Turnstile click attempt: {click_err}")

                    # Retry click after a while if still stuck
                    if turnstile_clicked and attempt % 10 == 0 and "challenges.cloudflare.com" in content:
                        turnstile_clicked = False

                    # Check if challenge is gone
                    if ("challenges.cloudflare.com" not in content
                            and "cf-browser-verification" not in content
                            and attempt >= 4):

                        # Extract cookies
                        cookies_list = await page.context.cookies()
                        cf_cookies = {}
                        for c in cookies_list:
                            if domain in c.get("domain", ""):
                                cf_cookies[c["name"]] = c["value"]

                        ua = await page.evaluate("navigator.userAgent")

                        if cf_cookies or len(content) > 1000:
                            self._cf_cache[domain] = CfClearance(
                                cookies=cf_cookies, user_agent=ua,
                            )
                            logger.info(
                                f"Cloudflare bypassed for {domain} "
                                f"({len(cf_cookies)} cookies, "
                                f"cf_clearance={'cf_clearance' in cf_cookies})"
                            )
                            if self._cf_cache_path:
                                self.save_cf_cache(self._cf_cache_path)
                            return True

                logger.warning(f"Cloudflare challenge timed out for {domain}")
                return False

        except Exception as e:
            logger.error(f"Camoufox CF solver error for {domain}: {e}")
            return False
        finally:
            if local_forwarder:
                try:
                    local_forwarder["server"].close()
                    await local_forwarder["server"].wait_closed()
                except Exception:
                    pass

    def save_cf_cache(self, path: "Path"):
        """Persist CF clearance cookies to disk for reuse after restart."""
        from pathlib import Path
        data = {domain: cf.to_dict() for domain, cf in self._cf_cache.items()}
        if data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
            logger.debug(f"Saved CF cookies for {len(data)} domains")

    def load_cf_cache(self, path: "Path"):
        """Load previously saved CF clearance cookies."""
        from pathlib import Path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for domain, cf_data in data.items():
                self._cf_cache[domain] = CfClearance.from_dict(cf_data)
            if data:
                logger.info(f"Loaded CF cookies for {len(data)} domains")
        except Exception as e:
            logger.warning(f"Failed to load CF cache: {e}")

    async def close(self):
        """Close all sessions."""
        if self._session:
            await self._session.close()
            self._session = None
        if hasattr(self, '_ff_session') and self._ff_session:
            await self._ff_session.close()
            self._ff_session = None


async def _start_http_proxy_over_socks(upstream_socks: str) -> dict | None:
    """Start a local HTTP CONNECT proxy that tunnels through an authenticated SOCKS5 upstream.

    Browsers support HTTP proxy auth but not SOCKS5 auth. This starts a tiny
    HTTP proxy on localhost that accepts CONNECT requests and tunnels them
    through the upstream SOCKS5 proxy using python-socks.

    Returns {"port": int, "server": asyncio.Server, "url": str} or None.
    """
    import socks  # from PySocks, installed by curl_cffi
    import socket
    from urllib.parse import urlparse as _urlparse

    pp = _urlparse(upstream_socks)
    upstream_host = pp.hostname
    upstream_port = pp.port or 1080
    upstream_user = pp.username
    upstream_pass = pp.password

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            # Read the HTTP CONNECT request
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                writer.close()
                return
            request_str = request_line.decode("utf-8", errors="replace").strip()
            # e.g. "CONNECT example.com:443 HTTP/1.1"
            parts = request_str.split()
            if len(parts) < 2 or parts[0] != "CONNECT":
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            target = parts[1]
            host, _, port_str = target.partition(":")
            port = int(port_str) if port_str else 443

            # Read remaining headers until blank line
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in (b"\r\n", b"\n", b""):
                    break

            # Connect to target via SOCKS5
            s = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
            s.set_proxy(
                socks.SOCKS5, upstream_host, upstream_port,
                rdns=True,
                username=upstream_user,
                password=upstream_pass,
            )
            s.settimeout(15)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, s.connect, (host, port))
            s.setblocking(False)

            # Tell client the tunnel is established
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            # Bidirectional pipe
            up_reader, up_writer = await asyncio.open_connection(sock=s)

            async def _pipe(r, w):
                try:
                    while True:
                        data = await r.read(65536)
                        if not data:
                            break
                        w.write(data)
                        await w.drain()
                except Exception:
                    pass
                finally:
                    try:
                        w.close()
                    except Exception:
                        pass

            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

        except Exception:
            try:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
            try:
                writer.close()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        logger.info(f"Local HTTP-to-SOCKS5 proxy listening on {url}")
        return {"port": port, "server": server, "url": url}
    except Exception as e:
        logger.error(f"Failed to start local proxy: {e}")
        return None


# Keep a simple factory for backward compat
def create_http_client(
    proxy: str | None = None,
    timeout: float = 30.0,
    connect_timeout: float = 5.0,
) -> HttpClient:
    """Create an HttpClient with optional proxy support.

    Supports SOCKS5, SOCKS4, HTTP proxies natively via curl_cffi.
    For SOCKS5 proxies, remote DNS resolution is used by default
    (socks5h://) to avoid local DNS leaks.

    Args:
        proxy: Proxy URL string, or None for direct connection.
        timeout: Total request timeout in seconds.
        connect_timeout: TCP connection establishment timeout in seconds.
    """
    # curl_cffi needs socks5h:// for remote DNS resolution
    if proxy and proxy.startswith("socks5://"):
        proxy = "socks5h://" + proxy[len("socks5://"):]
    return HttpClient(proxy=proxy, timeout=timeout, connect_timeout=connect_timeout)

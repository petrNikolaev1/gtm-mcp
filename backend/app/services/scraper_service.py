"""Web Scraper Service — god-level text extraction from websites.

Uses Apify residential proxies (if configured) for parallel scraping.
BeautifulSoup + CSS selector cleanup for maximum readable text extraction.
Removes nav, footer, cookies, popups, sidebars — keeps only page content.
50 concurrent requests via semaphore, rotating User-Agents.
"""
import asyncio
import random
import re
from typing import Dict, Any, List, Optional, Callable
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import logging
import httpx

from app.services.cost_tracker import get_tracker

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


class ScraperService:
    def __init__(self, apify_proxy_password: Optional[str] = None):
        self._request_count = 0
        self._apify_proxy_password = apify_proxy_password

    def _validate_url(self, url: str) -> tuple[bool, str, str]:
        if not url:
            return False, "", "INVALID_URL: URL is empty"
        url = url.strip()
        if url in ['--', '-', 'n/a', 'N/A', 'na', 'NA', 'none', 'None', 'null', '']:
            return False, "", "INVALID_URL: No URL provided"
        if len(url) < 4:
            return False, "", "INVALID_URL: URL is too short"
        if ' ' in url or '\n' in url or '\t' in url:
            return False, "", "INVALID_URL: URL contains invalid characters"
        url = re.sub(r'^(https?:/*)?', '', url, flags=re.IGNORECASE)
        url = url.rstrip('/')
        if not url or '.' not in url.split('/')[0]:
            return False, "", "INVALID_URL: No valid domain found"
        normalized = f"https://{url}"
        try:
            parsed = urlparse(normalized)
            if not parsed.netloc or not parsed.scheme:
                return False, "", "INVALID_URL: Could not parse URL"
            domain_parts = parsed.netloc.split('.')
            if len(domain_parts) < 2 or len(domain_parts[-1]) < 2:
                return False, "", "INVALID_URL: Invalid domain format"
        except Exception:
            return False, "", "INVALID_URL: URL parsing failed"
        return True, normalized, ""

    def _extract_text(self, html: str, url: str) -> str:
        """Extract ALL readable text from HTML. Removes non-content elements via tags + CSS selectors."""
        try:
            soup = BeautifulSoup(html, 'lxml')

            # Remove non-content HTML elements
            for element in soup.find_all([
                'script', 'style', 'nav', 'header', 'footer',
                'aside', 'noscript', 'iframe', 'svg', 'path',
                'meta', 'link', 'form', 'button', 'input',
                'select', 'textarea'
            ]):
                element.decompose()

            # Remove elements with common non-content CSS classes/IDs
            selectors_to_remove = [
                '[class*="nav"]', '[class*="menu"]', '[class*="sidebar"]',
                '[class*="footer"]', '[class*="header"]', '[class*="cookie"]',
                '[class*="popup"]', '[class*="modal"]', '[class*="banner"]',
                '[class*="advertisement"]', '[class*="social"]', '[class*="share"]',
                '[id*="nav"]', '[id*="menu"]', '[id*="sidebar"]',
                '[id*="footer"]', '[id*="header"]', '[id*="cookie"]',
            ]
            for selector in selectors_to_remove:
                try:
                    for el in soup.select(selector):
                        el.decompose()
                except Exception:
                    pass

            # Get ALL remaining text
            text = soup.get_text(separator=' ', strip=True)
            text = re.sub(r'\s+', ' ', text).strip()

            # Max ~50KB
            if len(text) > 50000:
                text = text[:50000] + "... [truncated]"

            return text

        except Exception as e:
            logger.warning(f"Text extraction failed for {url}: {e}")
            return ""

    def _is_binary_content(self, content: str) -> bool:
        """Check if content appears to be binary/garbled."""
        if not content:
            return False
        sample = content[:1000]
        non_printable = sum(1 for c in sample if not c.isprintable() and c not in '\n\r\t ')
        return non_printable > len(sample) * 0.15

    def _get_headers(self) -> dict:
        """Realistic browser headers with rotating user agent."""
        self._request_count += 1
        ua = USER_AGENTS[self._request_count % len(USER_AGENTS)]
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }

    def _get_proxy_url(self) -> Optional[str]:
        """Build Apify residential proxy URL. Checks: user key → env var → settings."""
        import os
        from app.config import settings
        password = (self._apify_proxy_password or os.environ.get('APIFY_PROXY_PASSWORD') or getattr(settings, 'APIFY_PROXY_PASSWORD', None) or "").strip()
        if not password:
            return None
        session_id = f"scrape_{random.randint(10000, 99999)}"
        host = getattr(settings, 'APIFY_PROXY_HOST', 'proxy.apify.com')
        port = getattr(settings, 'APIFY_PROXY_PORT', 8000)
        return f"http://groups-RESIDENTIAL,session-{session_id}:{password}@{host}:{port}"

    async def scrape_website(self, url: str, timeout: int = 15, max_retries: int = 3) -> Dict[str, Any]:
        original = url
        is_valid, normalized, error = self._validate_url(url)
        if not is_valid:
            return {"success": False, "error": error, "url": original, "status_code": None}

        result = await self._fetch_url(original, normalized, timeout)

        # Retry on 429 / 5xx with exponential backoff
        retryable = result.get("status_code") in (429, 500, 502, 503, 504)
        for attempt in range(max_retries):
            if result["success"] or not retryable:
                break
            delay = (attempt + 1) * 2  # 2s, 4s
            logger.debug(f"Retry {attempt+1}/{max_retries} for {url} after {delay}s (status {result.get('status_code')})")
            await asyncio.sleep(delay)
            result = await self._fetch_url(original, normalized, timeout)
            retryable = result.get("status_code") in (429, 500, 502, 503, 504)

        # HTTP fallback
        if not result["success"] and "CONNECTION" in result.get("error", ""):
            http_url = normalized.replace("https://", "http://")
            http_result = await self._fetch_url(original, http_url, timeout)
            if http_result["success"]:
                return http_result
        return result

    async def _fetch_url(self, original: str, fetch_url: str, timeout: int) -> Dict[str, Any]:
        """Fetch URL with Apify residential proxy + full text extraction."""
        proxy_url = self._get_proxy_url()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
                verify=True,
                proxy=proxy_url,
            ) as client:
                response = await client.get(fetch_url, headers=self._get_headers())
                status_code = response.status_code

                if status_code == 403:
                    return {"success": False, "error": "BLOCKED: Access denied (403)", "url": original, "status_code": 403}
                elif status_code == 404:
                    return {"success": False, "error": "NOT_FOUND: Page does not exist (404)", "url": original, "status_code": 404}
                elif status_code == 429:
                    return {"success": False, "error": "RATE_LIMITED: Too many requests (429)", "url": original, "status_code": 429}
                elif status_code >= 500:
                    return {"success": False, "error": f"SERVER_ERROR: Website error ({status_code})", "url": original, "status_code": status_code}
                elif status_code >= 400:
                    return {"success": False, "error": f"ERROR: HTTP {status_code}", "url": original, "status_code": status_code}

                try:
                    html = response.text
                except Exception:
                    try:
                        html = response.content.decode('utf-8', errors='ignore')
                    except Exception:
                        html = response.content.decode('latin-1', errors='ignore')

                if self._is_binary_content(html):
                    return {"success": False, "error": "ENCODING_ERROR: Could not decode page content", "url": original, "status_code": status_code}

                text = self._extract_text(html, fetch_url)

                if not text or len(text) < 50:
                    return {"success": False, "error": "EMPTY: No text content (site may use JavaScript rendering)", "url": original, "status_code": status_code}

                if proxy_url:
                    get_tracker().log_apify(1, len(response.content))
                return {"success": True, "text": text, "url": original, "final_url": str(response.url), "status_code": status_code}

        except httpx.TimeoutException:
            return {"success": False, "error": f"TIMEOUT: Site did not respond within {timeout}s", "url": original, "status_code": None}
        except httpx.ConnectError as e:
            error_str = str(e).lower()
            if "ssl" in error_str or "certificate" in error_str:
                return {"success": False, "error": "SSL_ERROR: Certificate verification failed", "url": original, "status_code": None}
            if "nodename" in error_str or "getaddrinfo" in error_str or "name or service not known" in error_str:
                return {"success": False, "error": "DNS_ERROR: Website domain not found", "url": original, "status_code": None}
            return {"success": False, "error": "CONNECTION_ERROR: Could not connect to website", "url": original, "status_code": None}
        except httpx.TooManyRedirects:
            return {"success": False, "error": "REDIRECT_ERROR: Too many redirects", "url": original, "status_code": None}
        except Exception as e:
            logger.error(f"Scrape error for {original}: {e}")
            return {"success": False, "error": f"ERROR: {str(e)[:100]}", "url": original, "status_code": None}

    async def scrape_batch(
        self, urls: List[Dict[str, Any]], timeout: int = 15,
        max_concurrent: int = 100, delay: float = 0.02,
        on_result: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        """Scrape batch with adaptive concurrency via Apify residential proxies.
        Starts at max_concurrent, shrinks on 429, grows back when OK."""
        from app.services.adaptive_semaphore import get_semaphore
        sem = get_semaphore("apify", user_id=0, initial=max_concurrent, min_concurrent=10)
        results = [] if not on_result else None
        completed = 0
        total = len(urls)

        async def process(item):
            nonlocal completed
            async with sem.acquire():
                await asyncio.sleep(delay)
                try:
                    result = await self.scrape_website(item["url"], timeout=timeout)
                    result["row_id"] = item["row_id"]
                except Exception as e:
                    result = {"row_id": item["row_id"], "success": False, "error": str(e)[:100], "url": item["url"], "status_code": None}
                completed += 1
                if completed % 25 == 0:
                    logger.info(f"Scrape progress: {completed}/{total}")
                if on_result:
                    await on_result(result)
                else:
                    results.append(result)

        await asyncio.gather(*[process(item) for item in urls], return_exceptions=True)
        return results or []

    async def scrape_domains_fast(self, domains: List[str], timeout: int = 10, max_concurrent: int = 50) -> Dict[str, str]:
        """Quick parallel scrape of domains. Returns {domain: text} dict."""
        semaphore = asyncio.Semaphore(max_concurrent)
        results: Dict[str, str] = {}

        async def process(domain: str):
            async with semaphore:
                result = await self.scrape_website(domain, timeout=timeout)
                if result.get("success"):
                    results[domain] = result["text"]

        await asyncio.gather(*[process(d) for d in domains], return_exceptions=True)
        logger.info(f"Fast scrape: {len(results)}/{len(domains)} domains scraped successfully")
        return results


scraper_service = ScraperService()


from __future__ import annotations

import time
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError




@dataclass
class ProxyConfig:
    server: str
    username: Optional[str] = None
    password: Optional[str] = None


def looks_like_cf(html: str, url: str) -> bool:
    h = (html or "").lower()
    u = (url or "").lower()
    return (
        "__cf_chl_" in u
        or "cf-chl" in h
        or "cloudflare" in h and ("attention required" in h or "challenge" in h)
        or "turnstile" in h
    )


class PlaywrightFetchClient:
    def __init__(
        self,
        headless: bool,
        user_data_dir: str,
        proxy: Optional[ProxyConfig] = None,
        user_agent: Optional[str] = None,
        locale: str = "uk-UA",
        timezone_id: str = "Europe/Sofia",
        nav_timeout_ms: int = 60_000,
    ):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.proxy = proxy
        self.user_agent = user_agent
        self.locale = locale
        self.timezone_id = timezone_id
        self.nav_timeout_ms = nav_timeout_ms

        self._pw = None
        self._ctx = None

    def start(self) -> None:
        if self._pw and self._ctx:
            return
        os.makedirs(self.user_data_dir, exist_ok=True)
        self._pw = sync_playwright().start()

        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if self.proxy:
            launch_kwargs["proxy"] = {
                "server": self.proxy.server,
                **({"username": self.proxy.username} if self.proxy.username else {}),
                **({"password": self.proxy.password} if self.proxy.password else {}),
            }

        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            **launch_kwargs,
            locale=self.locale,
            timezone_id=self.timezone_id,
            viewport={"width": 1365, "height": 768},
            user_agent=self.user_agent,
            ignore_https_errors=True,
        )

    def close(self) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            self._ctx = None
        try:
            if self._pw:
                self._pw.stop()
        finally:
            self._pw = None

    def fetch(self, url: str, wait_ms: int = 3000) -> Dict[str, Any]:
        self.start()
        t0 = time.time()
        page = self._ctx.new_page()

        # basic anti-detection tweak
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        status = 0
        final_url = url
        html = ""
        title = ""
        headers: Dict[str, str] = {}
        error: Optional[str] = None

        try:
            resp = page.goto(url, wait_until="networkidle", timeout=self.nav_timeout_ms)
            if resp is not None:
                status = resp.status
                headers = {k.lower(): v for k, v in resp.headers.items()}
            final_url = page.url

            page.wait_for_timeout(wait_ms)
            html = page.content()
            title = page.title()

            # If CF challenge detected, wait longer and re-check once
            if looks_like_cf(html, final_url):
                page.wait_for_timeout(8000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass
                final_url = page.url
                html = page.content()
                title = page.title()

        except PWTimeoutError:
            error = "Timeout"
        except Exception as e:
            error = str(e)
        finally:
            try:
                page.close()
            except Exception:
                pass

        blocked = looks_like_cf(html, final_url) or status in (401, 403, 429)
        return {
            "status": status,
            "final_url": final_url,
            "html": html,
            "headers": headers,
            "title": title,
            "error": error,
            "blocked": blocked,
            "latency_s": round(time.time() - t0, 3),
        }


if __name__ == "__main__":
    url = 'https://sovajewels.com/catalog/koltsa/koltso-iz-belogo-zolota-i-keramiki-smart-beautiful-artikul-110474820202.html'
    pc = ProxyConfig(server="http://gate.decodo.com:10001", username="sp9tlfl8r5", password="Xsuok87G~1bd3TouSp")

    # seed = PlaywrightFetchClient(headless=False, user_data_dir=".pw_sova", proxy=pc)
    seed = PlaywrightFetchClient(headless=False, user_data_dir=".pw_sova")
    res = seed.fetch(url)
    print(res["status"], res["blocked"], res["final_url"])
    if res.get("html"):
        with open("debug_sova_seed.html", "w", encoding="utf-8") as f:
            f.write(res["html"])
        print("Saved HTML to debug_sova_seed.html")

    seed.close()
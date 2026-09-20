from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Page, Route, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "app" / "frontend"
VALID_CODE = "0000000-00.2026.8.21.0001"
TOKEN = "browser-smoke-token"


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def ready_payload(*, markdown: str = "Resumo pronto no navegador real.") -> dict:
    return {
        "code": VALID_CODE,
        "class_name": "Procedimento Comum",
        "court": "TJRS",
        "updated_at": "2026-09-20T12:00:00Z",
        "summary_status": "available",
        "summary": {
            "markdown": markdown,
            "created_at": "2026-09-20T12:00:00Z",
        },
        "parties": [{"name": "Parte Sintética", "side": "active", "person_type": "person"}],
        "subjects": [{"code": "1", "name": "Obrigação"}],
        "header": {"instance": 1, "county": "Porto Alegre", "amount": 1234.56},
        "recent_steps": [
            {
                "title": "Sentença",
                "text": "Movimento público sintético.",
                "occurred_at": "2026-09-20T10:00:00Z",
            }
        ],
    }


def fulfill_json(route: Route, status: int, payload: dict) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False),
    )


def configure_ready_api(page: Page, payload: dict) -> list[dict]:
    requests: list[dict] = []

    def handler(route: Route) -> None:
        request = route.request
        requests.append(
            {
                "method": request.method,
                "url": request.url,
                "authorization": request.headers.get("authorization"),
            }
        )
        if request.method == "GET" and "/processes/" in request.url:
            fulfill_json(route, 200, payload)
            return
        fulfill_json(route, 500, {"detail": "unexpected request"})

    page.route("**/processes/**", handler)
    return requests


def fill_lookup(page: Page) -> None:
    page.locator("#bearer-token").fill(TOKEN)
    page.locator("#process-code").fill(VALID_CODE)
    page.locator("#search-submit").click()


def run() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FrontendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            console_errors: list[str] = []
            page_errors: list[str] = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            page.goto(origin, wait_until="networkidle")
            assert page.title() == "Rpy — Inteligência processual"
            assert page.locator("#search-submit").is_visible()
            assert page.locator("#bearer-token").get_attribute("type") == "password"

            requests = configure_ready_api(page, ready_payload())
            fill_lookup(page)
            page.locator("#result").wait_for(state="visible")
            assert "Resumo pronto no navegador real." in page.locator("#summary-body").inner_text()
            assert "Movimento público sintético." in page.locator("#movements-list").inner_text()
            assert page.locator("#result-code").inner_text() == VALID_CODE
            assert requests and requests[0]["method"] == "GET"
            assert requests[0]["authorization"] == f"Bearer {TOKEN}"
            assert TOKEN not in requests[0]["url"]

            page.unroute("**/processes/**")
            malicious = (
                "# Resumo do processo\n"
                "<ProcessHeader className=\"process-header\">\n"
                f"- Processo: {VALID_CODE}\n"
                "</ProcessHeader>\n"
                "## Partes\n"
                "<Party name=\"Parte Sintética\" />\n"
                "<img src=x onerror=alert(1)>"
            )
            configure_ready_api(page, ready_payload(markdown=malicious))
            fill_lookup(page)
            page.locator("#result").wait_for(state="visible")
            rendered = page.locator("#summary-body").inner_text()
            assert "<img src=x onerror=alert(1)>" in rendered
            assert page.locator("#summary-body img").count() == 0
            assert page.locator('[data-component="ProcessHeader"]').count() == 1
            assert page.locator('[data-component="Party"]').inner_text() == "Parte Sintética"
            assert console_errors == [], console_errors
            console_errors.clear()

            page.unroute("**/processes/**")

            def missing_handler(route: Route) -> None:
                request = route.request
                if request.method == "GET":
                    fulfill_json(route, 404, {"detail": "not found"})
                    return
                if request.method == "POST":
                    fulfill_json(route, 202, {"status": "pending", "created": True})
                    return
                fulfill_json(route, 500, {"detail": "unexpected request"})

            page.route("**/processes/**", missing_handler)
            fill_lookup(page)
            page.locator("#request-process").wait_for(state="visible")
            page.locator("#request-process").click()
            page.wait_for_function(
                'document.querySelector("#status-title").textContent.includes("Consulta registrada")'
            )

            assert all(
                "Failed to load resource: the server responded with a status of 404" in error
                for error in console_errors
            ), console_errors
            assert page_errors == [], page_errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    run()
    print("frontend browser smoke: PASS")

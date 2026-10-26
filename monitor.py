"""Daily monitoring of Skifteportalen API specifications via a Render Cron Job."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

PAGE_URL = (
    "https://www.domstol.dk/selvbetjening/blanketter-og-vejledninger/"
    "minskiftesagdk/api-tilslutning-til-skifteportalen/"
)
# The publisher has also served integration guides through this older page.
EXTRA_PAGES = [x.strip() for x in os.getenv("EXTRA_PAGES", "").split(",") if x.strip()]
KEYWORDS = re.compile(r"(api|openapi|swagger|integration|skifteportal|specifikation|schema|teknisk|vejledning)", re.I)
EXTENSIONS = (".pdf", ".yaml", ".yml", ".json", ".zip", ".wsdl", ".xsd")
MAX_FILES = int(os.getenv("MAX_FILES", "40"))
MAX_BYTES = int(os.getenv("MAX_BYTES", str(25 * 1024 * 1024)))
TIMEOUT = 25
USER_AGENT = "SkifteportalenSpecMonitor/1.0 (+daily check; contact via deployment owner)"
GITHUB_API = "https://api.github.com"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("monitor")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_bytes(session: requests.Session, url: str) -> bytes:
    for attempt in range(3):
        try:
            with session.get(url, timeout=TIMEOUT, stream=True) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError(f"Document exceeds MAX_BYTES: {url}")
                    chunks.append(chunk)
                return b"".join(chunks)
        except (requests.RequestException, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def normalize_page(html: bytes, base: str) -> tuple[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "footer", "nav", "header"]):
        tag.decompose()
    main = soup.select_one("main") or soup.select_one("article") or soup.body or soup
    text = " ".join(main.stripped_strings)
    urls = {}
    for tag in main.select("a[href]"):
        href = urljoin(base, tag.get("href", "").strip()).split("#", 1)[0]
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        label = " ".join(tag.stripped_strings)
        path = parsed.path.lower()
        if path.endswith(EXTENSIONS) and (KEYWORDS.search(href + " " + label) or path.endswith((".yaml", ".yml", ".json", ".wsdl", ".xsd"))):
            urls[href] = label[:180]
    return text, urls


def normalize_document(url: str, data: bytes) -> tuple[str, str]:
    """Returns (semantic fingerprint, source type). Fall back to bytes if parsing fails."""
    ext = urlparse(url).path.lower()
    if ext.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            text = " ".join(" ".join(page.extract_text() or "" for page in reader.pages).split())
            if text:
                return digest(text.encode("utf-8")), "pdf-text"
        except Exception as exc:
            log.warning("PDF text extraction failed for %s: %s", url, exc)
        return digest(data), "pdf-bytes"
    if ext.endswith(".json"):
        try:
            data = json.dumps(json.loads(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            return digest(data), "json-canonical"
        except (ValueError, UnicodeDecodeError):
            pass
    if ext.endswith((".yaml", ".yml", ".wsdl", ".xsd")):
        return digest(b"\n".join(line.rstrip() for line in data.replace(b"\r\n", b"\n").split(b"\n"))), "text"
    return digest(data), "bytes"


def scan(session: requests.Session) -> dict:
    records: dict[str, dict] = {}
    discovered: dict[str, str] = {}
    errors = []
    pages_ok = 0
    for page in [PAGE_URL, *EXTRA_PAGES]:
        try:
            html = get_bytes(session, page)
            text, links = normalize_page(html, page)
            records[page] = {"hash": digest(text.encode()), "kind": "page-text", "label": "Source page"}
            discovered.update(links)
            pages_ok += 1
        except Exception as exc:
            errors.append(f"Page failed: {page}: {exc}")
    for url, label in sorted(discovered.items())[:MAX_FILES]:
        try:
            data = get_bytes(session, url)
            fingerprint, kind = normalize_document(url, data)
            records[url] = {"hash": fingerprint, "kind": kind, "label": label}
        except Exception as exc:
            errors.append(f"Document failed: {url}: {exc}")
    if pages_ok == 0:
        raise RuntimeError("Could not access any source page: " + "; ".join(errors))
    # Preserve failed documents from old state later to prevent false removal alerts.
    return {"records": records, "errors": errors, "checked_at": datetime.now(timezone.utc).isoformat()}


def github_headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def github_state(session: requests.Session) -> tuple[dict | None, str | None]:
    repo = os.environ["GITHUB_REPO"]
    path = os.getenv("STATE_PATH", "monitoring/skifteportalen-state.json")
    branch = os.getenv("GITHUB_BRANCH", "main")
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    response = session.get(url, headers=github_headers(), params={"ref": branch}, timeout=TIMEOUT)
    if response.status_code == 404:
        return None, None
    response.raise_for_status()
    obj = response.json()
    import base64
    return json.loads(base64.b64decode(obj["content"])), obj["sha"]


def save_state(session: requests.Session, state: dict, sha: str | None, message: str) -> None:
    import base64
    repo = os.environ["GITHUB_REPO"]
    path = os.getenv("STATE_PATH", "monitoring/skifteportalen-state.json")
    payload = {"message": message, "branch": os.getenv("GITHUB_BRANCH", "main"),
               "content": base64.b64encode((json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()).decode()}
    if sha:
        payload["sha"] = sha
    result = session.put(f"{GITHUB_API}/repos/{repo}/contents/{path}",
                         headers=github_headers(), json=payload, timeout=TIMEOUT)
    result.raise_for_status()


def diff(old: dict, new: dict) -> list[tuple[str, str]]:
    changes = []
    for url in sorted(set(old) | set(new)):
        if url not in old:
            changes.append(("ADDED", url))
        elif url not in new:
            changes.append(("REMOVED", url))
        elif old[url]["hash"] != new[url]["hash"]:
            changes.append(("CHANGED", url))
    return changes


def notify(session: requests.Session, title: str, lines: list[str]) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    message = title + "\n" + "\n".join(lines)
    log.info("%s", message)
    if webhook:
        # Discord content is limited to 2,000 chars.
        response = session.post(webhook, json={"content": message[:1950]}, timeout=TIMEOUT)
        response.raise_for_status()


def main() -> int:
    if not os.getenv("GITHUB_TOKEN") or not os.getenv("GITHUB_REPO"):
        log.error("Set GITHUB_TOKEN and GITHUB_REPO")
        return 2
    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            previous, sha = github_state(session)
            current = scan(session)
            old_records = previous.get("records", {}) if previous else {}
            # A transient failed download should not register as a deletion.
            failed_urls = set()
            for msg in current["errors"]:
                for url in old_records:
                    if url in msg:
                        failed_urls.add(url)
            for url in failed_urls:
                current["records"].setdefault(url, old_records[url])
            changes = diff(old_records, current["records"]) if previous else []
            if previous is None:
                notify(session, "Skifteportalen API monitor initialized", [f"Tracking {len(current['records'])} resources."])
            elif changes:
                notify(session, f"Skifteportalen API: {len(changes)} change(s)",
                       [f"{status}: {url}" for status, url in changes])
            if current["errors"]:
                notify(session, "Skifteportalen API: fetch warnings", current["errors"])
            # Persist after notification so failures can be retried rather than silently lost.
            if previous is None or changes or current["errors"] or previous.get("errors"):
                save_state(session, current, sha, "Monitor Skifteportalen specification sources")
            else:
                log.info("No changes; %d resources checked", len(current["records"]))
            return 1 if current["errors"] else 0
        except Exception:
            log.exception("Monitoring failed")
            return 1


if __name__ == "__main__":
    sys.exit(main())

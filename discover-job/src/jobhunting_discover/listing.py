"""Guest-only listing sources. Each result retains its full, literal posting URL."""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stderr

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from jobhunting_core.postings import job_key


CANADA = re.compile(r"\b(?:Canada|Canadian|Toronto|Vancouver|Montreal|Montréal|Ottawa|Waterloo|Calgary|Kitchener|Victoria|Edmonton|Winnipeg|Halifax|Québec|Quebec|ON|BC|QC|AB|MB|SK|NS|NB|NL|PE|NT|NU|YT)\b", re.I)
PARTIAL_LOG = re.compile(r"\b429\b|rate.?limit|fail(?:ed|ure)?\b|error\b|stop(?:ped|ping)? early|early stop|could not fetch|timed? out", re.I)


def canada_location(value: str) -> bool:
    return bool(CANADA.search(value))


def jobspy_log_partial(log: str) -> bool:
    return bool(PARTIAL_LOG.search(log))


def plain(value: str) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text("\n", strip=True)


def session() -> requests.Session:
    client = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET", "POST"])
    client.mount("https://", HTTPAdapter(max_retries=retry))
    client.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"})
    return client


def request_json(client: requests.Session, method: str, url: str, **kwargs) -> dict:
    response = client.request(method, url, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def record(source: str, native_id: str, url: str, title: str, location: str, posted: str, description: str, *, company: str | None = None, detail_url: str = "") -> dict:
    if not url or not title or not location:
        raise ValueError(f"Missing listing identity: {source} {native_id} {url}")
    return {"source": source, "source_id": str(native_id), "url": url, "company": company or source,
            "title": title.strip(), "location": location, "posted": str(posted or ""),
            "description": plain(description), "detail_url": detail_url, "capture_log": ""}


def parse_ats(kind: str, payload: dict, company: str, board: str, base: str = "") -> list[dict]:
    rows = []
    if kind == "ashby":
        for job in payload["jobs"]:
            locations = [job["location"], *(item["location"] for item in job.get("secondaryLocations", []))]
            # Ashby sends "address": null for some postings (2026-11-09)
            addresses = [job.get("address") or {}, *(item.get("address") or {} for item in job.get("secondaryLocations", []))]
            if not job["isListed"] or not (any(canada_location(loc) for loc in locations) or any((a.get("postalAddress") or {}).get("addressCountry") in ("CA", "Canada") for a in addresses)):
                continue
            rows.append(record(company, job["id"], job["jobUrl"], job["title"], " | ".join(locations), job.get("publishedAt", ""), job.get("descriptionPlain") or job.get("descriptionHtml", "")))
    elif kind == "lever":
        for job in payload:
            categories = job.get("categories") or {}
            locations = [loc for loc in categories.get("allLocations") or [categories.get("location")] if loc]
            if job.get("country") != "CA" and not any(canada_location(loc) for loc in locations):
                continue
            sections = [f"{item.get('text', '')}\n{plain(item.get('content', ''))}" for item in job.get("lists") or []]
            description = "\n\n".join(part for part in (job.get("descriptionPlain"), *sections, job.get("additionalPlain")) if part)
            rows.append(record(company, job["id"], job["hostedUrl"], job["text"], " | ".join(locations) or "Canada",
                               job.get("createdAt", ""), description))
    elif kind == "greenhouse":
        for job in payload["jobs"]:
            location = job["location"]["name"]
            if canada_location(location):
                rows.append(record(company, job["id"], job["absolute_url"], job["title"], location, job.get("first_published") or job.get("updated_at", ""), job.get("content", "")))
    elif kind == "workable":
        for job in payload["jobs"]:
            locations = job.get("locations", [])
            location = " | ".join(f"{item.get('city', '')}, {item.get('country', '')}" for item in locations) or f"{job.get('city', '')}, {job.get('country', '')}"
            if job.get("country") == "Canada" or any(item.get("country") == "Canada" for item in locations):
                rows.append(record(company, job["shortcode"], job["url"], job["title"], location, job.get("published_on", ""), job.get("description", "")))
    elif kind == "workday":
        for job in payload["jobPostings"]:
            path = job["externalPath"]
            location = job.get("locationsText") or ""  # iA Financial Group leaves it out on some postings
            if not canada_location(location):
                location = f"Canada (ATS facet); {location}".rstrip("; ")
            fields = job.get("bulletFields") or [path.rsplit("_", 1)[-1]]
            rows.append(record(company, fields[0], f"{base}/en-US/{board}{path}", job["title"], location, job.get("postedOn", ""), "", detail_url=f"{base}/wday/cxs/{base.split('//')[1].split('.')[0]}/{board}{path}"))
    elif kind == "phenom":
        for job in payload["data"]["jobs"]:
            location = job.get("location", "")
            if job.get("country") in ("Canada", "CAN", "CA") or canada_location(location):
                url = f"{base}/job/{job['jobId']}/{re.sub('[^A-Za-z0-9]+', '-', job['title']).strip('-')}"
                rows.append(record(company, job["jobId"], url, job["title"], location, job.get("postedDate", ""), "", detail_url=url))
    elif kind == "eightfold":
        for job in payload["positions"]:
            locations = job["locations"]
            location = " | ".join(locations)
            if not canada_location(location):
                location = f"Canada (ATS filter); {location}"
            url = f"https://apply.careers.microsoft.com{job['positionUrl']}"
            rows.append(record(company, job["id"], url, job["name"], location, job.get("postedTs", ""), "", detail_url=url))
    else:
        raise ValueError(f"Unsupported ATS: {kind}")
    for row in rows:
        row["capture_log"] = f"ATS {kind}; board={board}; list parsed"
    return rows


def canada_facet(facets: list[dict]) -> tuple[str, str]:
    def walk(items: list[dict], parameter: str) -> tuple[str, str] | None:
        for item in items:
            if item.get("descriptor") == "Canada" and item.get("id"):
                return parameter, item["id"]
            found = walk(item.get("values", []), item.get("facetParameter", parameter))
            if found:
                return found
        return None

    for group in facets:
        # Salesforce names its country facet "CF_-_REC_-_..._Country_from_Job_Posting_Location_Extended"
        if group.get("facetParameter") in ("Country", "locationMainGroup", "locationHierarchy1") or group.get("descriptor") == "Country":
            found = walk(group.get("values", []), group["facetParameter"])
            if found:
                return found
    raise ValueError("Canada facet absent")


def posted_days_ago(value, now: datetime) -> float | None:
    """Days since a listing's posted value: ISO dates, epoch seconds, or Workday's "Posted 3 Days Ago".
    None when it cannot be read; such postings are kept."""
    text = str(value or "").strip()
    lower = text.lower()
    if not text:
        return None
    if re.fullmatch(r"\d{10}|\d{13}", text):
        return (now.timestamp() - int(text) / (1000 if len(text) == 13 else 1)) / 86400
    if "today" in lower:
        return 0
    if "yesterday" in lower:
        return 1
    if match := re.search(r"(\d+)(\+?)\s*days?\s*ago", lower):
        return int(match[1]) + (1 if match[2] else 0)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))).total_seconds() / 86400


def fetch_ats(company: str, config: dict, keep=None) -> dict:
    """All of a company's Canadian postings with full JDs. `keep` drops postings before their JDs are fetched."""
    started = time.monotonic()
    client = session()
    kind = config["type"]
    rows, logs = [], []
    boards = config.get("boards", [""])
    for board in boards:
        if kind == "ashby":
            payload = request_json(client, "GET", f"https://api.ashbyhq.com/posting-api/job-board/{board}")
            rows.extend(parse_ats(kind, payload, company, board))
            logs.append(f"{board}: {len(payload['jobs'])} global")
        elif kind == "greenhouse":
            payload = request_json(client, "GET", f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
            rows.extend(parse_ats(kind, payload, company, board))
            logs.append(f"{board}: {len(payload['jobs'])} global")
        elif kind == "lever":
            # EU accounts (ADAPTOVATE) live on api.eu.lever.co
            payload = request_json(client, "GET", f"https://{config.get('host', 'api.lever.co')}/v0/postings/{board}?mode=json")
            rows.extend(parse_ats(kind, payload, company, board))
            logs.append(f"{board}: {len(payload)} global")
        elif kind == "workable":
            payload = request_json(client, "GET", f"https://apply.workable.com/api/v1/widget/accounts/{board}?details=true")
            rows.extend(parse_ats(kind, payload, company, board))
            logs.append(f"{board}: {len(payload['jobs'])} global")
        elif kind == "workday":
            base = f"https://{config['tenant']}.{config['instance']}.myworkdayjobs.com"
            url = f"{base}/wday/cxs/{config['tenant']}/{board}/jobs"
            def post(offset: int, facets: dict) -> dict:
                return request_json(client, "POST", url, json={"limit": 20, "offset": offset, "searchText": "", "appliedFacets": facets})
            first = post(0, {})
            if config.get("canada_only"):
                # Canadian employers (Desjardins, iA, AIMCo) whose board has no country facet: every posting is in Canada
                applied, page = {}, first
            else:
                facet, value = canada_facet(first["facets"])
                applied = {facet: [value]}
                page = post(0, applied)
            total = page["total"]
            collected = list(page["jobPostings"])
            for offset in range(20, total, 20):
                collected.extend(post(offset, applied)["jobPostings"])
                time.sleep(.25)
            if len(collected) != total:
                raise ValueError(f"{company}/{board}: pagination {len(collected)} != {total}")
            if len({item["externalPath"] for item in collected}) != total:
                raise ValueError(f"{company}/{board}: repeated Workday pages; coverage incomplete")
            parsed = parse_ats(kind, {"jobPostings": collected}, company, board, base)
            if len(parsed) != total:
                raise ValueError(f"{company}/{board}: Canada facet returned {total}, parsed {len(parsed)}")
            rows.extend(parsed)
            logs.append(f"{board}: {total} Canada")
        elif kind == "phenom":
            base = f"https://{config['host']}/{config['prefix']}"
            body = {"lang": "en_global", "deviceType": "desktop", "country": "global", "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "jobs": True, "counts": True, "all_fields": ["category", "country", "city"], "size": 100, "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page10", "siteType": "external", "keywords": "", "global": True, "selected_fields": {}, "locationData": {}}
            collected, total = [], None
            for offset in range(0, 10000, 100):
                payload = request_json(client, "POST", f"https://{config['host']}/widgets", json={**body, "from": offset})["refineSearch"]
                if total is None:
                    total = payload["totalHits"]
                collected.extend(payload["data"]["jobs"])
                if len(collected) >= total:
                    break
                time.sleep(.15)
            if len(collected) != total:
                raise ValueError(f"{company}: pagination {len(collected)} != {total}")
            if len({str(item["jobId"]) for item in collected}) != total:
                raise ValueError(f"{company}: repeated Phenom pages; coverage incomplete")
            rows.extend(parse_ats(kind, {"data": {"jobs": collected}}, company, board, base))
            logs.append(f"{total} global")
        elif kind == "eightfold":
            collected, total = [], None
            for offset in range(0, 10000, 10):
                payload = request_json(client, "GET", f"https://{config['host']}/api/pcsx/search", params={"domain": config["domain"], "location": "Canada", "start": offset})["data"]
                if total is None:
                    total = payload["count"]
                collected.extend(payload["positions"])
                if len(collected) >= total:
                    break
                time.sleep(.4)
            if len(collected) != total:
                raise ValueError(f"{company}: pagination {len(collected)} != {total}")
            if len({str(item["id"]) for item in collected}) != total:
                raise ValueError(f"{company}: repeated Eightfold pages; coverage incomplete")
            parsed = parse_ats(kind, {"positions": collected}, company, board)
            if len(parsed) != total:
                raise ValueError(f"{company}: Canada filter returned {total}, parsed {len(parsed)}")
            rows.extend(parsed)
            logs.append(f"{total} Canada")
        else:
            raise ValueError(f"Unsupported ATS: {kind}")

    if keep is not None:
        kept = [row for row in rows if keep(row)]
        logs.append(f"skipped {len(rows) - len(kept)}: already recorded, outside the date window, or title not a technical role")
        rows = kept
    # Complete JD text is required before a candidate can be judged.
    unique = {job_key(row["url"]): row for row in rows}
    detail_rows = [row for row in unique.values() if row["detail_url"]]
    detail_client = threading.local()
    def enrich(row: dict) -> tuple[dict, str]:
        try:
            if not hasattr(detail_client, "session"):
                detail_client.session = session()
            if kind == "workday":
                payload = request_json(detail_client.session, "GET", row["detail_url"])
                row["description"] = plain(payload["jobPostingInfo"]["jobDescription"])
                row["posted"] = payload["jobPostingInfo"].get("startDate", row["posted"])
            else:
                response = detail_client.session.get(row["detail_url"], timeout=30)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                for data in soup.find_all("script", type="application/ld+json"):
                    try:
                        parsed = json.loads(data.string or data.get_text())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, list):
                        parsed = next((item for item in parsed if isinstance(item, dict) and item.get("@type") == "JobPosting"), {})
                    if isinstance(parsed, dict) and parsed.get("@type") == "JobPosting":
                        row["description"] = plain(parsed.get("description", ""))
                        break
                if not row["description"] and kind == "phenom" and "phApp.ddo = " in response.text:
                    start = response.text.index("phApp.ddo = ") + len("phApp.ddo = ")
                    detail, _ = json.JSONDecoder().raw_decode(response.text[start:])
                    row["description"] = plain(detail["jobDetail"]["data"]["job"]["description"])
                if not row["description"]:
                    for selector in ("[class*='job-description']", "[class*='jobDescription']", "#job-description"):
                        element = soup.select_one(selector)
                        if element:
                            row["description"] = element.get_text("\n", strip=True)
                            break
                if kind == "eightfold" and "Ce poste sera ouvert pendant au moins cinq jours" in row["description"] and not any(
                    heading in row["description"] for heading in ("Responsibilities", "Responsabilités", "Qualifications", "Vos missions", "Rejoignez")
                ):
                    row["description_fragment"] = row["description"]
                    row["description"] = ""
            row["capture_log"] += "; detail fetched" if row["description"] else "; JD absent"
            return row, "" if row["description"] else f"{row['url']}: JD absent"
        except (requests.RequestException, KeyError, ValueError) as error:
            row["capture_log"] += f"; detail failed: {type(error).__name__}: {error}"
            return row, f"{row['url']}: {type(error).__name__}: {error}"

    failures = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in as_completed([pool.submit(enrich, row) for row in detail_rows]):
            _, error = future.result()
            if error:
                failures.append(error)
    for row in unique.values():
        row["source"] = f"{company} Careers"
    logs.extend(failures)
    return {"source": f"{company} Careers", "status": "partial" if failures else "completed", "jobs": list(unique.values()),
            "logs": logs, "elapsed_s": round(time.monotonic() - started, 2)}


# Indeed stops returning results at about 880 per query (seen 2026-10-29 for several broad queries
# over 30-90 days); a query at this size may be truncated.
INDEED_TRUNCATION_ROWS = 850


# A technical role word in the title. Indeed cannot AND a group of ORs (2026-10-29 test), so
# campus-signal queries are fetched alone and this filter is applied here.
# Words come from titles of jobs the user accepted (Jobs sheet, 2026-10-29); loose on purpose, since
# a wrongly kept title only costs one more JD read.
TECH_TITLE = re.compile(r"software|developer|engineer|programmer|machine learning|\bml\b|\bai\b|artificial intelligence|llm|nlp|"
                        r"\bdata\b|scientist|research|firmware|embedded|\bqa\b|quality assurance|\btest|"
                        r"technolog|technical|analy|architect|robotic|\bios\b|automation|systems|designer|devops|cloud", re.I)


def fetch_indeed(queries: list[str], days: int = 7, title_filtered: list[str] = ()) -> dict:
    """Fetch every query; for queries in title_filtered keep only rows whose title has a technical role word."""
    from jobspy import scrape_jobs

    started = time.monotonic()
    jobs, logs, partial = {}, [], False
    for index, query in enumerate([*title_filtered, *queries]):
        if index:
            time.sleep(10)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.INFO)
        try:
            with redirect_stderr(stream):
                frame = scrape_jobs(site_name=["indeed"], search_term=query, location="Canada", country_indeed="Canada", results_wanted=1000, hours_old=days * 24, fetch_description=True, description_format="markdown", verbose=2)
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        query_log = stream.getvalue()
        query_partial = jobspy_log_partial(query_log)
        if len(frame) >= INDEED_TRUNCATION_ROWS:
            # Results past Indeed's cap are invisible; the query must be split to see them.
            query_partial = True
            query_log += f"\nreturned {len(frame)} rows, at Indeed's result cap: later results are unseen; split this query (by province, role or days)"
        logs.append({"query": query, "rows": len(frame), "partial": query_partial, "log": query_log})
        partial |= query_partial
        records = frame.to_dict(orient="records")
        if query in title_filtered:
            kept = [raw for raw in records if TECH_TITLE.search(str(raw.get("title") or ""))]
            logs.append(f"{query}: skipped {len(records) - len(kept)} whose title has no technical-role word")
            records = kept
        for raw in records:
            location = ", ".join(str(value) for field in ("location", "city", "state", "country")
                                  if (value := raw.get(field)) is not None and str(value) != "nan")
            if not canada_location(location):
                continue
            description = raw.get("description")
            row = record("Indeed Canada", raw.get("id", ""), raw["job_url"], raw["title"], location, raw.get("date_posted", ""),
                         description if isinstance(description, str) else "", company=raw["company"])
            key = job_key(row["url"])
            if key in jobs:
                # Keep every query that found this posting.
                jobs[key]["query"] += f"; {query}"
                continue
            row["query"] = query
            row["capture_log"] = f"JobSpy query={query}; last {days} days; partial={query_partial}; see source log"
            if not row["description"]:
                partial = True
                logs.append({"query": query, "partial": True, "log": f"JD absent: {row['url']}"})
            jobs[key] = row
    return {"source": "Indeed Canada", "status": "partial" if partial else "completed", "jobs": list(jobs.values()),
            "logs": logs, "elapsed_s": round(time.monotonic() - started, 2)}

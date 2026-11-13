"""One posting, one key: postings and Jobs rows are matched by the source job ID in their URL."""
import re
from urllib.parse import parse_qs, urlsplit


def job_key(url: str) -> str:
    """Source and native job ID, so links that differ only in tracking parameters match; the URL itself without an ID."""
    parsed = urlsplit(url)
    host, path = (parsed.hostname or "").lower(), parsed.path
    query = parse_qs(parsed.query)
    def host_is(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)

    source, native_id = "", ""
    if host_is("linkedin.com"):
        source = "linkedin"
        match = re.search(r"/jobs/view/(?:.*?-)?(\d+)(?:/|$)", path)
        native_id = match.group(1) if match else query.get("currentJobId", [""])[0]
    elif re.fullmatch(r"(?:[a-z0-9-]+\.)*indeed\.[a-z]{2,}", host):
        source, native_id = "indeed", query.get("jk", [""])[0]
    elif host_is("greenhouse.io") or "gh_jid" in query:
        source = "greenhouse"
        match = re.search(r"/jobs/(\d+)", path)
        native_id = query.get("gh_jid", [match.group(1) if match else ""])[0]
    elif host_is("lever.co"):
        source = "lever"
        match = re.search(r"/(?:jobs/)?([0-9a-f]{8}-[0-9a-f-]{27,})/?$", path, re.I)
        native_id = match.group(1) if match else ""
    elif host_is("ashbyhq.com"):
        source = "ashby"
        match = re.search(r"/([0-9a-f]{8}-[0-9a-f-]{27,})/?$", path, re.I)
        native_id = query.get("ashby_jid", [match.group(1) if match else ""])[0]
    elif host_is("myworkdayjobs.com"):
        source = f"workday:{host.split('.')[0]}"
        match = re.search(r"_([A-Za-z0-9-]+)$", path)
        native_id = re.sub(r"-(\d+)$", "", match.group(1)) if match else ""
    elif host_is("workable.com"):
        source = "workable"
        match = re.search(r"/j/([A-Za-z0-9]+)", path)
        native_id = match.group(1) if match else ""
    elif host_is("eightfold.ai") or host_is("careers.microsoft.com"):
        source = "eightfold"
        match = re.search(r"/job/(\d+)", path)
        native_id = match.group(1) if match else ""
    elif host_is("deloitte.ca"):
        source = "successfactors:deloitte"
        match = re.search(r"/(\d+)-(?:en|fr)_[A-Z]{2}", path)
        native_id = match.group(1) if match else ""
    elif host_is("jobs.bmo.com") or host_is("careers.opentext.com"):
        source = f"phenom:{host}"
        match = re.search(r"(R\d{9}|/job/(\d+))(?:/|[A-Z]|$)", path)
        native_id = (match.group(2) or match.group(1)) if match else ""
    return f"{source}:{native_id.lower()}" if native_id else url

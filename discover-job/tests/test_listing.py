import json

import pytest

from jobhunting_discover import listing
from jobhunting_discover.listing import jobspy_log_partial, parse_ats


@pytest.mark.parametrize("log", ["HTTP 429 on page 3", "failed to fetch job details", "stopped early", "early stop", "ERROR: page unavailable"])
def test_jobspy_partial(log):
    assert jobspy_log_partial(log)
    assert not jobspy_log_partial("Indeed finished scraping; 69 jobs returned")


@pytest.mark.parametrize("kind,payload,board,base", [
    ("ashby", {"jobs": [{"id": "uuid", "jobUrl": "https://jobs.ashbyhq.com/a/uuid", "title": "Engineer", "location": "Toronto", "secondaryLocations": [], "isListed": True, "descriptionPlain": "Build systems"}]}, "a", ""),
    ("greenhouse", {"jobs": [{"id": 123, "absolute_url": "https://job-boards.greenhouse.io/a/jobs/123", "title": "Engineer", "location": {"name": "Canada"}, "content": "<p>Build systems</p>"}]}, "a", ""),
    ("workable", {"jobs": [{"shortcode": "A1", "url": "https://apply.workable.com/j/A1", "title": "Engineer", "country": "Canada", "city": "Toronto", "locations": [], "description": "<p>Build systems</p>"}]}, "a", ""),
    ("workday", {"jobPostings": [{"externalPath": "/job/Toronto/Engineer_JR123", "title": "Engineer", "locationsText": "Toronto, ON", "bulletFields": ["JR123"]}]}, "a", "https://a.wd3.myworkdayjobs.com"),
    ("phenom", {"data": {"jobs": [{"jobId": "R123456789", "title": "Engineer", "location": "Toronto, ON", "country": "Canada"}]}}, "", "https://jobs.example.com/ca/en"),
    ("eightfold", {"positions": [{"id": 123, "positionUrl": "/careers/job/123", "name": "Engineer", "locations": ["Canada, Ontario, Toronto"]}]}, "", ""),
])
def test_ats_parser(kind, payload, board, base):
    rows = parse_ats(kind, payload, "Acme", board, base)
    assert len(rows) == 1
    assert rows[0]["title"] == "Engineer"
    if kind in {"ashby", "greenhouse", "workable"}:
        assert rows[0]["description"] == "Build systems"
    else:
        assert rows[0]["detail_url"]


def test_workday_canada_facet_keeps_generic_location_rows():
    rows = parse_ats("workday", {"jobPostings": [{"externalPath": "/job/Remote/Engineer_JR123",
                            "title": "Engineer", "locationsText": "Multiple Locations"}]},
                     "Acme", "site", "https://acme.wd3.myworkdayjobs.com")
    assert len(rows) == 1
    assert rows[0]["location"] == "Canada (ATS facet); Multiple Locations"


def test_eightfold_boilerplate_without_duties_is_partial(monkeypatch):
    class Response:
        def __init__(self, payload=None, text=""):
            self.payload, self.text = payload, text

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Client:
        def request(self, method, url, **kwargs):
            return Response({"data": {"count": 1, "positions": [{"id": 123, "positionUrl": "/careers/job/123",
                            "name": "Intern", "locations": ["Canada, Toronto"]}]}})

        def get(self, url, **kwargs):
            fragment = "Ce poste sera ouvert pendant au moins cinq jours. Microsoft offre l'égalité d'accès à l'emploi."
            return Response(text=f'<script type="application/ld+json">{json.dumps({"@type": "JobPosting", "description": fragment})}</script>')

    monkeypatch.setattr(listing, "session", Client)
    result = listing.fetch_ats("Microsoft Canada", {"type": "eightfold", "host": "microsoft.eightfold.ai", "domain": "microsoft.com"})
    assert result["status"] == "partial"
    assert result["jobs"][0]["description"] == ""
    assert "Ce poste" in result["jobs"][0]["description_fragment"]


def test_fetch_indeed_uses_day_window_and_keeps_every_query(monkeypatch):
    import sys, types
    import pandas as pd
    from jobhunting_discover import listing

    calls = []

    def scrape_jobs(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{"id": "in-1", "job_url": "https://ca.indeed.com/viewjob?jk=abc", "title": "Software Engineer, New Grad",
                              "company": "Acme", "location": "Toronto, ON, CA", "date_posted": "2026-10-28",
                              "description": "JD text"}])

    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=scrape_jobs))
    monkeypatch.setattr(listing.time, "sleep", lambda seconds: None)
    result = listing.fetch_indeed(["software engineer new grad", "software engineer 2027"], days=3)
    assert [call["hours_old"] for call in calls] == [72, 72]
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["query"] == "software engineer new grad; software engineer 2027"


def test_fetch_indeed_marks_capped_query_partial(monkeypatch):
    import sys, types
    import pandas as pd
    from jobhunting_discover import listing

    rows = [{"id": f"in-{i}", "job_url": f"https://ca.indeed.com/viewjob?jk=k{i}", "title": "Developer", "company": "Acme",
             "location": "Toronto, ON, CA", "date_posted": "2026-10-28", "description": "JD"} for i in range(listing.INDEED_TRUNCATION_ROWS)]
    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=lambda **kwargs: pd.DataFrame(rows)))
    result = listing.fetch_indeed(["entry level software engineer"], days=60)
    assert result["status"] == "partial"
    assert "split this query" in result["logs"][0]["log"]


def test_fetch_indeed_title_filters_campus_queries_only(monkeypatch):
    import sys, types
    import pandas as pd
    from jobhunting_discover import listing

    def scrape_jobs(**kwargs):
        rows = [{"id": "a", "job_url": "https://ca.indeed.com/viewjob?jk=a", "title": "Registered Nurse - New Grad"},
                {"id": "b", "job_url": "https://ca.indeed.com/viewjob?jk=b", "title": "Software Developer, New Grad"},
                {"id": "c", "job_url": "https://ca.indeed.com/viewjob?jk=c", "title": "Junior QA Specialist"}]
        for row in rows:
            row.update(company="Acme", location="Toronto, ON, CA", date_posted="2026-10-28", description="JD")
        return pd.DataFrame(rows if kwargs["search_term"] == '"new grad"' else rows[:1])

    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=scrape_jobs))
    monkeypatch.setattr(listing.time, "sleep", lambda seconds: None)
    result = listing.fetch_indeed(["nurse"], days=7, title_filtered=['"new grad"'])
    titles = {row["title"]: row["query"] for row in result["jobs"]}
    assert titles == {"Software Developer, New Grad": '"new grad"', "Junior QA Specialist": '"new grad"',
                      "Registered Nurse - New Grad": "nurse"}


@pytest.mark.parametrize("title, kept", [("Technology, Application Development, Full Time Analyst", True), ("Robotics Intern", True),
                                         ("Associate LLM Engineer", True), ("Registered Nurse - New Grad", False), ("Sales Clerk (Grad)", False)])
def test_tech_title_filter(title, kept):
    from jobhunting_discover.listing import TECH_TITLE
    assert bool(TECH_TITLE.search(title)) is kept


@pytest.mark.parametrize("posted,days", [
    ("2026-11-02T12:00:00Z", 7), ("2026-11-02T12:00:00.000+0000", 7), ("2026-11-02T07:00:00-05:00", 7),
    ("2026-11-02", 7.5), (str(1793620800), 7), (str(1793620800000), 7),
    ("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 3 Days Ago", 3), ("Posted 30+ Days Ago", 31),
])
def test_posted_dates_from_every_ats_read_as_days_ago(posted, days):
    from datetime import datetime, timezone
    assert listing.posted_days_ago(posted, datetime(2026, 11, 9, 12, tzinfo=timezone.utc)) == pytest.approx(days)


@pytest.mark.parametrize("posted", ["", None, "soon"])
def test_unreadable_posted_dates_are_unknown(posted):
    from datetime import datetime, timezone
    assert listing.posted_days_ago(posted, datetime(2026, 11, 9, tzinfo=timezone.utc)) is None


def test_ashby_posting_with_a_null_address_is_still_read():
    job = {"id": "1", "jobUrl": "https://jobs.ashbyhq.com/x/1", "title": "ML Engineer", "location": "Toronto, ON",
           "isListed": True, "address": None, "secondaryLocations": [{"location": "Remote", "address": None}],
           "publishedAt": "2026-11-01", "descriptionPlain": "JD"}
    assert [row["title"] for row in parse_ats("ashby", {"jobs": [job]}, "X", "x")] == ["ML Engineer"]


def test_workday_country_facet_is_found_by_its_label():
    facets = [{"facetParameter": "CF_-_REC_-_LRV_-_Country_from_Job_Posting_Location_Extended", "descriptor": "Country",
               "values": [{"descriptor": "United States of America", "id": "us"}, {"descriptor": "Canada", "id": "ca"}]}]
    assert listing.canada_facet(facets) == ("CF_-_REC_-_LRV_-_Country_from_Job_Posting_Location_Extended", "ca")


def test_workday_board_of_a_canadian_employer_needs_no_country_facet(monkeypatch):
    posting = {"externalPath": "/job/Montreal/Data-Scientist_R1", "title": "Data Scientist", "locationsText": "Montreal",
               "bulletFields": ["R1"], "postedOn": "Posted Today"}
    calls = []
    def fake(client, method, url, **kwargs):
        calls.append(kwargs.get("json", {}).get("appliedFacets"))
        if url.endswith("/jobs"):
            return {"total": 1, "jobPostings": [posting], "facets": [{"facetParameter": "Region", "values": []}]}
        return {"jobPostingInfo": {"jobDescription": "<p>JD</p>"}}
    monkeypatch.setattr(listing, "request_json", fake)
    result = listing.fetch_ats("Desjardins", {"type": "workday", "tenant": "desjardins", "instance": "wd10",
                                              "boards": ["DesjardinsCareers"], "canada_only": True})
    assert [row["title"] for row in result["jobs"]] == ["Data Scientist"] and calls[0] == {}


def test_workday_posting_without_location_text_is_still_read():
    rows = parse_ats("workday", {"jobPostings": [{"externalPath": "/job/X/Developer_R9", "title": "Developer", "bulletFields": ["R9"]}]},
                     "iA Financial Group", "Careers", "https://ia.wd3.myworkdayjobs.com")
    assert rows[0]["location"] == "Canada (ATS facet)"


def test_lever_keeps_canadian_postings_with_their_full_jd():
    def job(n, country, location):
        return {"id": n, "text": f"Engineer {n}", "hostedUrl": f"https://jobs.lever.co/x/{n}", "country": country,
                "categories": {"location": location, "allLocations": [location]}, "createdAt": 1793620800000,
                "descriptionPlain": "About", "lists": [{"text": "Responsibilities", "content": "<li>Build</li>"}],
                "additionalPlain": "Benefits"}
    rows = parse_ats("lever", [job("1", "CA", "Montreal"), job("2", "CN", "Shanghai"), job("3", None, "Toronto, ON")], "Xsolla", "xsolla")
    assert [row["title"] for row in rows] == ["Engineer 1", "Engineer 3"]
    assert "Responsibilities" in rows[0]["description"] and "Build" in rows[0]["description"] and "Benefits" in rows[0]["description"]

import pytest

from jobhunting_core.postings import job_key


@pytest.mark.parametrize("first,second,expected", [
    ("https://www.linkedin.com/jobs/view/123/?trk=a", "https://www.linkedin.com/jobs/view/123?trk=b", True),
    ("https://www.linkedin.com/jobs/view/software-engineer-1234567890/", "https://www.linkedin.com/jobs/view/1234567890", True),
    ("https://ca.indeed.com/viewjob?jk=abc&from=a", "https://ca.indeed.com/rc/clk?jk=abc", True),
    ("https://job-boards.greenhouse.io/a/jobs/123", "https://a.example/jobs?gh_jid=123", True),
    ("https://jobs.ashbyhq.com/a/12345678-1234-1234-1234-123456789abc", "https://jobs.ashbyhq.com/b/12345678-1234-1234-1234-123456789abc", True),
    ("https://a.wd3.myworkdayjobs.com/en-US/site/job/X_JR123", "https://a.wd3.myworkdayjobs.com/en-US/other/job/Y_JR123", True),
    ("https://cibc.wd3.myworkdayjobs.com/en-US/site/job/X_2619547-1", "https://cibc.wd3.myworkdayjobs.com/en-US/site/job/Y_2619547-2", True),
    ("https://jobs.bmo.com/ca/en/job/R123456789/title", "https://jobs.bmo.com/ca/en/job/BOMOGLOBALR123456789EXTERNALENCA/title", True),
    ("https://jobs.lever.co/a/12345678-1234-1234-1234-123456789abc", "https://jobs.lever.co/a/12345678-1234-1234-1234-123456789abc?ref=x", True),
    ("https://example.com/a?x=1", "https://example.com/a?x=2", False),
    ("https://a.wd3.myworkdayjobs.com/en-US/site/job/X_JR123", "https://b.wd3.myworkdayjobs.com/en-US/site/job/X_JR123", False),
])
def test_job_key(first, second, expected):
    assert (job_key(first) == job_key(second)) is expected

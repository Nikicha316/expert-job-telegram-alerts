"""Offline fixture tests: no live site, Telegram, or browser needed."""
import json
import unittest

from sources import (_micro1_from_json, _post_id, _urls_from_sitemap_xml,
                     location_from_text, parse_mercor_index, parse_micro1_html)


class SourcesTest(unittest.TestCase):
    def test_mercor_next_data_with_nonce_and_location(self):
        fixture = {
            "props": {"pageProps": {"dehydratedState": {"queries": [
                {"state": {"data": {"listings": [
                    {"listingId": "list_AAAB123", "title": "LLM Evaluator", "status": "active",
                     "location": "Remote", "eligibleLocation": ["Bulgaria", "Romania"],
                     "rateMin": 50, "rateMax": 70, "payRateFrequency": "hour"},
                    {"listingId": "list_AAAB999", "title": "Closed gig", "status": "closed"}
                ]}}}
            ]}}}
        }
        raw = ('<html><script type="application/json" nonce="random" id="__NEXT_DATA__">'
               + json.dumps(fixture) + '</script></html>')
        jobs = parse_mercor_index(raw)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Remote")
        self.assertEqual(jobs[0].pay, "$50–$70 / hour")
        self.assertEqual(jobs[0].eligible_locations, "Bulgaria, Romania")
        self.assertIn("list_AAAB123", jobs[0].url)

    def test_micro1_card_and_location(self):
        ident = "2418674e-c1a6-4dbb-a43c-85fc8ddfdec4"
        markup = ('<div class="job-card"><h3>Video Generalist</h3>'
                  '<div>Location: Remote - Europe</div><div>$10–$20/hour</div>'
                  f'<a href="https://jobs.micro1.ai/post/{ident}">Apply</a></div>')
        jobs = parse_micro1_html(markup)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Video Generalist")
        self.assertEqual(jobs[0].location, "Remote - Europe")
        self.assertEqual(jobs[0].pay, "$10–$20/hour")
        self.assertEqual(jobs[0].job_id, ident)

    def test_micro1_json_response_and_freelance_filter(self):
        data = {"data": {"jobs": [
            {"id": "762cca82-f4d9-4b73-8546-14ef28ab5f36", "jobTitle": "AI Writer",
             "jobType": "Contractor", "location": "Remote, global", "minPay": 20, "maxPay": 40},
            {"id": "d358fe3a-3fa1-4673-b90a-99293655405d", "title": "Staff Engineer",
             "jobType": "Full-time"},
            {"id": "wrong", "title": "Not a listing"}
        ]}}
        jobs = list(_micro1_from_json(data))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Remote, global")
        self.assertEqual(jobs[0].pay, "$20–$40 / hour")

    def test_sitemap_urls(self):
        xml = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               '<url><loc>https://work.mercor.com/jobs/list_AB12/a-role</loc></url>'
               '<url><loc>https://work.mercor.com/about</loc></url></urlset>')
        jobs, subs = _urls_from_sitemap_xml(xml)
        self.assertEqual(len(jobs), 1)
        self.assertFalse(subs)

    def test_finds_location_in_multiline_detail(self):
        self.assertEqual(location_from_text("Job type: Contractor\nLocation: Remote, Europe\nSchedule: flexible"),
                         "Remote, Europe")
        self.assertEqual(location_from_text("Job details\nLocation\nUnited States only\nApply"),
                         "United States only")
        self.assertIsNone(location_from_text("Job description without location"))

    def test_ignores_other_sites_in_micro1_links(self):
        self.assertIsNone(_post_id("https://example.com/post/2418674e-c1a6-4dbb-a43c-85fc8ddfdec4"))


if __name__ == "__main__":
    unittest.main()

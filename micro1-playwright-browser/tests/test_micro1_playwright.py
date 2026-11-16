import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# During package QA, import the existing bot project next to this patch.
# After installing these files into your GitHub repo, the bot modules are in ROOT.
if not (ROOT / 'models.py').exists():
    BASE = ROOT.parent / 'micro1-mercor-telegram-bot'
    if BASE.exists():
        sys.path.insert(0, str(BASE))

from micro1_playwright import _job_id, parse_micro1_cards, read_visible_board
from sources import SourceError

UUID1 = '12345678-1234-1234-1234-123456789abc'
UUID2 = '12345678-1234-1234-1234-abcdef123456'
UUID3 = 'deadbeef-1234-1234-1234-abcdef123456'


def card(ident, name, pay='$35-$55/h', location=''):
    return (f'<a class="jobs_item" href="https://jobs.micro1.ai/post/{ident}?utm_source=test">'
            f'<h2 class="jobs_name">{name}</h2>'
            f'<div class="jobs_salary"><span class="job_salary-bold">{pay}</span></div>'
            + (f'<div class="jobs_location">{location}</div>' if location else '') + '</a>')


class Micro1ParseTests(unittest.TestCase):
    def test_card_fields_location_and_tracking_removed(self):
        html = f'<section class="jobs_list">{card(UUID1,"AI Python Specialist",location="Remote - EU")}</section>'
        jobs = parse_micro1_cards(html)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, 'AI Python Specialist')
        self.assertEqual(jobs[0].location, 'Remote - EU')
        self.assertEqual(jobs[0].pay, '$35-$55/h')
        self.assertEqual(jobs[0].url, f'https://jobs.micro1.ai/post/{UUID1}')

    def test_missing_location_is_not_guessed(self):
        html = f'<section class="jobs_list">{card(UUID1,"Language Expert")}</section>'
        self.assertIsNone(parse_micro1_cards(html)[0].location)

    def test_deduplicates_job_ids(self):
        html = '<section class="jobs_list">' + card(UUID1,'AI',pay='$40/h') + card(UUID1,'AI') + '</section>'
        self.assertEqual(len(parse_micro1_cards(html)), 1)

    def test_error_message_is_not_a_job(self):
        with self.assertRaisesRegex(SourceError, 'Unable to load jobs'):
            parse_micro1_cards('<div class="jobs_list"><p>Unable to load jobs. Please try again.</p></div>')

    def test_unexpected_empty_page_fails(self):
        with self.assertRaisesRegex(SourceError, 'zero job cards'):
            parse_micro1_cards('<div class="jobs_list"></div>')

    def test_rejects_external_links(self):
        self.assertIsNone(_job_id('https://bad.example/post/' + UUID1))
        self.assertIsNone(_job_id('http://jobs.micro1.ai/post/' + UUID1))
        self.assertIsNone(_job_id('https://jobs.micro1.ai/post/nothing'))
        self.assertIsNone(_job_id('https://jobs.micro1.ai/post/' + UUID1 + '/apply'))
        self.assertEqual(_job_id('https://req.micro1.ai/post/' + UUID1), UUID1)

    def test_skip_internal_core_team(self):
        html = f'<section class="jobs_list"><a class="jobs_item" href="https://jobs.micro1.ai/post/{UUID1}"><h2 class="jobs_name">Internal</h2><div class="jobs_tag">Core team</div></a>{card(UUID2,"AI Expert")}</section>'
        self.assertEqual(len(parse_micro1_cards(html)), 1)


class Micro1BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(
                headless=True,
                executable_path=os.environ.get('MICRO1_CHROMIUM_PATH', '/usr/bin/chromium'),
                args=['--no-sandbox'],
            )
        except Exception:
            cls.pw.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_rendered_page_with_infinite_scroll(self):
        page = self.browser.new_page(viewport={'width':1000,'height':700})
        try:
            markup = f'''<div style="height:900px"></div>
            <section class="jobs_list"><p class="jobs_result-info">3 roles found</p>
            {card(UUID1,'French Language Expert')}{card(UUID2,'Video Evaluator')}
            </section><div class="scroll-sentinel" style="height:120px"></div>
            <script>
              const sentinel = document.querySelector('.scroll-sentinel');
              new IntersectionObserver((entries, observer) => {{
                if (entries[0].isIntersecting) {{
                  document.querySelector('.jobs_list').insertAdjacentHTML('beforeend',
                    `{card(UUID3,'Robotics Expert',location='Remote')}`);
                  observer.disconnect();
                }}
              }}).observe(sentinel);
            </script>'''
            page.set_content(markup)
            jobs = read_visible_board(page, timeout_ms=8000, max_pages=3)
            self.assertEqual(len(jobs), 3)
            self.assertEqual([j.title for j in jobs], ['French Language Expert', 'Video Evaluator', 'Robotics Expert'])
        finally:
            page.close()

    def test_browser_rendered_error_page_is_detected(self):
        page = self.browser.new_page()
        try:
            page.set_content('<main><div class="jobs_list">Unable to load jobs. Please try again.</div></main>')
            with self.assertRaisesRegex(SourceError, 'Unable to load jobs'):
                read_visible_board(page, timeout_ms=1000)
        finally:
            page.close()

    def test_partial_job_pages_fail_without_state(self):
        page = self.browser.new_page()
        try:
            page.set_content(f'<main><div class="jobs_list"><p class="jobs_result-info">2 roles found</p>{card(UUID1,"AI Coach")}</div></main>')
            with self.assertRaisesRegex(SourceError,'1/2'):
                read_visible_board(page, timeout_ms=1500, max_pages=2)
        finally:
            page.close()


if __name__ == '__main__':
    unittest.main()

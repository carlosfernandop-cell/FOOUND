"""Offline publication boundary checks. No network, jobs, email or real records."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
APPROVED_SHA256 = "e942f550b8355242784b10b085409ee894ff824bfe51b1df7934a93f6cf95533"


class LandingPublicationTests(unittest.TestCase):
    def test_exact_approved_landing_and_destinations(self):
        landing = (ROOT / "docs/index.html").read_bytes()
        self.assertEqual(hashlib.sha256(landing).hexdigest(), APPROVED_SHA256)
        text = landing.decode()
        self.assertIn('id="candidate-showcase"', text)
        self.assertIn('id="reference-jonas"', text)
        self.assertIn('id="reference-priya"', text)
        self.assertIn('id="reference-tomasz"', text)
        self.assertIn('href="https://foound.lovable.app/"', text)
        self.assertNotIn('{{DISCOVERY_URL}}', text)
        self.assertIn('href="mara-at-work.html"', text)

    def test_approved_mara_example_and_safe_destinations(self):
        page = (ROOT / "docs/mara-at-work.html").read_bytes()
        self.assertEqual(hashlib.sha256(page).hexdigest(),
                         "837e0bd06d54934c76cb0db4d83e208ec102af86827b18a356ffeac6cbd6d952")
        text = page.decode()
        self.assertIn('href="./#candidate-showcase"', text)
        self.assertIn('Sample opportunities, not live openings.', text)
        self.assertIn('Example Brief for Mara Lindqvist', text)
        for forbidden in ('fetch(', 'XMLHttpRequest', 'supabase', 'localStorage',
                          'sessionStorage', '<form', 'design-review/'):
            self.assertNotIn(forbidden, text)

    def test_daily_generation_never_overwrites_landing(self):
        config = {key: '' for key in ('NOTION_TOKEN', 'NOTION_DB_ID',
                  'GMAIL_USER', 'GMAIL_APP_PASSWORD', 'RECIPIENT_EMAIL',
                  'ANTHROPIC_API_KEY')}
        with patch.dict(os.environ, config), patch.object(sys, 'argv', ['job_alerts.py']):
            import job_alerts as ja
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'docs'
            output.mkdir()
            landing = (ROOT / 'docs/index.html').read_bytes()
            (output / 'index.html').write_bytes(landing)
            agent = replace(ja.load_agent_config('001'), output_dir=str(output))
            with patch.object(ja, 'rank_with_fit', return_value=([], False)), \
                 patch.object(ja._fstate, 'record_engine_run'), \
                 patch.object(ja.requests, 'get', side_effect=AssertionError('No network')), \
                 patch.object(ja.requests, 'post', side_effect=AssertionError('No network')):
                for day in (22, 22, 23):
                    with patch.object(ja, '_et_now', return_value=datetime(2026, 9, day, 8)):
                        ja.build_shortlist(agent, [], set(), 0)
                    self.assertEqual((output / 'index.html').read_bytes(), landing)
                    self.assertTrue((output / 'current-edition.html').exists())
                    self.assertTrue((output / f'archive/2026-09-{day}.html').exists())
                    self.assertIn('href="/current-edition.html"',
                                  (output / 'current-edition.html').read_text())
                    self.assertIn('href="../current-edition.html"',
                                  (output / 'archive/index.html').read_text())
            self.assertEqual(len(list((output / 'archive').glob('2026-*.html'))), 2)


if __name__ == '__main__':
    unittest.main()

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "refresh_cpbl", Path(__file__).resolve().parents[1] / "refresh-cpbl.py")
refresh_cpbl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh_cpbl)


def page(year, month, token, rows=""):
    return f'''<form method="post" action="/player/trans">
      <input name="Year" value="{year}">
      <input name="Month" value="{month}">
      <input name="__RequestVerificationToken" value="{token}">
      <input name="ClubNo" value=""><input name="KindCode" value="">
      <input name="Keyword" value="">
    </form><table><tr><th>異動日期</th><th>球員</th><th>球隊</th>
    <th>異動原因</th></tr>{rows}</table>'''


class FakeSession:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def request(self, url, method="get", data=None, referer=None):
        self.calls.append((url, method, data, referer))
        return next(self.pages), refresh_cpbl.ZH_URL


class FormCollectionTests(unittest.TestCase):
    def test_post_contract_and_rotating_token_for_both_months(self):
        session = FakeSession([
            page(2026, 10, "first-token"),
            page(2026, 9, "second-token"),
            page(2026, 10, "third-token"),
        ])
        result = refresh_cpbl.collect_form_months(
            session, refresh_cpbl.ZH_URL, [(2026, 9), (2026, 10)])
        self.assertEqual(result, {(2026, 9): [], (2026, 10): []})
        first, second = session.calls[1], session.calls[2]
        self.assertEqual(first[:2], (refresh_cpbl.ZH_URL, "post"))
        self.assertEqual(first[2], {
            "Year": "2026", "Month": "9",
            "__RequestVerificationToken": "first-token",
            "ClubNo": "", "KindCode": "", "Keyword": "",
        })
        self.assertEqual(second[2]["Month"], "10")
        self.assertEqual(second[2]["__RequestVerificationToken"], "second-token")

    def test_blocked_month_cannot_be_treated_as_empty(self):
        session = FakeSession([page(2026, 10, "token"), "NOT FOUND"])
        with self.assertRaises(ValueError):
            refresh_cpbl.collect_form_months(
                session, refresh_cpbl.ZH_URL, [(2026, 10)])

    def test_filtered_default_cannot_replace_all_teams(self):
        session = FakeSession([page(2026, 10, "token").replace(
            'name="ClubNo" value=""', 'name="ClubNo" value="ACN"')])
        with self.assertRaises(ValueError):
            refresh_cpbl.collect_form_months(
                session, refresh_cpbl.ZH_URL, [(2026, 10)])
        self.assertEqual(len(session.calls), 1)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cpbl-cache.json"
        self.old_row = {
            "date": "2026-09-29", "player": "陳思仲", "team": "味全龍",
            "event": "降二軍", "acnt": "0000006728", "playerEn": "Chen",
        }
        self.original = json.dumps({
            "updated": "2026-09-30T01:57:18+09:00", "rows": [self.old_row],
        }, ensure_ascii=False).encode()
        self.path.write_bytes(self.original)
        self.now = datetime(2026, 10, 4, 10, tzinfo=refresh_cpbl.KST)

    def test_prolonged_outage_backfills_missed_months(self):
        previous = json.loads(self.original)
        later = datetime(2026, 11, 20, tzinfo=refresh_cpbl.KST)
        self.assertEqual(refresh_cpbl.required_months(previous, later),
                         [(2026, 9), (2026, 10), (2026, 11)])

    def test_initial_rows_without_check_timestamp_determine_backfill(self):
        previous = {"rows": [self.old_row]}
        later = datetime(2026, 10, 20, tzinfo=refresh_cpbl.KST)
        self.assertEqual(refresh_cpbl.required_months(previous, later),
                         [(2026, 9), (2026, 10)])

    def test_empty_october_is_success_and_keeps_september_and_names(self):
        new_row = dict(self.old_row)
        new_row.pop("playerEn")
        rc = refresh_cpbl.refresh(self.path, self.now, lambda months: {
            (2026, 9): [new_row], (2026, 10): [],
        })
        self.assertEqual(rc, 0)
        result = json.loads(self.path.read_text())
        self.assertEqual(result["rows"], [self.old_row])
        self.assertEqual(result["updated"], "2026-10-04T10:00:00+09:00")
        self.assertEqual(result["checkedMonths"], ["2026-09", "2026-10"])
        self.assertEqual(result["latest"], "2026-09-29")

    def test_http_failure_keeps_cache_bytes_and_timestamp(self):
        def blocked(months):
            raise refresh_cpbl.FetchError("HTTP 404")
        self.assertEqual(refresh_cpbl.refresh(self.path, self.now, blocked), 1)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_one_missing_month_cannot_write_a_partial_refresh(self):
        rc = refresh_cpbl.refresh(self.path, self.now, lambda months: {(2026, 9): []})
        self.assertEqual(rc, 1)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_valid_zero_transactions_is_a_successful_cache(self):
        rc = refresh_cpbl.refresh(self.path, self.now, lambda months: {
            (2026, 9): [], (2026, 10): [],
        })
        self.assertEqual(rc, 0)
        result = json.loads(self.path.read_text())
        self.assertEqual(result["rows"], [])
        self.assertIsNone(result["latest"])


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime

from cpbl_transactions import (
    merge_month_rows, months_to_refresh, parse_month_page, parse_rows, query_form,
)


HEADERS = "<tr><th>異動日期</th><th>球員</th><th>球隊</th><th>異動原因</th></tr>"


def form(year=2026, month=10, action="/player/trans", method="post"):
    return f'''<form action="{action}" method="{method}">
      <input type="hidden" name="__RequestVerificationToken" value="csrf-value">
      <select name="Year"><option value="2025">2025</option>
      <option value="{year}" selected>{year}</option></select>
      <select name="Month"><option value="9">9</option>
      <option value="{month}" selected>{month}</option></select>
      <select name="TeamNo"><option value="" selected>全部球隊</option></select>
      <input name="TransType" value=""><input type="submit" name="Search" value="query">
      <input type="checkbox" name="NotSelected" value="bad">
    </form>'''


def page(body="", year=2026, month=10):
    return form(year, month) + "<table>" + HEADERS + body + "</table>"


def row(day="2026-10-01", player="王柏融", team="台鋼雄鷹", event="升一軍", acnt="0001", player_en=""):
    return {"date": day, "player": player, "team": team, "event": event,
            "acnt": acnt, "playerEn": player_en}


class ParseRowsTests(unittest.TestCase):
    def test_four_column_mapping_uses_headers_not_badge_text(self):
        html = page('''<tr><td>2026/10/01</td>
            <td><a href="/team/person?Acnt=0001">王柏融</a><span>▲</span></td>
            <td>台鋼雄鷹</td><td>升一軍</td></tr>''')
        self.assertEqual(parse_rows(html, 2026), [{
            "date": "2026-10-01", "player": "王柏融", "team": "台鋼雄鷹",
            "event": "升一軍", "acnt": "0001",
        }])

    def test_rowspan_and_empty_date_continuation(self):
        html = page('''<tr><td rowspan="2">10/01</td><td>王柏融</td><td>台鋼雄鷹</td><td>升一軍</td></tr>
          <tr><td>林子偉</td><td>樂天桃猿</td><td>降二軍</td></tr>
          <tr><td></td><td>陳傑憲</td><td>統一獅</td><td>升一軍</td></tr>''')
        parsed = parse_month_page(html, 2026, 10)
        self.assertEqual([item["date"] for item in parsed], ["2026-10-01"] * 3)
        self.assertEqual([item["player"] for item in parsed], ["王柏融", "林子偉", "陳傑憲"])

    def test_lowercase_acnt_and_annotations_inside_link(self):
        html = page('''<tr><td>2026-10-02</td><td>
          <a href="/team/person?kind=A&amp;acnt=0002">＃ 林子偉 <span>◎</span></a></td>
          <td>樂天桃猿</td><td>註冊註銷</td></tr>''')
        parsed = parse_rows(html, 2026)[0]
        self.assertEqual((parsed["player"], parsed["acnt"], parsed["team"], parsed["event"]),
                         ("林子偉", "0002", "樂天桃猿", "註冊註銷"))

    def test_omitted_date_cell_inherits_only_within_its_table(self):
        html = page('''<tr><td>2026/10/01</td><td>王柏融</td><td>台鋼雄鷹</td><td>升一軍</td></tr>
          <tr><td><a href="/team/person?acnt=0002">林子偉</a></td>
          <td>樂天桃猿</td><td>降二軍</td></tr>''')
        rows = parse_month_page(html, 2026, 10)
        self.assertEqual([r["date"] for r in rows], ["2026-10-01", "2026-10-01"])
        self.assertEqual(rows[1]["player"], "林子偉")
        self.assertEqual(rows[1]["team"], "樂天桃猿")
        invalid = page('<tr><td>林子偉</td><td>樂天桃猿</td><td>降二軍</td></tr>')
        with self.assertRaises(ValueError):
            parse_month_page(invalid, 2026, 10)

    def test_unrelated_table_is_not_a_transaction_table(self):
        html = "<table><tr><td>2026/10/01</td><td>王柏融</td><td>台鋼雄鷹</td><td>升一軍</td></tr></table>"
        self.assertEqual(parse_rows(html, 2026), [])

    def test_english_headers_and_real_calendar_validation(self):
        html = '''<table><tr><th>Date</th><th>Player</th><th>Team</th><th>Reason</th></tr>
          <tr><td>2026/02/30</td><td>Wang</td><td>Hawks</td><td>Registered</td></tr>
          <tr><td>2024/02/29</td><td>Wang</td><td>Hawks</td><td>Registered</td></tr></table>'''
        self.assertEqual([item["date"] for item in parse_rows(html, 2026)], ["2024-02-29"])


class QueryFormTests(unittest.TestCase):
    def test_discovers_action_and_retains_selected_values_and_csrf(self):
        result = query_form(form(), "https://www.cpbl.com.tw/player/trans?Year=2026")
        self.assertEqual(result["url"], "https://www.cpbl.com.tw/player/trans")
        self.assertEqual(result["method"], "post")
        self.assertEqual((result["year_field"], result["month_field"]), ("Year", "Month"))
        self.assertEqual(result["data"], {"__RequestVerificationToken": "csrf-value",
                         "Year": "2026", "Month": "10", "TeamNo": "", "TransType": ""})

    def test_lowercase_fields_and_get_default(self):
        html = '<form><input name="year" value="2026"><input name="month" value="10"></form>'
        result = query_form(html, "https://www.cpbl.com.tw/player/trans")
        self.assertEqual((result["year_field"], result["month_field"], result["method"]),
                         ("year", "month", "get"))

    def test_missing_ambiguous_or_cross_origin_form_rejects(self):
        for html in ("<html>Home</html>", form() + form(),
                     form(action="https://example.com/player/trans"),
                     form(action="https://en.cpbl.com.tw/player/trans")):
            with self.subTest(html=html[:80]), self.assertRaises(ValueError):
                query_form(html, "https://www.cpbl.com.tw/player/trans")


class MonthPageTests(unittest.TestCase):
    def test_filtered_pages_cannot_replace_a_whole_month(self):
        for name in ("ClubNo", "TeamNo", "KindCode", "TransType", "Keyword"):
            html = page().replace("</form>", f'<input name="{name}" value="partial"></form>')
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_month_page(html, 2026, 10)

    def test_nonempty_all_option_uses_official_label(self):
        html = page().replace('value="" selected>全部球隊', 'value="all-code" selected>全部球隊')
        self.assertEqual(parse_month_page(html, 2026, 10), [])

    def test_header_only_or_explicit_empty_month_is_valid(self):
        self.assertEqual(parse_month_page(page(), 2026, 10), [])
        self.assertEqual(parse_month_page(page('<tr><td colspan="4">查無資料</td></tr>'), 2026, 10), [])
        self.assertEqual(parse_month_page(form() + "<div>查無資料</div>", 2026, 10), [])

    def test_block_and_home_pages_are_not_empty_months(self):
        for html in ("NOT FOUND", "<html>Anti-DDoS Flood Protection</html>",
                     "<html>Home</html>", form() + "<h1>CPBL Home</h1>",
                     form() + "<h1>Access denied</h1>"):
            with self.subTest(html=html[:80]), self.assertRaises(ValueError):
                parse_month_page(html, 2026, 10)

    def test_scope_mismatch_rejects_even_empty_pages(self):
        with self.assertRaises(ValueError):
            parse_month_page(page(month=9), 2026, 10)
        with self.assertRaises(ValueError):
            parse_month_page(page('''<tr><td>2026/09/30</td><td>王柏融</td>
                <td>台鋼雄鷹</td><td>升一軍</td></tr>'''), 2026, 10)

    def test_english_form_uses_its_actual_official_origin(self):
        html = page().replace('action="/player/trans"',
                              'action="https://en.cpbl.com.tw/player/trans"')
        self.assertEqual(parse_month_page(
            html, 2026, 10, page_url="https://en.cpbl.com.tw/player/trans"), [])

    def test_malformed_rows_do_not_become_successful_empty_pages(self):
        for body in ('<tr><td colspan="4">Unexpected server error</td></tr>',
                     '<tr><td>2026/10/32</td><td>王柏融</td><td>台鋼雄鷹</td><td>升一軍</td></tr>',
                     '<tr><td></td><td>王柏融</td><td>台鋼雄鷹</td><td>升一軍</td></tr>',
                     '<tr><td>2026/10/01</td><td>王柏融</td><td>台鋼雄鷹</td><td></td></tr>'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_month_page(page(body), 2026, 10)


class CacheTests(unittest.TestCase):
    def test_trailing_window_handles_month_and_year_transitions(self):
        self.assertEqual(months_to_refresh(datetime(2026, 10, 4)), [(2026, 9), (2026, 10)])
        self.assertEqual(months_to_refresh(datetime(2027, 1, 3)), [(2026, 12), (2027, 1)])
        self.assertEqual(months_to_refresh(datetime(2026, 10, 20)), [(2026, 10)])
        self.assertEqual(months_to_refresh(datetime(2026, 10, 1), days=0), [(2026, 10)])

    def test_replaces_only_queried_months_deduplicates_and_keeps_english(self):
        older = row("2026-08-28", player_en="Wang Po-Jung")
        september = row("2026-09-30", event="降二軍")
        october = row(player_en="Wang Po-Jung")
        updated = row()
        updated.pop("playerEn")
        new = row("2026-10-02", event="降二軍")
        merged = merge_month_rows([older, september, october], {(2026, 10): [updated, updated, new]})
        self.assertEqual([item["date"] for item in merged],
                         ["2026-10-02", "2026-10-01", "2026-09-30", "2026-08-28"])
        self.assertEqual(merged[1]["playerEn"], "Wang Po-Jung")
        self.assertEqual(merged[0]["playerEn"], "Wang Po-Jung")
        self.assertEqual(merged[-1], older)

    def test_empty_month_removes_only_its_previous_rows(self):
        old = row("2026-09-30")
        self.assertEqual(merge_month_rows([old, row()], {(2026, 10): []}), [old])

    def test_wrong_scope_or_invalid_calendar_date_rejects_merge(self):
        for replacement in (row("2026-09-30"), row("2026-10-32")):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                merge_month_rows([], {(2026, 10): [replacement]})


if __name__ == "__main__":
    unittest.main()

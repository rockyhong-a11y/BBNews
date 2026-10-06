"""Profile remarks are explicit, partial history, never inferred roster moves.

PROFILE is a reduced copy of the actual /v1/players/0000001719 response
captured on 2026-10-06, including its PascalCase wire keys and exact remarks.
"""
import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cpbl_profiles import collect_profiles, merge_profile_rows, parse_profile


ACNT = "0000001719"
TODAY = date(2026, 10, 6)
PROFILE = {
    "Data": {"Player": {"Basic": {
        "Acnt": ACNT,
        "CHName": "胡智爲",
        "Engname": "HU Chih Wei",
        "RetiredDate": None,
        "PlayerStatus": "",
        "Team": {"Code": "ADD022", "Name": "統一7-ELEVEn獅二軍"},
        "Rmk": (
            "2021/03/14~07/10 味全龍《自行培訓球員》\n"
            "2021/08/20 統一7-ELEVEn獅《合約所屬球員》\n"
            "2021/08/25 統一7-ELEVEn獅《新註冊》\n"
            "2023/03/01 統一7-ELEVEn獅《註冊球員》\n"
            "2023 統一7-ELEVEn《季後賽28人名單》\n"
            "2024/02/26 統一7-ELEVEn獅《合約所屬》\n"
            "2024/08/02 統一7-ELEVEn獅《新註冊》\n"
            "2025/02/26 統一7-ELEVEn獅《註冊球員》\n"
            "2025 統一7-ELEVEn獅《季後賽32人名單》\n"
            "2026/02/24 統一7-ELEVEn 獅 《註冊球員》"
        ),
    }}}
}


def profile(remark, acnt=ACNT, **fields):
    payload = copy.deepcopy(PROFILE)
    payload["Data"]["Player"]["Basic"].update(Rmk=remark, Acnt=acnt, **fields)
    return payload


def movement(day="2026-09-29", event="降二軍", player_en=""):
    return {"date": day, "player": "胡智爲", "playerEn": player_en,
            "acnt": ACNT, "team": "統一7-ELEVEn獅", "event": event}


class ProfileParsingTests(unittest.TestCase):
    def test_real_pascalcase_profile_excludes_period_and_year_only_rosters(self):
        rows = parse_profile(PROFILE, ACNT, TODAY)
        self.assertEqual([row["date"] for row in rows], [
            "2021-08-20", "2021-08-25", "2023-03-01", "2024-02-26",
            "2024-08-02", "2025-02-26", "2026-02-24",
        ])
        self.assertEqual(rows[-1], {
            "date": "2026-02-24", "player": "胡智爲", "playerEn": "HU Chih Wei",
            "acnt": ACNT, "team": "統一7-ELEVEn獅", "event": "註冊球員",
            "source": "https://stats.cpbl.com.tw/players/" + ACNT,
            "sourceText": "2026/02/24 統一7-ELEVEn 獅 《註冊球員》",
            "sourceKind": "profile-remark",
        })

    def test_history_uses_explicit_team_and_event_not_current_roster_or_retired_date(self):
        raw = "2026/10/01 味全龍《註冊註銷》"
        payload = profile(raw, RetiredDate="2026-10-05T00:00:00", PlayerStatus="＃")
        rows = parse_profile(payload, ACNT, TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["date"], rows[0]["team"], rows[0]["event"]),
                         ("2026-10-01", "味全龍", "註冊註銷"))
        self.assertEqual(rows[0]["sourceText"], raw)

    def test_no_remark_never_infers_registration_or_release(self):
        for remark in (None, "", "球員曾效力海外球隊"):
            with self.subTest(remark=remark):
                self.assertEqual(parse_profile(profile(
                    remark, RetiredDate="2026-10-05T00:00:00", PlayerStatus="＃"), ACNT, TODAY), [])

    def test_ambiguous_dates_and_ranges_are_not_filled_in(self):
        remark = (
            "2026/09/10~10/01 味全龍《自行培訓球員》\n"
            "2026/09/10～10/01 味全龍《自行培訓球員》\n"
            "2026/09/10 ~ 10/01 味全龍《自行培訓球員》\n"
            "2026/09/10 至 10/01 味全龍《自行培訓球員》\n"
            "2026 統一7-ELEVEn獅《季後賽32人名單》\n"
            "10/01 統一7-ELEVEn獅《註冊球員》\n"
            "2026/10/01 統一7-ELEVEn獅 註冊球員"
        )
        self.assertEqual(parse_profile(profile(remark), ACNT, TODAY), [])

    def test_future_date_excluded_and_exact_today_kept(self):
        remark = "2026/10/06 味全龍《新註冊》\n2026/10/07 味全龍《註冊註銷》"
        self.assertEqual([row["date"] for row in parse_profile(profile(remark), ACNT, TODAY)],
                         ["2026-10-06"])

    def test_invalid_calendar_date_fails_instead_of_accepting_impossible_event(self):
        with self.assertRaises(ValueError):
            parse_profile(profile("2026/02/30 味全龍《新註冊》"), ACNT, TODAY)

    def test_missing_basic_wrong_identity_and_nontext_remark_reject(self):
        payloads = ({"Data": {}}, profile("", acnt="0000000000"),
                    profile("", CHName=""), profile(["2026/10/01 味全龍《新註冊》"]))
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_profile(payload, ACNT, TODAY)

    def test_profile_name_and_basic_types_are_validated(self):
        malformed = [profile("2026/10/01 味全龍《新註冊》", CHName=123),
                     profile("2026/10/01 味全龍《新註冊》", Engname=["HU"]),
                     {"Data": {"Player": {"Basic": None}}}]
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_profile(payload, ACNT, TODAY)


class ProfileCollectionTests(unittest.TestCase):
    def test_every_indexed_profile_is_requested_using_discovered_official_path(self):
        second = "0000006888"
        calls = []
        def getter(path):
            calls.append(path)
            if path == "/v1/players/autocomplete":
                return {"Data": {"Players": [{"Acnt": ACNT}, {"Acnt": second}]}}
            if path == "/v1/players/" + ACNT:
                return PROFILE
            if path == "/v1/players/" + second:
                return profile(None, acnt=second)
            self.fail("Unexpected guessed endpoint: " + path)
        result = collect_profiles(TODAY, getter=getter)
        self.assertEqual(result["profilesChecked"], 2)
        self.assertEqual(len(result["rows"]), 7)
        self.assertCountEqual(calls, ["/v1/players/autocomplete",
                                     "/v1/players/" + ACNT, "/v1/players/" + second])

    def test_one_failed_profile_does_not_return_partial_success(self):
        second = "0000006888"
        def getter(path):
            if path == "/v1/players/autocomplete":
                return {"Data": {"Players": [{"Acnt": ACNT}, {"Acnt": second}]}}
            if path == "/v1/players/" + ACNT:
                return PROFILE
            raise RuntimeError("HTTP 503")
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            collect_profiles(TODAY, getter=getter)

    def test_missing_empty_duplicate_or_invalid_profile_index_rejects(self):
        payloads = ({"Data": {}}, {"Data": {"Players": []}},
                    {"Data": {"Players": [{"Acnt": ACNT}, {"Acnt": ACNT}]}},
                    {"Data": {"Players": [{"Acnt": "1719"}]}})
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                collect_profiles(TODAY, getter=lambda path, payload=payload: payload)


class ProfileMergeTests(unittest.TestCase):
    def test_add_only_keeps_old_movement_and_month_rows(self):
        old = [movement(), movement("2026-08-28", "升一軍")]
        original = copy.deepcopy(old)
        additions = parse_profile(profile("2026/10/01 味全龍《註冊註銷》"), ACNT, TODAY)
        merged = merge_profile_rows(old, additions)
        self.assertEqual([row["date"] for row in merged],
                         ["2026-10-01", "2026-09-29", "2026-08-28"])
        self.assertEqual(merged[1:], old)
        self.assertEqual(old, original)

    def test_duplicate_remark_only_fills_missing_english_without_replacing_provenance(self):
        old = movement("2026-02-24", "註冊球員")
        old["team"] = "統一7-ELEVEn 獅"
        old["sourceKind"] = "movement-table"
        addition = parse_profile(PROFILE, ACNT, TODAY)[-1]
        merged = merge_profile_rows([old], [addition, addition])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["playerEn"], "HU Chih Wei")
        self.assertEqual(merged[0]["team"], old["team"])
        self.assertEqual(merged[0]["sourceKind"], "movement-table")
        self.assertNotIn("sourceText", merged[0])
        self.assertEqual(old["playerEn"], "")

    def test_zero_dated_profile_remarks_keep_existing_daily_movements(self):
        old = [movement()]
        self.assertEqual(merge_profile_rows(old, []), old)


try:
    spec = importlib.util.spec_from_file_location(
        "refresh_cpbl_profiles_test", Path(__file__).resolve().parents[1] / "refresh-cpbl.py")
    refresh_cpbl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(refresh_cpbl)
except ModuleNotFoundError as exc:
    if exc.name != "bs4":
        raise
    refresh_cpbl = None


@unittest.skipIf(refresh_cpbl is None, "Legacy movement import requires bs4; pure profile tests run without it")
class ProfileRefreshTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "cpbl-cache.json"
        self.now = datetime(2026, 10, 6, 15, 0, tzinfo=timezone(timedelta(hours=9)))
        self.previous = {"updated": "2026-09-30T01:57:18+09:00", "rows": [movement()],
                         "checkedMonths": ["2026-09"], "latest": "2026-09-29"}
        self.path.write_text(json.dumps(self.previous, ensure_ascii=False), encoding="utf-8")
        self.original = self.path.read_bytes()

    def test_success_keeps_month_movement_confirmation_time_and_adds_profile_coverage(self):
        additions = parse_profile(profile("2026/10/01 味全龍《註冊註銷》"), ACNT, TODAY)
        result = refresh_cpbl.refresh_profiles(self.path, self.now, collector=lambda today: {
            "rows": additions, "profilesChecked": 517,
        })
        self.assertEqual(result, 0)
        cache = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(cache["updated"], self.previous["updated"])
        self.assertEqual(cache["movementsUpdated"], self.previous["updated"])
        self.assertEqual(cache["checkedMonths"], self.previous["checkedMonths"])
        self.assertEqual(cache["profilesUpdated"], self.now.isoformat(timespec="seconds"))
        self.assertEqual(cache["profilesChecked"], 517)
        self.assertEqual(cache["coverage"], "profile-remarks")
        self.assertEqual(cache["latest"], "2026-10-01")
        self.assertEqual(cache["rows"][1], self.previous["rows"][0])

    def test_existing_explicit_movement_time_is_not_overwritten(self):
        self.previous["movementsUpdated"] = "2026-09-29T10:00:00+09:00"
        self.path.write_text(json.dumps(self.previous), encoding="utf-8")
        self.assertEqual(refresh_cpbl.refresh_profiles(self.path, self.now, collector=lambda today: {
            "rows": [], "profilesChecked": 517,
        }), 0)
        cache = json.loads(self.path.read_text())
        self.assertEqual(cache["movementsUpdated"], self.previous["movementsUpdated"])
        self.assertEqual(cache["rows"], self.previous["rows"])

    def test_partial_profile_failure_keeps_cache_bytes_and_all_timestamps(self):
        second = "0000006888"
        def getter(path):
            if path == "/v1/players/autocomplete":
                return {"Data": {"Players": [{"Acnt": ACNT}, {"Acnt": second}]}}
            if path == "/v1/players/" + ACNT:
                return PROFILE
            raise RuntimeError("HTTP 503")
        result = refresh_cpbl.refresh_profiles(self.path, self.now, collector=lambda today:
                                               collect_profiles(today, getter=getter))
        self.assertEqual(result, 1)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.path.parent.glob(".cpbl-cache.json.*")), [])

    def test_invalid_dated_remark_keeps_cache_bytes(self):
        def collector(today):
            return {"rows": parse_profile(profile("2026/02/30 味全龍《新註冊》"), ACNT, today),
                    "profilesChecked": 1}
        self.assertEqual(refresh_cpbl.refresh_profiles(self.path, self.now, collector=collector), 1)
        self.assertEqual(self.path.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()

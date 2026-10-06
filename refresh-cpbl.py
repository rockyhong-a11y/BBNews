#!/usr/bin/env python3
"""새 공식 CPBL 프로필의 날짜가 명시된 등록·계약 이력을 보강한다.

프로필에는 일별 1·2군 승강 전체 피드가 없으므로 기존 이동 캐시와
월별 이동 확인 시각을 보존한다. 이전 폼 수집 함수는 회귀 검사에 유지.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cpbl_transactions import (
    merge_month_rows, months_to_refresh, parse_month_page, parse_rows, query_form,
)
from cpbl_profiles import SOURCE, collect_profiles, merge_profile_rows

ZH_URL = "https://www.cpbl.com.tw/player/trans"
EN_URL = "https://en.cpbl.com.tw/player/trans"
KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class FetchError(RuntimeError):
    pass


class CurlSession:
    """GET과 폼 POST에서 CDN·CSRF 쿠키 전체를 공유한다."""

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cookie_jar = str(Path(self.directory.name) / "cookies.txt")
        return self

    def __exit__(self, *args):
        self.directory.cleanup()

    def request(self, url, method="get", data=None, referer=None):
        body_path = Path(self.directory.name) / "response.html"
        cmd = [
            "curl", "-sS", "-L", "--fail-with-body", "--max-redirs", "5",
            "--max-time", "30", "--connect-timeout", "10",
            "-c", self.cookie_jar, "-b", self.cookie_jar,
            "-A", UA,
            "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H", "Accept-Language: zh-TW,zh;q=0.9,en;q=0.8",
            "-o", str(body_path), "-w", "%{http_code}\n%{url_effective}",
        ]
        if referer:
            cmd += ["-e", referer]
        if method.lower() == "post":
            cmd += ["-H", "Content-Type: application/x-www-form-urlencoded",
                    "--data", urlencode(data or {})]
        elif method.lower() == "get":
            if data is not None:
                parts = urlsplit(url)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                query.update(data)
                url = urlunsplit(parts._replace(query=urlencode(query)))
        else:
            raise FetchError("지원하지 않는 공식 폼 요청 방식")
        try:
            res = subprocess.run(cmd + [url], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=40)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchError(f"공식 CPBL 요청 실패: {url}: {exc}") from exc
        status, _, final_url = res.stdout.strip().partition("\n")
        print(f"   {method.upper()} {url}: HTTP {status or '?'} (curl {res.returncode})")
        if res.returncode or status != "200":
            raise FetchError(f"공식 원본 접근 실패: HTTP {status or '?'} — "
                             f"{res.stderr.strip() or url}")
        return body_path.read_text(encoding="utf-8", errors="replace"), final_url


def collect_form_months(session, page_url, months):
    """현재 공식 HTML의 action/method/필드명/토큰을 사용한다."""
    html, response_url = session.request(page_url)
    form = query_form(html, response_url)
    if not form["unfiltered"]:
        raise ValueError("공식 기본 폼이 전체 구단·이동 범위를 제공하지 않음")
    results = {}
    for year, month in months:
        payload = dict(form["data"])
        payload[form["year_field"]] = str(year)
        payload[form["month_field"]] = str(month)
        # 월 외의 구단·이벤트 값과 토큰은 발견한 공식 폼 그대로 유지한다.
        html, response_url = session.request(
            form["url"], method=form["method"], data=payload, referer=response_url)
        rows = parse_month_page(html, year, month, page_url=response_url)
        results[(year, month)] = rows
        print(f"   {year}-{month:02d}: 공식 조회 확인 · {len(rows)}건"
              + (" (새 기록 없음)" if not rows else ""))
        # 응답에서 갱신된 CSRF 토큰을 다음 조회에 사용한다.
        form = query_form(html, response_url)
        if not form["unfiltered"]:
            raise ValueError("공식 응답 폼의 조회 범위가 일부로 변경됨")
    return results


def collect_months(months):
    with CurlSession() as session:
        zh = collect_form_months(session, ZH_URL, months)
        # EN은 부가 정보이며 실패해도 검증된 ZH 데이터를 유지한다.
        try:
            en = collect_form_months(session, EN_URL, months)
        except (FetchError, ValueError) as exc:
            print(f"   EN 영문명 보강 건너뜀: {exc}")
            en = {}
    en_by_acnt = {
        row["acnt"]: row["player"]
        for rows in en.values() for row in rows if row.get("acnt")
    }
    for rows in zh.values():
        for row in rows:
            row["playerEn"] = en_by_acnt.get(row.get("acnt"), "")
    return zh


def required_months(previous, now):
    """최근 7일과 마지막 정상 확인 이후 놓친 월을 함께 복구한다."""
    checked = previous.get("updated")
    if not checked and previous.get("rows"):
        checked = max(row["date"] for row in previous["rows"])
    days = 7
    if checked:
        verified = datetime.fromisoformat(checked)
        verified = (verified.replace(tzinfo=KST) if verified.tzinfo is None
                    else verified.astimezone(KST))
        days = max(days, (now.date() - verified.date()).days)
    return months_to_refresh(now, days=days)


def refresh(cache_path="cpbl-cache.json", now=None, collector=None):
    now = now or datetime.now(KST)
    now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    path = Path(cache_path)
    try:
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"rows": []}
        if not isinstance(previous.get("rows"), list):
            raise ValueError("기존 캐시 rows 형식 오류")
        months = required_months(previous, now)
        month_rows = (collector or collect_months)(months)
        if set(month_rows) != set(months):
            raise ValueError("필요한 조회 월이 모두 검증되지 않음")
        rows = merge_month_rows(previous["rows"], month_rows)
        latest = max((row["date"] for row in rows), default=None)
        payload = {
            "updated": now.isoformat(timespec="seconds"),
            "rows": rows,
            "latest": latest,
            "checkedMonths": [f"{year}-{month:02d}" for year, month in months],
        }
        # 모든 월이 성공한 뒤 한 번만 교체한다. 부분 조회로 캐시를 덮지 않는다.
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=path.parent,
                    prefix=f".{path.name}.", delete=False) as output:
                tmp = output.name
                json.dump(payload, output, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
    except (FetchError, ValueError, OSError) as exc:
        print(f"❌ CPBL 갱신 실패 — 기존 캐시·확인 시각 유지: {exc}")
        return 1

    print(f"✅ CPBL 공식 조회 완료: {len(rows)}건 · 최신 기록 {latest or '없음'}")
    if rows == previous["rows"]:
        print("   새 기록 없음 · 공식 확인 시각만 갱신")
    return 0


def refresh_profiles(cache_path='cpbl-cache.json', now=None, collector=None):
    """검증된 프로필 이력 보강. 월별 승강 조회 시각은 갱신하지 않는다."""
    now = now or datetime.now(KST)
    now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    path = Path(cache_path)
    tmp = None
    try:
        previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'rows': []}
        if not isinstance(previous.get('rows'), list):
            raise ValueError('기존 캐시 rows 형식 오류')
        result = (collector or collect_profiles)(now.date())
        rows = merge_profile_rows(previous['rows'], result['rows'])
        movement_latest = max((row['date'] for row in previous['rows'] if row.get('sourceKind') != 'profile-remark'), default=None)
        profile_latest = max((row['date'] for row in result['rows']), default=None)
        payload = dict(previous, rows=rows, source=SOURCE,
                       profilesUpdated=now.isoformat(timespec='seconds'),
                       profilesChecked=result['profilesChecked'], coverage='profile-remarks',
                       profileLatest=profile_latest, movementLatest=movement_latest,
                       latest=max((row['date'] for row in rows), default=None),
                       movementsUpdated=previous.get('movementsUpdated', previous.get('updated')),
                       coverageNote=f'공식 프로필의 등록·계약 이력입니다. 일별 1·2군 등록·말소 전체 공시는 제공되지 않아 기존 이동 기록(최신 {movement_latest or "없음"})을 함께 표시합니다.')
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}.', delete=False) as output:
            tmp = output.name
            json.dump(payload, output, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)
        print(f'공식 프로필 {result["profilesChecked"]}명 확인 · 보강 이력 {len(result["rows"])}건 · 전체 {len(rows)}건')
        print(f'최신 기록 {payload["latest"]} · 일별 이동 확인 {payload["movementsUpdated"]}')
        return 0
    except Exception as exc:
        print(f'CPBL 프로필 수집 실패 — 기존 캐시·확인 시각 유지: {exc}', file=sys.stderr)
        return 1
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


if __name__ == "__main__":
    sys.exit(refresh_profiles())

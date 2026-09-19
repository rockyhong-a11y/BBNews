#!/usr/bin/env python3
"""CPBL 이적·등록·말소 데이터 수집 → cpbl-cache.json

cpbl.com.tw는 CORS + Cloudflare WAF로 브라우저에서 직접 접근이 불가능하다.
또한 Python urllib/requests는 WAF에 차단되지만 curl은 통과한다.
(allofbaseball/scripts/fetch_cpbl.py 에서 검증된 방식)

따라서 GitHub Actions 러너에서 curl로 수집해 JSON 캐시로 커밋한다.

출력:
{
  "updated": "2026-09-19T12:00:00+09:00",
  "rows": [
    {"date":"2026-09-18","player":"王柏融","playerEn":"Wang Po-Jung",
     "team":"中信兄弟","event":"登錄","acnt":"0121"}
  ]
}
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta

ZH_URL = "https://cpbl.com.tw/player/trans"
EN_URL = "https://en.cpbl.com.tw/player/trans"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

KST = timezone(timedelta(hours=9))


# ── curl 헬퍼 (Cloudflare WAF 통과) ──────────────────────────
def curl_get(url):
    """curl로 GET → (html, __RequestVerificationToken 쿠키값)"""
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tf:
        tmp = tf.name
    try:
        res = subprocess.run(
            ["curl", "-s", "-L", "-D", "-", "-o", tmp,
             "-H", f"User-Agent: {UA}",
             "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "-H", "Accept-Language: zh-TW,zh;q=0.9,en;q=0.8",
             "--max-time", "30", "--connect-timeout", "15", url],
            capture_output=True, text=True, timeout=45,
        )
        cookie = ""
        for line in res.stdout.splitlines():
            if "set-cookie" in line.lower():
                m = re.search(r"__RequestVerificationToken=([^;,\s]+)", line)
                if m:
                    cookie = m.group(1)
        with open(tmp, encoding="utf-8", errors="replace") as f:
            return f.read(), cookie
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def curl_post(api_url, body_dict, token, cookie, referer, origin):
    """CSRF 인증 POST → 응답 본문(text)"""
    body = "&".join(f"{k}={v}" for k, v in body_dict.items())
    res = subprocess.run(
        ["curl", "-s", "-X", "POST",
         "-H", "Content-Type: application/x-www-form-urlencoded",
         "-H", "X-Requested-With: XMLHttpRequest",
         "-H", f"RequestVerificationToken: {token}",
         "-H", f"Cookie: __RequestVerificationToken={cookie}",
         "-H", f"Origin: {origin}",
         "-H", f"Referer: {referer}",
         "-H", f"User-Agent: {UA}",
         "-H", "Accept: application/json, text/html, */*",
         "--max-time", "30", "--data", body, api_url],
        capture_output=True, text=True, timeout=45,
    )
    return res.stdout


# ── 날짜 파싱 ────────────────────────────────────────────────
DATE_RE_FULL = re.compile(r"(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})")
DATE_RE_MD = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})\b")


def parse_date(text, default_year):
    if not text:
        return None
    m = DATE_RE_FULL.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = DATE_RE_MD.search(text)
        if not m:
            return None
        y, mo, d = default_year, int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


# ── 표 파싱 (rowspan 격자 전개) ──────────────────────────────
def parse_rows(html, default_year):
    """날짜 셀이 rowspan으로 여러 행에 걸쳐 있어 격자로 펼쳐 승계시킨다."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out = []

    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if len(trs) < 2:
            continue

        grid, occupied = {}, {}
        for ri, tr in enumerate(trs):
            ci = 0
            for cell in tr.find_all(["td", "th"]):
                while occupied.get((ri, ci)):
                    ci += 1
                try:
                    rs, cs = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
                except ValueError:
                    rs = cs = 1
                for dr in range(max(rs, 1)):
                    for dc in range(max(cs, 1)):
                        occupied[(ri + dr, ci + dc)] = True
                        grid[(ri + dr, ci + dc)] = cell
                ci += max(cs, 1)

        width = max((c for (_, c) in grid), default=-1) + 1
        if width < 3:
            continue

        for ri, tr in enumerate(trs):
            cells = [grid.get((ri, c)) for c in range(width)]
            texts = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) if c else ""
                     for c in cells]
            if not any(texts):
                continue
            if tr.find("th") and not tr.find("td"):
                continue

            date, date_idx = None, -1
            for i, t in enumerate(texts):
                d = parse_date(t, default_year)
                if d:
                    date, date_idx = d, i
                    break
            if not date:
                continue

            rest = [t for i, t in enumerate(texts) if i != date_idx and t]
            if len(rest) < 2:
                continue

            acnt, player_link = None, None
            for c in cells:
                if not c:
                    continue
                a = c.find("a", href=re.compile(r"Acnt=\d+"))
                if a:
                    mm = re.search(r"Acnt=(\d+)", a["href"])
                    acnt = mm.group(1) if mm else None
                    player_link = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
                    break

            if player_link:
                leftover = [t for t in rest if t != player_link]
                player = player_link
                team = leftover[0] if leftover else ""
                event = leftover[1] if len(leftover) > 1 else (leftover[-1] if leftover else "")
            else:
                player = rest[0]
                team = rest[1] if len(rest) > 1 else ""
                event = rest[2] if len(rest) > 2 else rest[-1]

            out.append({"date": date, "player": player, "team": team,
                        "event": event, "acnt": acnt})
    return out


# ── AJAX 폴백: 페이지 JS에서 엔드포인트를 찾아 POST ──────────
def try_ajax(html, cookie, page_url, origin, default_year):
    tokens = re.findall(r"RequestVerificationToken\s*:\s*['\"]([^'\"]{30,})['\"]", html)
    if not tokens or not cookie:
        print(f"   AJAX 폴백 불가 (토큰 {len(tokens)}개, 쿠키 {bool(cookie)})")
        return []

    candidates = set(re.findall(r"url\s*:\s*['\"](/[A-Za-z0-9_/\-]*(?:get|Get)[A-Za-z0-9_]*)['\"]", html))
    candidates |= set(re.findall(r"['\"](/player/[A-Za-z0-9_]*(?:get|Get)[A-Za-z0-9_]*)['\"]", html))
    print(f"   발견된 AJAX 후보: {sorted(candidates) or '(없음)'}")

    year = datetime.now(KST).year
    for path in sorted(candidates):
        api = origin.rstrip("/") + path
        for body in ({"kindCode": "A", "year": year, "teamNo": ""},
                     {"kindCode": "A"}, {}):
            try:
                txt = curl_post(api, body, tokens[0], cookie, page_url, origin)
            except Exception as e:
                print(f"   {path} 실패: {e}")
                continue
            if not txt or len(txt) < 50:
                continue
            payload = txt
            try:
                j = json.loads(txt)
                for k in ("Data", "TransDatas", "Rows", "Html"):
                    v = j.get(k) if isinstance(j, dict) else None
                    if isinstance(v, str) and len(v) > 50:
                        payload = v
                        break
            except Exception:
                pass
            rows = parse_rows(payload, default_year)
            if rows:
                print(f"   ✅ AJAX 성공: {path} ({len(rows)}건)")
                return rows
    return []


def dump_diagnostics(label, html):
    print(f"::group::[{label}] 진단 (파싱 0건)")
    print(f"HTML 길이: {len(html)}")
    low = html.lower()
    for marker in ("just a moment", "cf-browser-verification", "attention required",
                   "cloudflare", "access denied"):
        if marker in low:
            print(f"⚠️  차단 페이지 징후: '{marker}'")
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        print(f"table 개수: {len(tables)}")
        for i, t in enumerate(tables[:5]):
            print(f"-- table[{i}] class={t.get('class')} rows={len(t.find_all('tr'))}")
            for tr in t.find_all("tr")[:3]:
                print("   ", [re.sub(r"\s+", " ", c.get_text(" ", strip=True))[:40]
                              for c in tr.find_all(["td", "th"])])
        if not tables:
            print("--- HTML 앞부분 1500자 ---")
            print(html[:1500])
    except Exception as e:
        print(f"진단 실패: {e}")
    print("::endgroup::")


def collect(url, origin, label, year, optional=False):
    """optional=True이면 실패해도 진단 덤프 없이 조용히 넘어간다.
    EN 사이트는 Anti-DDoS로 자주 막히지만 영문명은 부가 정보라 필수가 아니다."""
    html, cookie = curl_get(url)
    print(f"[{label}] HTML {len(html)} bytes, 쿠키 {'있음' if cookie else '없음'}")
    if not html:
        return []
    rows = parse_rows(html, year)
    print(f"[{label}] 표 파싱 {len(rows)}건")
    if not rows:
        if optional:
            blocked = any(m in html.lower() for m in
                          ("anti-ddos", "flood protection", "just a moment", "cloudflare"))
            print(f"[{label}] 수집 불가{' (차단 페이지)' if blocked else ''} — 부가 정보라 건너뜀")
            return []
        dump_diagnostics(label, html)
        rows = try_ajax(html, cookie, url, origin, year)
        print(f"[{label}] AJAX 폴백 {len(rows)}건")
    return rows


def main():
    year = datetime.now(KST).year

    # ZH가 원본(필수), EN은 선수 영문명 보강용(선택)
    zh_rows = collect(ZH_URL, "https://cpbl.com.tw", "ZH", year)
    en_rows = collect(EN_URL, "https://en.cpbl.com.tw", "EN", year, optional=True)

    base = zh_rows or en_rows
    if not base:
        print("❌ 수집 실패 — 기존 캐시 유지")
        return 1

    en_by_acnt = {r["acnt"]: r for r in en_rows if r.get("acnt")}
    rows = []
    for i, r in enumerate(base):
        en = en_by_acnt.get(r.get("acnt")) if r.get("acnt") else None
        if en is None and i < len(en_rows) and en_rows[i]["date"] == r["date"]:
            en = en_rows[i]
        player_en = (en or {}).get("player", "")
        if player_en == r["player"]:
            player_en = ""
        rows.append({"date": r["date"], "player": r["player"], "playerEn": player_en,
                     "team": r["team"], "event": r["event"], "acnt": r["acnt"]})

    rows.sort(key=lambda x: x["date"], reverse=True)

    with open("cpbl-cache.json", "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(KST).isoformat(timespec="seconds"),
                   "rows": rows}, f, ensure_ascii=False, indent=2)

    print(f"\n✅ cpbl-cache.json 저장: {len(rows)}건 (최신 {rows[0]['date']})")
    for r in rows[:8]:
        print(f"   {r['date']} | {r['player']} | {r['team']} | {r['event']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

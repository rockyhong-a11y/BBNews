"""Public stats.cpbl.com.tw profile contracts; dated remarks are partial coverage.

Do not infer transactions from current team, RetiredDate, or roster differences.
The source publishes registration/contract remarks, not a daily movement feed.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.request import Request, urlopen

SOURCE = 'https://stats.cpbl.com.tw'


def get_json(path):
    for attempt in range(3):
        try:
            with urlopen(Request(SOURCE + '/api/proxy' + path,
                                 headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}), timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(f'HTTP {response.status}')
                return json.load(response)
        except Exception as exc:
            if attempt == 2:
                raise RuntimeError(f'공식 프로필 요청 실패: {path}: {exc}') from exc
            time.sleep(attempt + 1)


def parse_profile(payload, acnt, today):
    try:
        basic = payload['Data']['Player']['Basic']
    except (KeyError, TypeError) as exc:
        raise ValueError('공식 선수 프로필 Basic 누락') from exc
    if not isinstance(basic, dict) or basic.get('Acnt') != acnt or not isinstance(basic.get('CHName'), str) or not basic['CHName'].strip():
        raise ValueError('공식 선수 프로필 식별자 불일치')
    if basic.get('Engname') is not None and not isinstance(basic['Engname'], str):
        raise ValueError('공식 선수 영문명 형식 오류')
    remark = basic.get('Rmk')
    if remark is not None and not isinstance(remark, str):
        raise ValueError('공식 선수 이력 형식 오류')
    rows = []
    for line in (remark or '').splitlines():
        # 정확한 한 날짜와 이벤트가 있는 원문만 사용. 기간·연도 명단은 제외.
        match = re.fullmatch(r'\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s+([^《》]+?)\s*《([^《》]+)》\s*', line)
        if not match:
            continue
        year, month, day, team, event = match.groups()
        if re.search(r'[~～至]|\d{1,2}[/-]\d{1,2}', team):
            continue
        when = date(int(year), int(month), int(day))
        if when > today:
            continue
        rows.append({'date': when.isoformat(), 'player': basic['CHName'],
                     'playerEn': basic.get('Engname') or '', 'acnt': acnt,
                     'team': re.sub(r'\s+', '', team), 'event': event.strip(),
                     'source': SOURCE + '/players/' + acnt,
                     'sourceText': line.strip(), 'sourceKind': 'profile-remark'})
    return rows


def collect_profiles(today, getter=None):
    getter = getter or get_json
    payload = getter('/v1/players/autocomplete')
    try:
        players = payload['Data']['Players']
    except (KeyError, TypeError) as exc:
        raise ValueError('공식 선수 목록 누락') from exc
    if not isinstance(players, list) or not players:
        raise ValueError('공식 선수 목록이 비어 있음')
    ids = []
    for player in players:
        acnt = player.get('Acnt')
        if not isinstance(acnt, str) or not re.fullmatch(r'\d{10}', acnt) or acnt in ids:
            raise ValueError('공식 선수 목록 식별자 오류')
        ids.append(acnt)
    def collect(acnt):
        return parse_profile(getter('/v1/players/' + acnt), acnt, today)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for result in pool.map(collect, ids) for row in result]
    return {'rows': rows, 'profilesChecked': len(ids)}


def merge_profile_rows(previous, additions):
    """Add-only: partial profile remarks must never replace a whole movement month."""
    def key(row):
        return (row['date'], row.get('acnt') or row.get('player'),
                re.sub(r'\s+', '', row.get('team', '')), row.get('event'))
    merged = {key(row): dict(row) for row in previous}
    for row in additions:
        if key(row) not in merged:
            merged[key(row)] = row
        elif row.get('playerEn') and not merged[key(row)].get('playerEn'):
            merged[key(row)]['playerEn'] = row['playerEn']
    return sorted(merged.values(), key=lambda row: (row['date'], row.get('player', '')), reverse=True)

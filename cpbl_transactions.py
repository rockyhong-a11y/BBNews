"""Pure parsing and cache helpers for the official CPBL movement pages.

No URLs are guessed here: requests must use a Year/Month form discovered in
the official page.  A validated empty month is different from a failed fetch.
"""

import re
from datetime import date, timedelta
from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup


_OFFICIAL_HOSTS = {"cpbl.com.tw", "www.cpbl.com.tw", "en.cpbl.com.tw"}
_HEADER_ALIASES = {
    "date": {"異動日期", "日期", "date", "transactiondate"},
    "player": {"球員", "球員姓名", "player", "playername"},
    "team": {"球隊", "team"},
    "event": {"異動原因", "reason", "transaction", "transactionreason"},
}
_EMPTY_RE = re.compile(
    r"查無資料|無異動資料|本月無異動|沒有資料|無資料|"
    r"\bno\s+(?:data|records|transactions)(?:\s+(?:found|available))?\b",
    re.IGNORECASE,
)
_BLOCK_MARKERS = (
    "just a moment", "cf-browser-verification", "attention required",
    "access denied", "anti-ddos", "flood protection",
)


def _text(cell):
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip() if cell else ""


def _parse_date(text, default_year):
    full = re.search(r"(?<!\d)(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})(?!\d)", text)
    short = None if full else re.search(r"(?<![\d/.\-])(\d{1,2})[/.\-](\d{1,2})(?![\d/.\-])", text)
    if full:
        parts = tuple(map(int, full.groups()))
    elif short:
        parts = (int(default_year), *map(int, short.groups()))
    else:
        return None
    try:
        return date(*parts).isoformat()
    except ValueError:
        return None


def _header_columns(cells):
    columns = {}
    for index, cell in enumerate(cells):
        label = re.sub(r"[\s:：()（）_\-]+", "", _text(cell)).lower()
        for field, aliases in _HEADER_ALIASES.items():
            if label in aliases:
                columns.setdefault(field, index)
    return columns if len(columns) == 4 else None


def _table_grid(table):
    """Expand rowspan/colspan without including rows from nested tables."""
    trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    grid = {}
    for row_index, tr in enumerate(trs):
        col_index = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while (row_index, col_index) in grid:
                col_index += 1
            try:
                rowspan = max(1, min(int(cell.get("rowspan", 1)), len(trs) - row_index))
                colspan = max(1, min(int(cell.get("colspan", 1)), 32))
            except (TypeError, ValueError):
                rowspan = colspan = 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(row_index + dr, col_index + dc)] = cell
            col_index += colspan
    width = max((column for _, column in grid), default=-1) + 1
    return trs, [[grid.get((index, column)) for column in range(width)]
                 for index in range(len(trs))]


def _movement_tables(soup):
    for table in soup.find_all("table"):
        trs, grid = _table_grid(table)
        for index, cells in enumerate(grid):
            columns = _header_columns(cells)
            if columns:
                yield trs, grid, index, columns
                break


def _player(cell):
    acnt = None
    link = None
    for candidate in cell.find_all("a", href=True):
        values = [value for key, value in parse_qsl(urlsplit(candidate["href"]).query)
                  if key.lower() == "acnt" and value]
        if values:
            acnt, link = values[0], candidate
            break
    # The official footnote symbols are annotations, not part of the name.
    name = re.sub(r"[▲＃#✽◎]", "", _text(link or cell))
    return re.sub(r"\s+", " ", name).strip(), acnt


def _parse_table(trs, grid, header_index, columns, default_year):
    rows, malformed = [], False
    previous_date = None
    for index in range(header_index + 1, len(trs)):
        cells = grid[index]
        raw_cells = trs[index].find_all(["td", "th"], recursive=False)
        # 일부 공식 표는 후속 행에서 빈 날짜 td 자체를 생략한다.
        # 표준 네 열 순서이며 rowspan도 채우지 않은 세 셀 행만 승계한다.
        if (previous_date and columns == {"date": 0, "player": 1, "team": 2, "event": 3}
                and len(cells) == 4 and cells[3] is None and len(raw_cells) == 3
                and all(cell.get("colspan", "1") == "1" for cell in raw_cells)
                and not re.search(r"\d+[/.\-]\d+", _text(raw_cells[0]))):
            cells = [None, *raw_cells]
        texts = [_text(cell) for cell in cells]
        if not any(texts) or _header_columns(cells) or trs[index].find_parent("tfoot"):
            continue
        if _EMPTY_RE.search(" ".join(texts)):
            # Empty-state rows contain a single spanning cell; an ordinary
            # movement mentioning "no data" must still pass normal validation.
            if len({id(cell) for cell in cells if cell is not None}) == 1:
                continue
        date_text = texts[columns["date"]]
        parsed_date = _parse_date(date_text, default_year) if date_text else previous_date
        player_cell = cells[columns["player"]]
        player, acnt = _player(player_cell) if player_cell else ("", None)
        team, event = texts[columns["team"]], texts[columns["event"]]
        if not parsed_date or not player or not team or not event:
            malformed = True
            # A malformed explicit date must not leak the previous date into
            # subsequent blank-date rows.
            if date_text:
                previous_date = None
            continue
        previous_date = parsed_date
        rows.append({"date": parsed_date, "player": player, "team": team,
                     "event": event, "acnt": acnt})
    return rows, malformed


def parse_rows(html, default_year):
    """Parse only tables with all four official movement column headers."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for table in _movement_tables(soup):
        parsed, _ = _parse_table(*table, default_year)
        rows.extend(parsed)
    return rows


def _origin(url):
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid official URL") from exc
    if (parts.scheme.lower() != "https" or parts.hostname not in _OFFICIAL_HOSTS
            or parts.username or parts.password or port not in (None, 443)):
        raise ValueError("A HTTPS official CPBL URL is required")
    return parts.scheme.lower(), parts.hostname, port or 443


def _control_value(control):
    if control.name == "select":
        options = [option for option in control.find_all("option") if not option.has_attr("disabled")]
        selected = next((option for option in options if option.has_attr("selected")),
                        options[0] if options else None)
        if selected is None:
            return ""
        return selected.get("value", selected.get_text(strip=True))
    if control.name == "textarea":
        return control.get_text()
    return control.get("value", "")


def query_form(html, page_url):
    """Discover a Year/Month form and retain its successful form controls.

    The returned action is allowed only on the same official CPBL origin as
    page_url. Missing or ambiguous month forms fail instead of guessing a URL.
    """
    page_origin = _origin(page_url)
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for form in soup.find_all("form"):
        fields, controls = {}, {}
        for control in form.find_all(["input", "select", "textarea"]):
            name = control.get("name")
            if not name or control.has_attr("disabled"):
                continue
            kind = control.get("type", "text").lower()
            if kind in {"submit", "button", "reset", "file", "image"}:
                continue
            if kind in {"checkbox", "radio"} and not control.has_attr("checked"):
                continue
            controls[name] = _control_value(control)
            if name.lower() in {"year", "month"}:
                fields[name.lower()] = name
        if set(fields) != {"year", "month"}:
            continue
        action = urljoin(page_url, form.get("action") or page_url)
        if _origin(action) != page_origin:
            raise ValueError("Movement form action must keep the official page origin")
        method = form.get("method", "get").lower()
        if method not in {"get", "post"}:
            raise ValueError("Unsupported movement form method")
        candidates.append({"url": action, "method": method, "data": controls,
                           "year_field": fields["year"], "month_field": fields["month"]})
    if len(candidates) != 1:
        raise ValueError("Exactly one official Year/Month form is required")
    return candidates[0]


def parse_month_page(html, year, month, page_url="https://www.cpbl.com.tw/player/trans"):
    """Validate a requested month, including genuine zero-transaction months.

    A movement form with the selected requested scope is mandatory. A movement
    table (including a header-only empty table) or explicit empty-state message
    must also exist; errors and unrelated pages raise ValueError.
    """
    try:
        date(int(year), int(month), 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid requested year/month") from exc
    low = html.lower()
    if any(marker in low for marker in _BLOCK_MARKERS) or html.strip().lower() == "not found":
        raise ValueError("CPBL returned a blocked/error page")
    form = query_form(html, page_url)
    try:
        selected_year = int(form["data"][form["year_field"]])
        selected_month = int(form["data"][form["month_field"]])
    except (TypeError, ValueError) as exc:
        raise ValueError("The movement form has no selected year/month") from exc
    if (selected_year, selected_month) != (int(year), int(month)):
        raise ValueError("Returned movement month does not match the requested scope")
    soup = BeautifulSoup(html, "html.parser")
    tables = list(_movement_tables(soup))
    if not tables:
        if _EMPTY_RE.search(soup.get_text(" ", strip=True)):
            return []
        raise ValueError("No official movement table or empty state was returned")
    rows = []
    for table in tables:
        parsed, malformed = _parse_table(*table, int(year))
        if malformed:
            raise ValueError("Malformed movement table rows")
        rows.extend(parsed)
    if any(date.fromisoformat(row["date"]).timetuple()[:2] != (int(year), int(month)) for row in rows):
        raise ValueError("Movement rows fall outside the requested month")
    return rows


def months_to_refresh(now, days=7):
    """Return ascending months covering now and the previous `days` days."""
    if not isinstance(days, int) or days < 0:
        raise ValueError("days must be a nonnegative integer")
    start = now - timedelta(days=days)
    current = (start.year, start.month)
    end = (now.year, now.month)
    months = []
    while current <= end:
        months.append(current)
        current = (current[0] + 1, 1) if current[1] == 12 else (current[0], current[1] + 1)
    return months


def _row_month(row):
    try:
        parsed = date.fromisoformat(row["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Cache row has an invalid calendar date") from exc
    return parsed.year, parsed.month


def _identity(row):
    return row.get("acnt") or row.get("player", "")


def _row_key(row):
    return row["date"], _identity(row), row.get("team", ""), row.get("event", "")


def merge_month_rows(existing_rows, month_rows_mapping):
    """Replace only validated queried months and keep history/English names.

    Empty replacement lists deliberately clear only their matching month. An
    out-of-scope or invalid replacement raises instead of damaging the cache.
    """
    replacements = {}
    for scope, rows in month_rows_mapping.items():
        try:
            year, month = scope
            date(year, month, 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid replacement month") from exc
        replacements[(year, month)] = list(rows)
        if any(_row_month(row) != (year, month) for row in replacements[(year, month)]):
            raise ValueError("Replacement row is outside its validated month")
    existing = list(existing_rows)
    english_by_key = {_row_key(row): row["playerEn"] for row in existing if row.get("playerEn")}
    english_by_player = {_identity(row): row["playerEn"] for row in existing
                         if row.get("playerEn") and _identity(row)}
    preserved = [row for row in existing if _row_month(row) not in replacements]
    combined = preserved + [row for scope in sorted(replacements) for row in replacements[scope]]
    unique = {}
    for original in combined:
        row = dict(original)
        key = _row_key(row)
        row["playerEn"] = (row.get("playerEn") or english_by_key.get(key)
                           or english_by_player.get(_identity(row)) or "")
        if key in unique and not row["playerEn"]:
            row["playerEn"] = unique[key].get("playerEn", "")
        unique[key] = row
    return sorted(unique.values(), key=lambda row: row["date"], reverse=True)

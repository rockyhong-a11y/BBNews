#!/usr/bin/env python3
"""
야구 뉴스 대시보드 — 로컬 웹서버 + KBO 프록시

사용법:
    python3 serve.py            # 기본 포트 8000
    python3 serve.py 8080       # 다른 포트

브라우저에서 열기:
    http://localhost:8000/

KBO 서버가 공개 CORS 프록시 IP를 모두 차단해서, 이 로컬 서버가
같은 출처(same-origin)로 KBO API를 위임 호출합니다. 매 페이지 로드마다
실시간 최신 데이터를 받아옵니다.
"""
import http.server
import urllib.request
import urllib.parse
import urllib.error
import json
import sys
import os
from datetime import datetime


KBO_API = "https://www.koreabaseball.com/ws/Player.asmx/GetTradeList"
KBO_REFERER = "https://www.koreabaseball.com/Player/Trade.aspx"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class Handler(http.server.SimpleHTTPRequestHandler):
    # 로그 간소화: KBO 요청은 별도 출력, 그 외는 표시 안 함
    def log_message(self, format, *args):
        if "/api/" in self.path:
            sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {self.command} {self.path}\n")

    def do_GET(self):
        if self.path.startswith("/api/kbo"):
            self._handle_kbo()
            return
        # 그 외에는 정적 파일 서빙
        return super().do_GET()

    def _handle_kbo(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        year = qs.get("year", [str(datetime.now().year)])[0]
        count = qs.get("count", ["300"])[0]

        params = urllib.parse.urlencode({
            "seasonId": year,
            "monthId": "0",
            "bdSc": "0",
            "teamName": "",
            "searchIf": "",
            "pageNo": "1",
            "listCount": count,
        })
        target = f"{KBO_API}?{params}"

        try:
            req = urllib.request.Request(target, headers={
                "User-Agent": USER_AGENT,
                "Referer": KBO_REFERER,
                "Accept": "*/*",
                "Accept-Language": "ko-KR,ko;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read()
            # 캐시 파일도 동시에 갱신 (백업용)
            try:
                with open(os.path.join(os.path.dirname(__file__), "kbo-cache.json"), "wb") as f:
                    f.write(body)
            except Exception:
                pass

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            # 행 개수 로그
            try:
                rows = json.loads(body).get("rows", [])
                sys.stderr.write(f"    ↳ KBO {len(rows)}건 수신 → 캐시 갱신\n")
            except Exception:
                pass
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"HTTP {e.code}"}).encode())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  🚀  야구 뉴스 대시보드 서버 시작")
    print(f"  📍  http://localhost:{port}/")
    print(f"  ⚾  KBO 자동 fetch:  http://localhost:{port}/api/kbo")
    print(f"  ⏹  Ctrl+C 로 중지\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  종료\n")


if __name__ == "__main__":
    main()

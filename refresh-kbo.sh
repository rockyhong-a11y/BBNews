#!/bin/bash
# KBO 트랜잭션 데이터 갱신 스크립트
# GitHub Actions에서 자동 실행됨 (하루 4회)

set -euo pipefail
cd "$(dirname "$0")"

YEAR="${1:-$(date +%Y)}"
URL="https://www.koreabaseball.com/ws/Player.asmx/GetTradeList"
PARAMS="seasonId=${YEAR}&monthId=0&bdSc=0&teamName=&searchIf=&pageNo=1&listCount=300"
TMP="kbo-cache.tmp.json"

echo "🔄 KBO 데이터 fetch 중 (year=${YEAR})..."

curl -sf "${URL}?${PARAMS}" \
  -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
  -H "Referer: https://www.koreabaseball.com/Player/Trade.aspx" \
  -H "Accept: application/json, */*" \
  -H "Accept-Language: ko-KR,ko;q=0.9" \
  --max-time 30 \
  -o "$TMP"

# 유효한 JSON인지 확인
ROWS=$(python3 -c "import json,sys; d=json.load(open('$TMP')); r=d.get('rows',[]); print(len(r)); sys.exit(0 if r else 1)" 2>/dev/null) || {
  echo "❌ 유효한 데이터 없음 — 기존 캐시 유지"
  rm -f "$TMP"
  exit 1
}

mv "$TMP" kbo-cache.json

LATEST=$(python3 -c "import json; rows=json.load(open('kbo-cache.json')).get('rows',[]); print(rows[0]['row'][0]['Text'] if rows else '-')" 2>/dev/null || echo "-")

echo "✅ kbo-cache.json 저장 완료"
echo "   레코드 수: ${ROWS}건"
echo "   최신 날짜: ${LATEST}"

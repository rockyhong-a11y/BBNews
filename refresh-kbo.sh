#!/bin/bash
# KBO 트랜잭션 데이터 갱신 스크립트
# 사용: ./refresh-kbo.sh
#
# KBO 서버가 CORS 프록시 IP를 차단하고 Referer 헤더를 요구하므로
# 브라우저에서 직접 fetch가 불가능. 이 스크립트로 로컬 캐시를 갱신한다.

set -e
cd "$(dirname "$0")"

YEAR="${1:-$(date +%Y)}"
URL="https://www.koreabaseball.com/ws/Player.asmx/GetTradeList"
PARAMS="seasonId=${YEAR}&monthId=0&bdSc=0&teamName=&searchIf=&pageNo=1&listCount=300"

echo "🔄 KBO 데이터 fetch 중 (year=${YEAR})..."

curl -s "${URL}?${PARAMS}" \
  -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
  -H "Referer: https://www.koreabaseball.com/Player/Trade.aspx" \
  -H "Accept: */*" \
  -H "Accept-Language: ko-KR,ko;q=0.9" \
  -o kbo-cache.json

SIZE=$(wc -c < kbo-cache.json)
ROWS=$(python3 -c "import json; print(len(json.load(open('kbo-cache.json'))['rows']))" 2>/dev/null || echo "?")

echo "✅ kbo-cache.json 저장 완료"
echo "   파일 크기: ${SIZE} bytes"
echo "   레코드 수: ${ROWS} 건"
echo ""
echo "이제 index.html을 새로고침 (Cmd+Shift+R)하세요."

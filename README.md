# 지금 뜨는 뉴스·검색어 — v0.2

Windows PowerShell 또는 cmd에서 이 폴더로 이동한 뒤:

```
python check_feeds.py          # 1) 피드 84개 + 포털 3곳 점검 → feeds_report.md, feeds.draft.json
python trend_site.py --open    # 2) 실제 수집 → site\index.html 브라우저로 열림
python trend_site.py --demo    # 샘플 데이터로 화면만 확인 (인터넷 불필요)
python trend_site.py --rebuild --open  # 수집 없이 마지막 data.json으로 화면만 다시 생성
```

또는 `run.bat` 더블클릭.

## 파일

| 파일 | 용도 |
|---|---|
| `trend_site.py` | 수집·묶기·HTML 생성기 (표준 라이브러리만) |
| `feeds.json` | 언론사×분야 RSS 목록. 실패하는 줄은 지우거나 교체 |
| `check_feeds.py` | 피드·포털 점검 스크립트 |
| `PRD.md` | 요구사항 문서 |
| `site/index.html`, `site/data.json` | 생성 결과 |
| `history/` | 시각별 데이터(7일 보관) |

## 피드 점검 뒤 할 일

`feeds_report.md`에서 FAIL인 줄을 `feeds.json`에서 지우면 됩니다. 성공한 피드만 모은 `feeds.draft.json`을 `feeds.json`으로 바꿔 써도 됩니다.

## 텔레그램 알림(선택)

```
setx TELEGRAM_BOT_TOKEN "123456:ABC..."
setx TELEGRAM_CHAT_ID "123456789"
```
새 터미널을 열어야 적용됩니다. 전체 실패 또는 성공률 50% 미만일 때만 보냅니다.

## 디자인

irumhahn.com 스타일(종이색 배경, Noto Serif KR 제호, Pretendard 본문, 빨강·청록 강조, 신문형 2단). 두 줄 선은 쓰지 않고 한 줄 실선만 사용. 다크모드 자동.

## 포털

네이트·네이버는 robots.txt가 전체 차단이라 수집하지 않습니다. 줌 메인의 '많이 본 뉴스'와 '이슈 키워드'만 가져오며, 받은 원본은 `debug/zum.html`에 남습니다.

## 자동 갱신과 배포

| 단계 | 내용 |
|---|---|
| GitHub Actions | `.github/workflows/update.yml` — 매시 7분(KST)에 `trend_site.py` 실행 |
| deploy 브랜치 | 실행 결과 `site/`와 `history/`를 보관(다음 실행이 history를 이어받음). 매번 덮어써서 커밋이 쌓이지 않음 |
| Cloudflare Pages | 프로젝트 `news`. wrangler로 `site/`를 직접 업로드(Git 연결 아님 — 무료 빌드 월 500회 제한 회피) |
| 필요한 Secrets | 토큰: `NEWS`(또는 `CLOUDFLARE_API_TOKEN`, 권한 Account › Cloudflare Pages › Edit) · 계정 ID: `NEWS1`(또는 `CLOUDFLARE_ACCOUNT_ID`) |
| 도메인 | news.rajin.live |
| 검색 노출 | `robots.txt` 전체 차단 + `X-Robots-Tag: noindex` |

텔레그램 알림을 받으려면 GitHub 리포지토리 Settings → Secrets and variables → Actions에
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 넣으면 됩니다.

수동 실행: GitHub 리포지토리 → Actions → update → Run workflow

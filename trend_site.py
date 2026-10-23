#!/usr/bin/env python3
"""trend_site.py v0.2 — 지금 뜨는 뉴스·검색어 (개인용 정적 사이트 생성기)

표준 라이브러리만 사용. Python 3.9+.

  python trend_site.py              # 수집 → site/index.html, site/data.json, history/ 저장
  python trend_site.py --open       # 생성 후 브라우저 열기
  python trend_site.py --demo       # 샘플 데이터(오프라인)
  python trend_site.py --hours 24   # 최근 N시간만 (기본 36)
  python trend_site.py --no-portal  # 포털 인기뉴스 수집 생략

환경변수(선택): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — 설정 시 전체 실패/저조 때 알림
"""
import argparse, html, json, os, re, sys, time, urllib.request, urllib.parse, urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trend_site/0.2 (personal)"
TIMEOUT = 15
CATEGORIES = ["전체", "정치", "경제", "주식", "사회", "스포츠", "연예", "도서관", "파주"]
# 관심 분야 탭: 구글뉴스 검색으로 모은 기사 + 수집한 모든 기사 중 제목에 해당 낱말이 있는 것.
# 다룬 곳이 적어서 한 언론사만 쓴 기사도 보여 준다. 이 탭에서만 쓰는 기사는 전체·주식 탭에 넣지 않는다.
NICHE = {
    "도서관": re.compile(r"도서관|독서|북스타트|그림책|도서전|책 ?읽|사서(?:들|가|는|의|를|와|로|교사)"),
    "파주": re.compile(r"파주|운정|교하|금촌|헤이리|임진각|출판도시|파평|문산읍"),
}
# 주식 탭: 증권 RSS + 제목에 증시 낱말이 있는 기사
STOCK_RX = re.compile(r"코스피|코스닥|증시|주가|주식|상장|공모주|IPO|ETF|ETN|목표가|순매수|순매도|시가총액|시총|공매도|"
                      r"자사주|배당주|증권사|리포트|투자의견|레버리지|인버스|나스닥|다우지수|S&P|밸류업|상한가|하한가|신고가|신저가|"
                      r"반도체주|2차전지주|[가-힣]{1,6}주 (급등|급락|강세|약세)|개미|외국인 (순)?매(수|도)|기관 (순)?매(수|도)", re.I)


def is_stock(a):
    return "주식" in a.get("cats", {a.get("category")}) or bool(STOCK_RX.search(a["title"]))


def stock_trends(trends):
    return [t for t in trends if STOCK_RX.search(t["keyword"]) or any(STOCK_RX.search(n.get("title", "")) for n in t["news"])]
HERE = os.path.dirname(os.path.abspath(__file__))

STOP = set("""
의 가 이 은 는 을 를 에 에서 와 과 도 로 으로 부터 까지 에게 한테 께 보다 처럼 만 마저 조차 뿐 및 등 또 또한 그리고 그러나 하지만
오늘 내일 어제 올해 내년 지난 이번 최근 현재 관련 대한 위해 통해 대해 위한 따른 속보 단독 종합 영상 사진 포토 기자 뉴스 속
수 것 때 중 후 전 간 명 개 건 차 번 년 월 일 시 분 억 만 원 달러 % 등등 vs 및 한 두 세 네 첫 新 外
다시 만에 이후 결국 진짜 사실 공식 발언 논란 국감서 종합2보 종합보
""".split())
JOSA = ("에서는", "으로는", "에게는", "으로써", "으로서", "에서", "으로", "에게", "까지", "부터", "이다", "였다", "했다", "하다",
        "라고", "이라", "에는", "에도", "와의", "과의", "들이", "들은", "들을", "들의", "은", "는", "이", "가", "을", "를", "의", "에", "도", "로", "와", "과", "만", "들")


# ---------------- 수집 ----------------
def http_get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Language": "ko,en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_DATE_RX = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?\s*(Z|[+-]\d{2}:?\d{2})?")


def parse_date(s):
    """RFC822, ISO8601, '2026-10-19 15:00:00', '2026.10.19 15:00' 등을 KST datetime으로"""
    if not s:
        return None
    s = s.strip()
    d = None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            m = _DATE_RX.search(s)
            if not m:
                return None
            y, mo, da, h, mi, se, tz = m.groups()
            try:
                d = datetime(int(y), int(mo), int(da), int(h or 0), int(mi or 0), int(se or 0))
            except ValueError:
                return None
            if tz and tz != "Z":
                tz = tz.replace(":", "")
                off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * (1 if tz[0] == "+" else -1)
                d = d.replace(tzinfo=timezone(off))
            elif tz == "Z":
                d = d.replace(tzinfo=timezone.utc)
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=KST)
    return d.astimezone(KST)


def item_date(it):
    """item 안에서 날짜로 보이는 첫 태그를 찾는다 (pubDate, dc:date, published, updated, 대소문자 무관)"""
    for child in it:
        tag = child.tag.split("}")[-1].lower()
        if tag in ("pubdate", "date", "published", "updated", "pubdatetime", "lastbuilddate") or "date" in tag:
            d = parse_date(child.text or "")
            if d:
                return d
    return None


def text(el, *names):
    for n in names:
        x = el.find(n)
        if x is not None and (x.text or "").strip():
            return x.text.strip()
    return ""


ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"


_IMG_RX = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)


def item_image(it):
    """RSS/Atom item에서 썸네일 주소 찾기: media:content/thumbnail, enclosure, 본문 첫 <img>. 저장하지 않고 주소만 쓴다."""
    for el in it.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag in ("thumbnail", "content") and el.get("url"):
            if tag == "thumbnail" or (el.get("medium") == "image" or "image" in (el.get("type") or "") or
                                      re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", el.get("url"), re.I)):
                return el.get("url")
        if tag == "enclosure" and el.get("url") and "image" in (el.get("type") or "image"):
            return el.get("url")
        if tag == "link" and el.get("rel") == "enclosure" and "image" in (el.get("type") or ""):
            return el.get("href")
    for el in it.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag in ("description", "encoded", "content", "summary") and el.text:
            m = _IMG_RX.search(html.unescape(el.text))
            if m:
                return m.group(1)
    return None


def gnews_search(q):
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(q) + "&hl=ko&gl=KR&ceid=KR:ko"


# 연합뉴스 대체 검색: 분야별로 연합뉴스 기사 주소 + 분야 낱말 (분야 탭이 비지 않게)
YNA_FALLBACK = [
    ("전체", "site:yna.co.kr when:1d"),
    ("정치", "site:yna.co.kr (대통령 OR 국회 OR 여당 OR 야당 OR 정부 OR 북한) when:1d"),
    ("경제", "site:yna.co.kr (경제 OR 금리 OR 물가 OR 수출 OR 부동산 OR 기업) when:1d"),
    ("사회", "site:yna.co.kr (경찰 OR 법원 OR 검찰 OR 사고 OR 교육 OR 날씨) when:1d"),
    ("스포츠", "site:yna.co.kr (야구 OR 축구 OR 농구 OR 배구 OR 골프 OR 경기) when:1d"),
    ("연예", "site:yna.co.kr (가수 OR 배우 OR 드라마 OR 영화 OR 아이돌 OR 방송) when:1d"),
    ("주식", "site:yna.co.kr (코스피 OR 코스닥 OR 증시 OR 주가) when:1d"),
]


def fetch_feed(feed):
    """feed = {outlet, category, url} → (feed, items, error)"""
    try:
        body = http_get(feed["url"])
        try:
            root = ET.fromstring(body)
        except ET.ParseError:  # 깨진 XML(맨 & 문자, 제어 문자 — 연합뉴스 사회 등): 고쳐서 다시 읽기
            body = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", body)
            body = re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", body)
            root = ET.fromstring(body)
    except Exception as e:
        return feed, [], f"{type(e).__name__}: {str(e)[:70]}"
    items = []
    for it in root.iter("item"):
        src = it.find("source")
        if src is not None and src.text and src.get("url"):
            learn_domain(src.get("url"), src.text.strip())  # 구글뉴스가 알려 준 '주소→언론사 이름' (네이버 기사 출처 표시에 씀)
        items.append({"title": text(it, "title"), "url": text(it, "link") or (it.find("guid").text if it.find("guid") is not None else ""),
                      "date": item_date(it), "img": item_image(it),
                      "_src": (src.text or "").strip() if src is not None else ""})
    if not items:
        for it in root.iter(ATOM + "entry"):
            link = it.find(ATOM + "link")
            items.append({"title": text(it, ATOM + "title"),
                          "url": link.get("href", "") if link is not None else "",
                          "date": item_date(it), "img": item_image(it)})
    for a in items:
        src = re.sub(r"^https?://|/+$", "", a.pop("_src", "")).strip()  # 이름 대신 주소가 온 경우 정리
        a["outlet"] = src if feed.get("by_source") and src else feed["outlet"]  # 구글뉴스 검색: 실제 언론사 이름으로
        a["category"] = feed["category"]
    return feed, items, None


# ---------------- 네이버 뉴스 검색 API (공식, 2026-10-24 추가) ----------------
# 네이버 뉴스 페이지는 robots.txt가 막아서 긁지 않는다. 대신 개발자센터의 '검색 > 뉴스' API를 쓴다.
# 키: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (GitHub Secrets). 없으면 건너뛴다. 하루 25,000회 무료.
NAVER_API = "https://openapi.naver.com/v1/search/news.json"
NAVER_NICHE = [("도서관", "도서관"), ("도서관", "작은도서관"), ("파주", "파주"), ("파주", "운정")]
DOMAIN_CACHE = os.path.join(HERE, "history", "outlet_domains.json")
DOMAIN_NAMES = {  # 자주 나오는 곳은 미리 적어 둔다. 나머지는 구글뉴스 출처 정보로 배운다.
    "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "news1.kr": "뉴스1", "chosun.com": "조선일보", "donga.com": "동아일보",
    "joongang.co.kr": "중앙일보", "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "hankookilbo.com": "한국일보",
    "mk.co.kr": "매일경제", "hankyung.com": "한국경제", "sedaily.com": "서울경제", "mt.co.kr": "머니투데이", "edaily.co.kr": "이데일리",
    "fnnews.com": "파이낸셜뉴스", "asiae.co.kr": "아시아경제", "heraldcorp.com": "헤럴드경제", "kmib.co.kr": "국민일보",
    "segye.com": "세계일보", "seoul.co.kr": "서울신문", "munhwa.com": "문화일보", "nocutnews.co.kr": "노컷뉴스",
    "ohmynews.com": "오마이뉴스", "sbs.co.kr": "SBS", "kbs.co.kr": "KBS", "imbc.com": "MBC", "jtbc.co.kr": "JTBC",
    "ytn.co.kr": "YTN", "mbn.co.kr": "MBN", "tvchosun.com": "TV조선", "ichannela.com": "채널A", "kyeonggi.com": "경기일보",
    "kyeongin.com": "경인일보", "joongboo.com": "중부일보", "newspim.com": "뉴스핌", "inews24.com": "아이뉴스24",
    "dt.co.kr": "디지털타임스", "etnews.com": "전자신문", "naver.com": "네이버뉴스", "zdnet.co.kr": "지디넷코리아", "biz.chosun.com": "조선비즈",
}


def _host(url):
    h = urllib.parse.urlsplit(url if "//" in url else "//" + url).hostname or ""
    return re.sub(r"^(?:www|m|mobile)\.", "", h.lower())


def learn_domain(url, name):
    h = _host(url)
    if h and name and not re.match(r"^[\w.-]+\.[a-z]{2,}$", name):  # 이름 자리에 주소가 온 경우는 배우지 않는다
        DOMAIN_NAMES.setdefault(h, name)


def outlet_of(url):
    """기사 주소 → 언론사 이름. 모르면 주소(도메인)를 그대로 쓴다"""
    h = _host(url)
    parts = h.split(".")
    for i in range(len(parts) - 1):  # news.kbs.co.kr → kbs.co.kr 순으로
        cand = ".".join(parts[i:])
        if cand in DOMAIN_NAMES:
            return DOMAIN_NAMES[cand]
        if len(parts) - i <= 2 or cand in ("co.kr", "or.kr", "go.kr"):
            break
    return h or "네이버뉴스"


def load_domains():
    try:
        for k, v in json.load(open(DOMAIN_CACHE, encoding="utf-8")).items():
            DOMAIN_NAMES.setdefault(k, v)
    except Exception:
        pass


def save_domains():
    try:
        os.makedirs(os.path.dirname(DOMAIN_CACHE), exist_ok=True)
        json.dump(dict(sorted(DOMAIN_NAMES.items())), open(DOMAIN_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    except Exception:
        pass


def naver_keys():
    i, s = os.environ.get("NAVER_CLIENT_ID", "").strip(), os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    return (i, s) if i and s else None


def naver_search(query, display=100, sort="date"):
    """네이버 뉴스 검색 → [{title,url,outlet,date,img}] (키 없으면 예외)"""
    keys = naver_keys()
    if not keys:
        raise RuntimeError("네이버 API 키 없음")
    url = NAVER_API + "?" + urllib.parse.urlencode({"query": query, "display": display, "sort": sort})
    req = urllib.request.Request(url, headers={"X-Naver-Client-Id": keys[0], "X-Naver-Client-Secret": keys[1], "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = []
    for it in data.get("items", []):
        link = it.get("originallink") or it.get("link") or ""
        title = html.unescape(re.sub(r"</?b>", "", it.get("title", "")))
        out.append({"title": title, "url": link, "outlet": outlet_of(link), "date": parse_date(it.get("pubDate")), "img": None})
    return out


def naver_niche():
    """관심 탭(도서관·파주)용 네이버 뉴스 검색 → (items, status)"""
    items, status = [], []
    for cat, q in NAVER_NICHE:
        try:
            rows = naver_search(q)
            err = None
        except Exception as e:
            rows, err = [], f"{type(e).__name__}: {str(e)[:60]}"
        for a in rows:
            a["category"] = cat
        items += rows
        status.append({"name": f"네이버뉴스·{cat}({q})", "ok": err is None and bool(rows), "count": len(rows), "error": err or ("0건" if not rows else "")})
    return items, status


def naver_trends(trends, now, n=20):
    """급상승 검색어마다 네이버 뉴스 최신 기사 → t['naver'](최신 3건), t['naver_1h'](최근 1시간 건수, 20건 중)"""
    ok = 0
    for t in trends[:n]:
        try:
            rows = naver_search(t["keyword"], display=20)
        except Exception:
            continue
        ok += 1
        rows = [r for r in rows if r["date"]]
        t["naver"] = [{"title": r["title"], "url": r["url"], "outlet": r["outlet"], "date": r["date"].isoformat()} for r in rows[:3]]
        t["naver_1h"] = sum(1 for r in rows if now - r["date"] <= timedelta(hours=1))
    return ok


def fetch_trends():
    """구글 트렌드 KR 급상승 RSS"""
    url = "https://trends.google.com/trending/rss?geo=KR"
    try:
        root = ET.fromstring(http_get(url))
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:70]}"
    ns = "{https://trends.google.com/trending/rss}"
    out = []
    for it in root.iter("item"):
        kw = text(it, "title")
        if not kw:
            continue
        news = []
        for n in it.findall(ns + "news_item"):
            news.append({"title": text(n, ns + "news_item_title"), "url": text(n, ns + "news_item_url"),
                         "outlet": text(n, ns + "news_item_source")})
        start = parse_date(text(it, "pubDate"))
        out.append({"keyword": kw, "traffic": text(it, ns + "approx_traffic"), "news": news[:3],
                    "start": start.isoformat() if start else None})
    return out[:20], None


# ---------------- 포털 인기뉴스 (스크래핑, robots 확인 후) ----------------
# 2026-10-19 점검: 네이트·네이버는 robots.txt가 전체 차단(User-agent: * Disallow: /) → 수집하지 않음.
# 줌은 robots 허용. 메인(news.zum.com)에 "언론사별 가장 많이 본 뉴스(최근 1시간)"와 "AI 이슈 트렌드"가 있다.
# 기사 링크는 언론사 원문으로 바로 연결되고, 이슈 트렌드는 search.zum.com 검색 링크다.
ZUM_URL = "https://news.zum.com/"
_robots = {}


def robots_allows(url):
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(http_get(base + "/robots.txt").decode("utf-8", "ignore").splitlines())
            _robots[base] = rp
        except Exception:
            _robots[base] = None
    rp = _robots[base]
    return True if rp is None else rp.can_fetch("*", url)


def _strip_tags(x):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x))).strip()


def fetch_zum():
    """줌 메인 → [(이름, rows, 오류)] 두 개: 많이 본 뉴스, 이슈 키워드. 원본 HTML은 debug/zum.html에 남긴다."""
    names = ("언론사별 많이 본 뉴스", "줌 이슈 키워드")
    try:
        if not robots_allows(ZUM_URL):
            return [(n, [], "robots.txt 차단") for n in names]
        body = http_get(ZUM_URL).decode("utf-8", "ignore")
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:60]}"
        return [(n, [], err) for n in names]
    try:
        os.makedirs(os.path.join(HERE, "debug"), exist_ok=True)
        with open(os.path.join(HERE, "debug", "zum.html"), "w", encoding="utf-8") as f:
            f.write(body)
    except Exception:
        pass

    # 1) 많이 본 뉴스: "가장 많이 본" 문구 뒤 구간에서 외부(언론사) 링크만
    popular, seen = [], set()
    i = body.find("가장 많이 본")
    if i >= 0:
        chunk = body[i:i + 80000]
        for m in re.finditer(r'<a\b[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', chunk, re.S):
            u, t = html.unescape(m.group(1)), _strip_tags(m.group(2))
            host = urllib.parse.urlsplit(u).netloc
            if host.endswith("zum.com") or not (10 <= len(t) <= 120) or t in seen:
                continue
            seen.add(t)
            popular.append({"title": t, "url": u})
            if len(popular) >= 10:
                break

    # 2) 이슈 키워드: search.zum.com 링크의 query= 값
    issues, seen = [], set()
    for m in re.finditer(r'href="(https?://search\.zum\.com/search\.zum\?[^"]+)"', body):
        u = html.unescape(m.group(1))
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(u).query).get("query", [""])[0].strip()
        if not q or q in seen or len(q) > 40:
            continue
        seen.add(q)
        issues.append({"title": q, "url": u})
        if len(issues) >= 10:
            break

    return [(names[0], popular, None if popular else "'가장 많이 본' 구간에서 기사를 찾지 못함 (debug/zum.html 확인)"),
            (names[1], issues, None if issues else "이슈 키워드 링크를 찾지 못함 (debug/zum.html 확인)")]


# ---------------- 그래픽 뉴스 ----------------
YNA_GRAPHIC = "https://www.yna.co.kr/graphic/index"   # robots.txt: User-agent * Allow:/ (2026-10-19 확인)
GRAPHIC_SEARCH = ["https://news.google.com/rss/search?q=intitle:%EA%B7%B8%EB%9E%98%ED%94%BD+when:2d&hl=ko&gl=KR&ceid=KR:ko"]  # intitle:그래픽
GRAPHIC_RX = re.compile(r"^\s*[\[【(〔<]\s*(인포그래픽|그래픽\s*뉴스|그래픽|그래픽으로 보는[^\]]*|데이터\s*그래픽)\s*[\]】)〕>]")


def fetch_yna_graphic(limit=24):
    """연합뉴스 그래픽 목록: 제목·썸네일·시각"""
    try:
        if not robots_allows(YNA_GRAPHIC):
            return [], "robots.txt 차단"
        body = http_get(YNA_GRAPHIC).decode("utf-8", "ignore")
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:60]}"
    out, seen = [], set()
    for li in re.findall(r"<li\b[^>]*>(.*?)</li>", body, re.S):
        m = re.search(r'href="(https?://www\.yna\.co\.kr/view/(GYH\d+))', li)
        if not m or m.group(2) in seen:
            continue
        t = re.search(r'class="title01"[^>]*>(.*?)</span>', li, re.S) or re.search(r'alt="([^"]+)"', li)
        im = re.search(r'<img[^>]+src="(https?://[^"]+)"', li)
        tm = re.search(r'class="txt-time"[^>]*>([^<]+)<', li)
        if not t:
            continue
        seen.add(m.group(2))
        d = parse_date(tm.group(1)) if tm else None
        out.append({"title": _strip_tags(t.group(1)), "url": f"https://www.yna.co.kr/view/{m.group(2)}",
                    "img": im.group(1) if im else None, "outlet": "연합뉴스", "date": d.isoformat() if d else None})
        if len(out) >= limit:
            break
    return out, None if out else "목록을 찾지 못함(페이지 구조 변경?)"


NEWSIS_GRAPHIC = "https://www.newsis.com/photo/gralist/?cid=photo"  # robots.txt: * 는 /common/ /search/ /ar_detail/ 만 차단


def fetch_newsis_graphic(limit=24):
    """뉴시스 그래픽뉴스 목록: 제목·썸네일·시각(썸네일 rnd 값)"""
    try:
        if not robots_allows(NEWSIS_GRAPHIC):
            return [], "robots.txt 차단"
        body = http_get(NEWSIS_GRAPHIC).decode("utf-8", "ignore")
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:60]}"
    out, seen = [], set()
    for li in re.findall(r"<li\b[^>]*>(.*?)</li>", body, re.S):
        m = re.search(r'href="(/photo/graview/\?id=(NISI[\d_]+)[^"]*)"', li)
        if not m or m.group(2) in seen:
            continue
        im = re.search(r'<img[^>]+src="([^"]+)"', li)
        t = re.search(r'class="txt"[^>]*>\s*<a[^>]*>(.*?)</a>', li, re.S)
        if not t:
            continue
        seen.add(m.group(2))
        img = im.group(1) if im else None
        if img and img.startswith("//"):
            img = "https:" + img
        d = None
        r = re.search(r"rnd=(\d{14})", img or "")
        if r:
            x = r.group(1)
            d = parse_date(f"{x[:4]}-{x[4:6]}-{x[6:8]} {x[8:10]}:{x[10:12]}:{x[12:14]}")
        else:
            x = re.search(r"NISI(\d{8})", m.group(2))
            d = parse_date(f"{x.group(1)[:4]}-{x.group(1)[4:6]}-{x.group(1)[6:8]}") if x else None
        out.append({"title": _strip_tags(t.group(1)), "url": "https://www.newsis.com" + html.unescape(m.group(1)),
                    "img": img, "outlet": "뉴시스", "date": d.isoformat() if d else None})
        if len(out) >= limit:
            break
    return out, None if out else "목록을 찾지 못함(페이지 구조 변경?)"


KOREAN_LATIN_OUTLETS = {"KBS", "MBC", "SBS", "JTBC", "YTN", "MBN", "EBS", "OBS", "TV조선", "CBS", "BBS", "KNN", "TBC", "UBC", "G1"}


KHAN_INTERACTIVE = "https://www.khan.co.kr/interactive"  # robots.txt: * 는 /login 등 일부만 차단 (2026-10-20 확인)


def fetch_khan_interactive(pages=2):
    """경향신문 인터랙티브 목록: 제목·썸네일·기사 주소, 날짜는 기사 번호(YYYYMMDD…)에서"""
    out, seen, err = [], set(), None
    for n in range(1, pages + 1):
        url = KHAN_INTERACTIVE + ("" if n == 1 else f"?page={n}")
        try:
            if not robots_allows(url):
                return out, "robots.txt 차단"
            body = http_get(url).decode("utf-8", "ignore")
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:60]}"
            break
        i = body.find('id="recentList"')
        chunk = body[i:] if i >= 0 else body
        for li in re.findall(r"<li\b[^>]*>(.*?)</li>", chunk, re.S):
            m = re.search(r'<a[^>]+href="(https?://www\.khan\.co\.kr/article/(\d{8})\d+)[^"]*"[^>]*>(.*?)</a>', li, re.S)
            if not m or m.group(1) in seen:
                continue
            im = re.search(r'<img[^>]+src="([^"]+)"', li)
            img = im.group(1) if im else None
            if img and img.startswith("//"):
                img = "https:" + img
            ymd = m.group(2)
            d = parse_date(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}")
            title = re.sub(r"^\s*\[인터랙티브\]\s*", "", _strip_tags(m.group(3)))
            seen.add(m.group(1))
            out.append({"title": title, "url": m.group(1), "img": img, "outlet": "경향신문", "date": d.isoformat() if d else None})
    return out, (None if out else err or "목록을 찾지 못함(페이지 구조 변경?)")


HANKOOK_INTERACTIVE = "https://www.hankookilbo.com/interactive"  # robots.txt: /search·/api 등만 차단 (2026-10-20 확인)
CHOSUN_INTERACTIVE = "https://www.chosun.com/interactive/"       # robots.txt: /search·/test 등만 차단 (2026-10-20 확인)
# 중앙일보 /digitalspecial 은 robots.txt가 막아서, 한겨레·동아는 목록 페이지가 없어서 넣지 않는다.
INTER_TAG = re.compile(r"\s*\[\s*인터랙티브\s*\]\s*")


def _jstr(v):
    try:
        return json.loads('"' + v + '"')
    except Exception:
        return v


def fetch_hankook_interactive(limit=24):
    """한국일보 인터랙티브: 페이지에 들어 있는 JSON에서 제목·배포 시각·이미지·주소"""
    try:
        if not robots_allows(HANKOOK_INTERACTIVE):
            return [], "robots.txt 차단"
        body = http_get(HANKOOK_INTERACTIVE).decode("utf-8", "ignore")
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:60]}"
    out, seen = [], set()
    for part in body.split('"title":"')[1:]:
        m = re.match(r'((?:[^"\\]|\\.)*)"', part)
        if not m:
            continue
        chunk = part[:4000].split('"title":"')[0]

        def g(k):
            x = re.search('"' + k + r'":(?:null|"((?:[^"\\]|\\.)*)")', chunk)
            return _jstr(x.group(1)) if x and x.group(1) is not None else None
        url, dt = g("articleLink"), g("deployDt")
        if not url or not dt or not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        title = INTER_TAG.sub(" ", _strip_tags(_jstr(m.group(1)).replace("<BR>", " ").replace("<br>", " "))).strip()
        d = parse_date(dt[:16])
        out.append({"title": re.sub(r"\s+", " ", title), "url": url, "img": g("webImageFilePath") or g("articleImageFilePath"),
                    "outlet": "한국일보", "date": d.isoformat() if d else None})
        if len(out) >= limit:
            break
    return out, None if out else "목록을 찾지 못함(페이지 구조 변경?)"


def fetch_chosun_interactive(limit=12):
    """조선일보 인터랙티브 목록(최신순). 날짜가 없어 주소의 연·월(/2025/, /202210/)로 표시"""
    try:
        if not robots_allows(CHOSUN_INTERACTIVE):
            return [], "robots.txt 차단"
        body = http_get(CHOSUN_INTERACTIVE).decode("utf-8", "ignore")
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:60]}"
    out, seen, last = [], set(), None
    for card in body.split("story-card-wrapper")[1:]:
        m = re.search(r'<a class="story-card__headline[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', card, re.S)
        if not m:
            continue
        url = html.unescape(m.group(1))
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        im = re.search(r'<img src="([^"]+)"', card)
        ym = re.search(r"/(20\d\d)(\d\d)?/", url)
        if ym:
            d = parse_date(f"{ym.group(1)}-{ym.group(2) or '01'}-01")
            label = ym.group(1) + (f".{ym.group(2)}" if ym.group(2) else "")
        else:
            d, label = last, ""  # 날짜 없는 칸은 바로 앞(더 최신) 항목 순서를 따른다
        last = d or last
        title = re.sub(r"\s+", " ", _strip_tags(m.group(2).replace("<br>", " ").replace("<br/>", " "))).strip()
        out.append({"title": INTER_TAG.sub(" ", title).strip(), "url": url, "img": html.unescape(im.group(1)) if im else None,
                    "outlet": "조선일보", "date": d.isoformat() if d else None, "dl": label})
        if len(out) >= limit:
            break
    return out, None if out else "목록을 찾지 못함(페이지 구조 변경?)"


INTER_HOME = {"경향신문": KHAN_INTERACTIVE, "한국일보": HANKOOK_INTERACTIVE, "조선일보": CHOSUN_INTERACTIVE}


def render_interactive(rows):
    """인터랙티브 탭: 위에 언론사 버튼, 아래에 바둑판(최신순)"""
    if not rows:
        return ""
    rows = sorted(rows, key=lambda x: x.get("date") or "", reverse=True)

    def when(x):
        if "dl" in x:
            return x["dl"]
        return datetime.fromisoformat(x["date"]).strftime("%Y.%m.%d") if x.get("date") else ""
    cnt = Counter(x["outlet"] for x in rows)
    outlets = sorted(cnt, key=lambda o: (-cnt[o], o))
    chips = (f'<button class="on" data-o="">전체<i>{len(rows)}</i></button>'
             + "".join(f'<button data-o="{esc(o)}">{esc(o)}<i>{cnt[o]}</i></button>' for o in outlets))
    cells = "".join(
        f'<a data-o="{esc(x["outlet"])}" href="{esc(x["url"])}" target="_blank" rel="noopener noreferrer"><div class="ph iph">'
        + (f'<img src="{esc(x["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">' if x.get("img") else "")
        + f'</div><div class="pt">{esc(x["title"])}</div><div class="pm"><b>{esc(x["outlet"])}</b>'
        + (f" · {when(x)}" if when(x) else "") + "</div></a>" for x in rows)
    more = " · ".join(link(u, f"{o} 인터랙티브 전체 보기 →") for o, u in INTER_HOME.items() if o in cnt)
    return (f'<div class="pg" data-c="인터랙티브" role="tabpanel"><section class="photos" style="margin-top:0">'
            f'<div class="shead" style="padding-top:12px"><h2>인터랙티브</h2><span class="en">INTERACTIVE · {len(rows)}건</span></div>'
            f'<div class="ofilter" id="if" role="group" aria-label="언론사">{chips}</div>'
            f'<p class="note" style="margin:4px 0 4px">데이터·지도·스크롤 화면으로 만든 기획 기사입니다. 누르면 원문 인터랙티브 페이지가 열립니다.</p>'
            f'<div class="pgrid g3 inter" id="ig">{cells}</div>'
            f'<p class="note">{more}</p></section></div>')


def fetch_graphic_search():
    """구글뉴스 검색 RSS: 제목에 '그래픽'이 들어간 최근 2일 기사 (여러 언론사)"""
    out, err = [], None
    for url in GRAPHIC_SEARCH:
        try:
            root = ET.fromstring(http_get(url))
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:60]}"
            continue
        for it in root.iter("item"):
            src = it.find("source")
            outlet = (src.text or "").strip() if src is not None else ""
            t = text(it, "title")
            if outlet and t.endswith(" - " + outlet):
                t = t[: -len(outlet) - 3]
            t = html.unescape(t).strip()
            if outlet and not re.search(r"[가-힣]", outlet) and outlet.upper().split()[0] not in KOREAN_LATIN_OUTLETS:
                continue  # Vietnam.vn 같은 해외 재게시 사이트 제외
            if not (GRAPHIC_RX.search(t) or re.search(r"그래픽\s*뉴스|인포그래픽|\[그래픽", t)):
                continue  # '그래픽카드' 같은 기사 제외
            d = item_date(it)
            out.append({"title": t, "url": text(it, "link"), "outlet": outlet or "구글뉴스",
                        "img": item_image(it), "date": d.isoformat() if d else None})
    return out, (None if out else err or "0건")


# ---- 구글뉴스 링크 → 원문 주소, 원문 대표 사진(og:image) ----
# 2026-10-19 결정: 구글뉴스는 robots.txt(/rss/ 포함 차단)에도 불구하고 RSS와 원문 주소 확인에 사용한다.
# 원문 언론사 페이지는 각 사이트 robots.txt를 지킨다.
GN_RX = re.compile(r"news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_-]+)")
GN_CACHE = os.path.join(HERE, "history", "gn_cache.json")


def gn_decode(url):
    """구글뉴스 기사 링크를 원문 주소로 푼다. 실패하면 None"""
    m = GN_RX.search(url or "")
    if not m:
        return None
    gid = m.group(1)
    page = http_get(f"https://news.google.com/rss/articles/{gid}").decode("utf-8", "ignore")
    sg = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and ts):
        return None
    payload = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
               f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sg.group(1)}"]')
    body = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", payload, None, "generic"]]])}).encode()
    req = urllib.request.Request("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
                                 headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        txt = r.read().decode("utf-8", "ignore")
    part = json.loads(txt.split("\n\n", 1)[1])
    res = json.loads(part[0][2])
    return res[1] if len(res) > 1 and str(res[1]).startswith("http") else None


_OG_RX = [re.compile(p, re.I) for p in (
    r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]*content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image(?::url)?["\']',
    r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']twitter:image["\']')]


def og_image(url):
    """원문 페이지의 대표 사진 주소 (robots.txt가 막으면 None)"""
    if not robots_allows(url):
        return None
    page = http_get(url)[:400000].decode("utf-8", "ignore")
    for rx in _OG_RX:
        m = rx.search(page)
        if m:
            img = html.unescape(m.group(1)).strip()
            if img.startswith("//"):
                img = "https:" + img
            return urllib.parse.urljoin(url, img) if img else None
    return None


def enrich_graphics(rows, workers=6):
    """사진 없는 구글뉴스 그래픽 기사: 원문 주소로 바꾸고 원문 대표 사진을 붙인다. 결과는 캐시."""
    try:
        cache = json.load(open(GN_CACHE, encoding="utf-8"))
    except Exception:
        cache = {}
    todo = [g for g in rows if not g.get("img") and GN_RX.search(g.get("url") or "")]

    def work(g):
        gid = GN_RX.search(g["url"]).group(1)
        if gid in cache:
            return gid, cache[gid]
        out = {"url": None, "img": None}
        try:
            out["url"] = gn_decode(g["url"])
            if out["url"]:
                out["img"] = og_image(out["url"])
        except Exception as e:
            out["err"] = f"{type(e).__name__}"
        return gid, out

    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(work, todo))
    n_img = 0
    for g, (gid, r) in zip(todo, results):
        if "err" not in r:
            cache[gid] = r
        if r.get("url"):
            g["url"] = r["url"]
        if r.get("img"):
            g["img"] = r["img"]
            n_img += 1
    try:
        os.makedirs(os.path.dirname(GN_CACHE), exist_ok=True)
        keep = dict(list(cache.items())[-3000:])
        json.dump(keep, open(GN_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return len(todo), n_img


GRAPHIC_EXCLUDE = {"시선뉴스"}  # 그래픽 탭에서 뺄 매체 (2026-10-19 요청)


def graphic_section(yna, searched, items, limit=30, extra=()):
    """다른 언론사 그래픽: 검색 결과 + 수집 기사 중 [그래픽]류 제목. 연합 그래픽 목록과 겹치는 제목은 뺀다."""
    def norm(t):
        return re.sub(r"[\W_]+", "", GRAPHIC_RX.sub("", t)).lower()
    seen = {norm(y["title"]) for y in yna}
    pool = list(extra) + list(searched) + [{"title": a["title"], "url": a["url"], "outlet": a["outlet"], "img": a.get("img"),
                              "date": a["date"].isoformat()} for a in items if GRAPHIC_RX.search(a["title"])]
    out = []
    # 사진 있는 것 먼저, 그 안에서 최신순
    for g in sorted(pool, key=lambda g: (bool(g.get("img")), g.get("date") or ""), reverse=True):
        if g.get("outlet") in GRAPHIC_EXCLUDE:
            continue
        k = norm(g["title"])
        if not k or k in seen or not re.match(r"https?://", g.get("url") or ""):
            continue
        seen.add(k)
        out.append(g)
        if len(out) >= limit:
            break
    return out


# ---------------- 정제 ----------------
def clean(items, hours, now):
    """정제. 같은 (언론사, 제목)이 여러 분야 피드에 있으면 한 건으로 합치고 분야는 cats에 모두 남긴다."""
    lo, hi = now - timedelta(hours=hours), now + timedelta(minutes=10)  # 예약 송고(미래 시각) 기사 제외
    kept, out = {}, []
    for a in items:
        if not a["date"] or not (lo <= a["date"] <= hi):
            continue
        if not a["title"] or not re.match(r"https?://", a["url"] or ""):
            continue
        t = re.sub(r"\s+", " ", html.unescape(a["title"]).replace("\ufeff", "")).strip()
        if a["outlet"] and t.endswith(" - " + a["outlet"]):
            t = t[: -len(a["outlet"]) - 3].rstrip()
        t = re.sub(r"\s*[-|–]\s*(?:[가-힣A-Za-z0-9 ]{2,12}|(?:www\.)?[\w-]+(?:\.[\w-]+)+)$", "", t)  # "제목 - 언론사/주소" 꼬리 제거(구글뉴스)
        if re.match(r"(?:www\.)?[\w-]+(?:\.[\w-]+)+$", a["outlet"] or ""):  # 언론사 이름 대신 주소가 온 경우
            a = dict(a, outlet=re.sub(r"^www\.", "", a["outlet"]))
        key = (a["outlet"], t)
        img = a.get("img") or ""
        img = img.strip() if re.match(r"(https?://|data:image/)", img.strip()) else None
        if key in kept:
            kept[key]["cats"].add(a["category"])
            kept[key]["img"] = kept[key].get("img") or img
            continue
        a = dict(a, title=t, cats={a["category"]}, img=img)
        kept[key] = a
        out.append(a)
    return out


def words(title):
    ws = set()
    for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{2,}|\d{2,}", title):
        w = w.lower()
        for j in JOSA:
            if len(w) - len(j) >= 2 and w.endswith(j):
                w = w[: -len(j)]
                break
        if len(w) >= 2 and w not in STOP:
            ws.add(w)
    return ws


# ---------------- 사건 묶기 ----------------
def cluster(items, top=30, min_outlets=2):
    items = sorted(items, key=lambda a: a["date"])
    for a in items:
        a["_w"] = words(a["title"])
    seeds = []  # [{seed, members}]
    for a in items:
        placed = False
        for c in seeds:
            s = c["seed"]["_w"]
            inter = len(s & a["_w"])
            if inter < 2:
                continue
            jac = inter / len(s | a["_w"])
            ovl = inter / min(len(s), len(a["_w"]))
            if jac >= 0.3 or ovl >= 0.6:
                c["members"].append(a)
                placed = True
                break
        if not placed:
            seeds.append({"seed": a, "members": [a]})
    out = []
    for c in seeds:
        by_outlet = {}
        for a in c["members"]:
            by_outlet.setdefault(a["outlet"], a)  # 언론사당 1건(가장 먼저)
        ms = list(by_outlet.values())
        if len(ms) < min_outlets:
            continue
        rep = max(ms, key=lambda a: sum(len(a["_w"] & b["_w"]) for b in ms))
        out.append({"title": rep["title"], "count": len(ms), "outlets": sorted(by_outlet),
                    "latest": max(a["date"] for a in c["members"]), "first": min(a["date"] for a in c["members"]),
                    "total": len(c["members"]),
                    "img": rep.get("img") or next((a["img"] for a in c["members"] if a.get("img")), None),
                    "links": [{"outlet": a["outlet"], "url": a["url"], "title": a["title"]}
                              for a in sorted(ms, key=lambda a: a["outlet"])]})
    out.sort(key=lambda c: (c["count"], c["total"], c["latest"]), reverse=True)
    return out[:top]


def keywords(items, min_outlets=3, top=40):
    per = defaultdict(set)
    for a in items:
        for w in a.get("_w") or words(a["title"]):
            per[w].add(a["outlet"])
    ks = [(w, len(o)) for w, o in per.items() if len(o) >= min_outlets and not w.isdigit()]
    ks.sort(key=lambda x: (-x[1], x[0]))
    return ks[:top]


def trend_related(trends, items, top=5):
    """검색어별로 관련 기사 제목에서 함께 나온 낱말 (구글 공식 연관검색어 아님)"""
    for t in trends:
        kw = t["keyword"].lower()
        kws = words(t["keyword"]) | {kw}
        titles = [n["title"] for n in t["news"] + t.get("naver", [])] + [a["title"] for a in items if kw in a["title"].lower()][:30]
        cnt = Counter(w for ti in titles for w in words(ti) if w not in kws and not w.isdigit()
                      and not any(w in k or k in w for k in kws))
        t["related"] = [w for w, _ in cnt.most_common(top)]
    return trends


RISE_STOP = set("""속보 단독 종합 포토 영상 오늘 내일 어제 이번 지난 올해 내년 관련 위해 대한 통해 이후 이전 첫날 마감 오후 오전 개장
전망 발표 기록 공개 확인 진행 예정 개최 참석 강조 지적 논란 의혹 사과 입장 결과 추진 계획 가능성 우려 대응 기자 뉴스 사진 만에 다시
상승 하락 급등 급락 증가 감소 최대 최고 최저 역대 사상 국내 미국 한국 정부 시장 업계 관계자
차전 후보 경신 지속 돌파 재돌파 출시 선정 개막 종료 시작 가운데 이유 모습 대해 경우 사실
대통령 매우 쭉쭉 가격 억원 조원 만원 글로벌 감독 영화 신곡 아내 남편 강조 재정 공급 가격 국감 국정감사 ai it 사업 기업 회사 투자 지원 확대 강화 경제 정책 정치 사회 문제 상황 수준 이상 이하 가장 계속 결국 모두 전국 지역 서울
마무리 성황 성황리 모집 운영 참여자 참가자 체결 협약 업무협약 개최 행사 프로그램 진행 성료 함께 함께하""".split())


def rising_words(sub, now, recent_h=3, top=10):
    """최근 recent_h시간에 갑자기 많이 쓰인 낱말 (앞선 시간대 평균과 비교). 언론사 2곳 이상 필요."""
    cut = now - timedelta(hours=recent_h)
    recent = [a for a in sub if a["date"] >= cut]
    base = [a for a in sub if a["date"] < cut]
    if not recent:
        return []
    base_h = max(1.0, (cut - min((a["date"] for a in base), default=cut)).total_seconds() / 3600)
    r_art, r_out = defaultdict(list), defaultdict(set)
    for a in recent:
        for w in a.get("_w") or words(a["title"]):
            r_art[w].append(a)
            r_out[w].add(a["outlet"])
    b_cnt = Counter(w for a in base for w in (a.get("_w") or words(a["title"])))
    cands = []
    for w, arts in r_art.items():
        if len(r_out[w]) < 2 or w.isdigit() or w in RISE_STOP or len(w) < 2:
            continue
        expected = b_cnt.get(w, 0) / base_h * recent_h
        ratio = (len(arts) + 1) / (expected + 1)
        if ratio < 1.8:
            continue
        cands.append((len(r_out[w]) * ratio, w, arts))
    cands.sort(key=lambda x: -x[0])
    out, used = [], set()
    for _, w, arts in cands:
        if any(w in u or u in w for u in used):
            continue
        used.add(w)
        news, seen = [], set()
        for a in sorted(arts, key=lambda a: a["date"], reverse=True):
            if a["outlet"] in seen:
                continue
            seen.add(a["outlet"])
            news.append({"title": a["title"], "url": a["url"], "outlet": a["outlet"]})
            if len(news) >= 3:
                break
        out.append({"keyword": w, "traffic": f"3시간 {len(arts)}건·{len(r_out[w])}곳", "news": news, "kind": "rise"})
        if len(out) >= top:
            break
    return out


def channel_trends(c, sub, trends, now, top=10):
    """탭별 급상승: 전체는 구글 트렌드, 분야 탭은 그 분야 기사에 나오는 구글 검색어 + 분야 기사 속 급상승 낱말"""
    if c == "전체":
        return trends
    blob = "\n".join(a["title"].lower() for a in sub)
    matched = []
    for t in trends:
        kw = t["keyword"].lower()
        in_titles = len(kw) >= 2 and blob.count(kw) >= 2  # 그 분야 기사 2건 이상에 나와야 함
        stock_hit = c == "주식" and (bool(STOCK_RX.search(t["keyword"]))
                                     or sum(1 for n in t["news"] if STOCK_RX.search(n.get("title", ""))) >= 2)
        if in_titles or stock_hit:
            matched.append(dict(t))
    names = {t["keyword"].lower() for t in matched}
    rise = [r for r in rising_words(sub, now, top=top + 5) if not any(r["keyword"] in n or n in r["keyword"] for n in names)]
    if c in NICHE:  # 파주 탭에서 '파주'처럼 탭 주제 낱말 자체는 뺀다
        matched = [t for t in matched if not NICHE[c].search(t["keyword"])]
        rise = [r for r in rise if not NICHE[c].search(r["keyword"])]
    out = (matched + rise)[:top]
    return trend_related(out, sub)


def hourly(items, now, hours=24):
    """최근 N시간을 1시간 단위로 센 새 기사 수 [(시각ISO, 건수), ...]"""
    base = now.replace(minute=0, second=0, microsecond=0)
    slots = [base - timedelta(hours=h) for h in range(hours - 1, -1, -1)]
    cnt = Counter(a["date"].replace(minute=0, second=0, microsecond=0) for a in items)
    return [(h.isoformat(), cnt.get(h, 0)) for h in slots]


def photos(clusters, items, n=8):
    """사진 섹션: 사진 있는 사건 우선, 모자라면 사진 있는 최신 기사로 채움"""
    out, seen = [], set()
    if clusters and clusters[0].get("img"):  # 1위 사건은 위에 큰 사진으로 이미 나온다
        seen.add(clusters[0]["img"])
        seen_urls = {l["url"] for l in clusters[0]["links"]}
    else:
        seen_urls = set()
    for c in clusters[1:] if clusters and clusters[0].get("img") else clusters:
        if c.get("img") and c["img"] not in seen:
            seen.add(c["img"])
            out.append({"title": c["title"], "url": c["links"][0]["url"], "img": c["img"],
                        "meta": f'{c["count"]}개 언론사'})
    for a in sorted(items, key=lambda a: a["date"], reverse=True):
        if len(out) >= n:
            break
        if a.get("img") and a["img"] not in seen and a["url"] not in seen_urls:
            seen.add(a["img"])
            out.append({"title": a["title"], "url": a["url"], "img": a["img"], "meta": a["outlet"]})
    return out[:n]


def latest(items, n=8):
    return [{"title": a["title"], "url": a["url"], "outlet": a["outlet"], "date": a["date"].isoformat()}
            for a in sorted(items, key=lambda a: a["date"], reverse=True)[:n]]


def attach_news(trends, items):
    for t in trends:
        if len(t["news"]) >= 3:
            continue
        kw = t["keyword"].lower()
        for a in items:
            if kw in a["title"].lower() and len(t["news"]) < 3:
                t["news"].append({"title": a["title"], "url": a["url"], "outlet": a["outlet"]})
    return trends


# ---------------- 샘플 데이터 ----------------
def demo_graphic(now):
    def svg(col, i):
        bars = "".join(f"<rect x='{60 + k * 60}' y='{230 - h}' width='34' height='{h}' fill='#fff' fill-opacity='.7'/>"
                       for k, h in enumerate([60, 110, 80, 150, 120]))
        return "data:image/svg+xml," + urllib.parse.quote(
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 300'><rect width='400' height='300' fill='{col}'/>{bars}</svg>")
    t = ["맞벌이 부부 가사·돌봄 시간", "안세영 세계 배드민턴 여자 단식 최다 우승", "중국 경제성장률 추이", "서울 고가도로 전수조사 결과",
         "미국 이민 비자 발급 감소율", "탄핵소추 절차 한눈에"]
    cols = ["#9fb7c7", "#c7a99f", "#a9c79f", "#c7bf9f", "#b39fc7", "#9fc7bd"]
    yna = [{"title": x, "url": f"https://example.com/g{i}", "img": svg(cols[i], i), "outlet": "연합뉴스",
            "date": (now - timedelta(minutes=40 + i * 70)).isoformat()} for i, x in enumerate(t)]
    others = [{"title": f"[그래픽뉴스] {x}", "url": f"https://example.com/o{i}", "outlet": o,
               "img": svg(cols[(i + 2) % 6], i) if o == "뉴시스" else None,
               "date": (now - timedelta(minutes=30 + i * 55)).isoformat()}
              for i, (x, o) in enumerate([("가계대출 증가 추이", "뉴시스"), ("수도권 아파트 매매가격 변동률", "뉴시스"),
                                          ("연령대별 1인 가구 비율", "뉴스1"), ("월별 수출입 동향", "아시아경제"),
                                          ("지역별 첫 서리 관측일", "뉴시스")])]
    return yna, others


def demo_data(now):
    def A(outlet, cat, title, mins, i):
        return {"outlet": outlet, "category": cat, "title": title, "url": f"https://example.com/{i}",
                "date": now - timedelta(minutes=mins)}
    rows = [
        ("연합뉴스", "정치", "정부, 내년도 최저임금 인상률 확정 발표", 20), ("한겨레", "정치", "내년 최저임금 인상률 확정 정부 발표", 25),
        ("조선일보", "경제", "정부 내년 최저임금 확정 인상률 발표 소상공인 반발", 30), ("SBS", "경제", "최저임금 인상률 확정 정부 발표", 22),
        ("한국경제", "경제", "코스피 장중 사상 최고치 경신 외국인 순매수", 15), ("매일경제", "경제", "코스피 사상 최고치 경신 외국인 순매수 지속", 18),
        ("JTBC", "경제", "코스피 사상 최고치 외국인 순매수", 40), ("세계일보", "경제", "코스피 최고치 경신 외국인 순매수 행진", 55),
        ("동아일보", "사회", "서울 아침 기온 영하권 첫 한파주의보", 35), ("경향신문", "사회", "서울 첫 한파주의보 아침 영하권 기온", 50),
        ("노컷뉴스", "사회", "전국 첫 한파주의보 아침 기온 영하권", 70), ("국민일보", "사회", "첫 한파주의보 서울 아침 기온 영하권", 90),
        ("오마이뉴스", "스포츠", "프로야구 한국시리즈 1차전 우승 후보 격돌", 60), ("연합뉴스", "스포츠", "한국시리즈 1차전 프로야구 우승 후보 격돌", 65),
        ("SBS", "스포츠", "한국시리즈 1차전 격돌 프로야구 우승 후보", 80),
        ("조선일보", "정치", "국회 예산안 심사 착수 여야 격돌 예고", 100), ("한겨레", "정치", "여야 국회 예산안 심사 착수 격돌", 110),
        ("동아일보", "정치", "국회 예산안 심사 시작 여야 쟁점 격돌", 120),
        ("노컷뉴스", "연예", "가수 A 신곡 음원차트 1위 데뷔 첫 기록", 45), ("한국경제", "연예", "가수 A 신곡 음원차트 1위 첫 기록", 48),
        ("세계일보", "연예", "A 신곡 음원차트 1위 데뷔 후 첫 기록", 52),
        ("국민일보", "사회", "수도권 지하철 파업 예고 출근길 비상", 130), ("JTBC", "사회", "지하철 파업 예고 수도권 출근길 비상", 140),
        ("매일경제", "경제", "환율 1400원 재돌파 외환시장 긴장", 160), ("한국경제", "경제", "원달러 환율 1400원 재돌파 긴장", 170),
        ("오마이뉴스", "정치", "지방선거 앞두고 공천 갈등 격화", 200), ("경향신문", "정치", "공천 갈등 격화 지방선거 앞둔 여야", 210),
        ("연합뉴스", "스포츠", "손흥민 시즌 10호골 팀 승리 견인", 240), ("조선일보", "스포츠", "손흥민 시즌 10호골 승리 견인", 250),
    ]
    items = [A(o, c, t, m, i) for i, (o, c, t, m) in enumerate(rows)]
    hues = ["#c9b79c", "#9fb7b0", "#b9a3a0", "#a7b39a", "#b0aac6", "#c4b08a", "#9eaec2", "#bfa6b6"]
    for i, a in enumerate(items):
        if i % 2 == 0:
            col = hues[i % len(hues)]
            svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 300'><rect width='400' height='300' fill='{col}'/>"
                   f"<circle cx='{120 + i * 9 % 160}' cy='130' r='70' fill='#fff' fill-opacity='.35'/>"
                   f"<rect x='0' y='220' width='400' height='80' fill='#000' fill-opacity='.08'/></svg>")
            a["img"] = "data:image/svg+xml," + urllib.parse.quote(svg)
    trends = [{"keyword": "한국시리즈", "traffic": "50,000+", "news": [], "start": (now - timedelta(minutes=95)).isoformat()}, {"keyword": "최저임금", "traffic": "20,000+", "news": [], "start": (now - timedelta(minutes=95)).isoformat()},
              {"keyword": "한파주의보", "traffic": "10,000+", "news": [], "start": (now - timedelta(minutes=95)).isoformat()}, {"keyword": "손흥민", "traffic": "10,000+", "news": [], "start": (now - timedelta(minutes=95)).isoformat()},
              {"keyword": "코스피", "traffic": "5,000+", "news": [], "start": (now - timedelta(minutes=95)).isoformat()}, {"keyword": "지하철 파업", "traffic": "2,000+", "news": [], "start": (now - timedelta(minutes=95)).isoformat()},
              {"keyword": "환율", "traffic": "2,000+", "news": [], "start": (now - timedelta(minutes=95)).isoformat()}]
    portals = {"언론사별 많이 본 뉴스": [{"title": t, "url": f"https://example.com/z{i}"} for i, t in enumerate(
        ["최저임금 확정에 소상공인 반발 확산", "코스피 사상 최고치 경신, 외국인 순매수", "서울 첫 한파주의보 발효",
         "수도권 지하철 파업 예고에 출근길 비상", "가수 A 신곡 음원차트 1위 데뷔"])],
        "줌 이슈 키워드": [{"title": t, "url": f"https://example.com/q{i}"} for i, t in enumerate(
        ["손흥민", "한국시리즈", "환율", "예산안", "공천", "한파"])]}
    feeds_status = [{"name": "샘플 데이터", "ok": True, "count": len(items), "error": ""}]
    portal_status = [{"name": "언론사별 많이 본 뉴스", "ok": True, "count": 5, "error": ""}, {"name": "줌 이슈 키워드", "ok": True, "count": 6, "error": ""},
                     {"name": "언론사별 많이 본 뉴스", "ok": True, "count": 5, "error": ""}, {"name": "줌 이슈 키워드", "ok": True, "count": 6, "error": ""}]
    return items, trends, portals, feeds_status, portal_status


# ---------------- HTML ----------------
CSS = """
:root{--ink:#1c1c1e;--ink-soft:#55524a;--ink-body:#2b2926;--paper:#f6f5f1;--paper-2:#efede7;--paper-3:#fffefb;
--rule:#d8d4c8;--rule-dot:#b8b2a0;--muted:#77735f;--dim:#a9a496;--red:#e0523c;--teal:#0f766e;--teal-tint:#e3f3f0;
--sans:"Pretendard","Apple SD Gothic Neo","Malgun Gothic","Segoe UI",system-ui,sans-serif;--brand:"Noto Serif KR","Batang",serif}
@media (prefers-color-scheme:dark){:root{--ink:#ececf1;--ink-soft:#c9c9d2;--ink-body:#d9d9e0;--paper:#101014;--paper-2:#16161c;--paper-3:#1b1b22;
--rule:#33333d;--rule-dot:#4a4a55;--muted:#8e8e98;--dim:#6e6e78;--red:#f0705c;--teal:#5eead4;--teal-tint:#123a36}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 var(--sans);word-break:keep-all;overflow-wrap:anywhere}
a{color:inherit;text-decoration:none}a:hover{text-decoration:underline;text-underline-offset:3px}
.w{max-width:1180px;margin:0 auto;padding:0 22px 64px}
.dateline{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:18px 0 9px;border-bottom:1px solid var(--ink);
 font-size:11.5px;letter-spacing:.12em;color:var(--muted)}
.dl-r{display:flex;gap:8px;align-items:center;flex:none}.rf.busy{opacity:.5;pointer-events:none}.rf.new{border-color:var(--red);color:var(--red)}
.pill{border:1px solid var(--rule);border-radius:999px;padding:4px 12px;font-size:11.5px;letter-spacing:.08em;color:var(--ink-soft);background:none;cursor:pointer;font-family:inherit}
.titlerow{text-align:center;padding:22px 0 18px;border-bottom:1px solid var(--ink)}
.brand{display:inline-flex;align-items:center;gap:.28em;font:400 clamp(40px,7.5vw,64px)/1.1 "Do Hyeon",var(--sans);letter-spacing:.02em;margin:0}.tr .nv{font-size:13px;margin:6px 0 2px;color:var(--ink-body);line-height:1.5}.tr .nv .nvt{display:inline-block;font-size:10.5px;font-weight:800;color:#fff;background:#03a94f;border-radius:3px;padding:0 5px;margin-right:6px;vertical-align:1px}.tr .nv b{font-size:11.5px;color:var(--red);margin-right:6px}.tr .nv .o{margin-left:6px;color:var(--muted);font-size:11.5px}
.brand .mark{height:.74em;width:auto;flex:none;transform:translateY(-.04em)}
.motto{margin:6px 0 0;font-size:12px;letter-spacing:.2em;color:var(--muted)}
.nav{display:flex;justify-content:center;flex-wrap:wrap;gap:6px 30px;padding:14px 0 12px;border-bottom:1px solid var(--ink);position:sticky;top:0;background:var(--paper);z-index:3}
.nav button{all:unset;cursor:pointer;font-size:16px;color:var(--ink-soft);padding:2px 1px 5px;border-bottom:2px solid transparent}
.nav button{white-space:nowrap}
@media(max-width:560px){.nav{justify-content:flex-start;flex-wrap:nowrap;overflow-x:auto;gap:22px;scrollbar-width:none}.nav::-webkit-scrollbar{display:none}
.nav.more{-webkit-mask-image:linear-gradient(90deg,#000 78%,transparent);mask-image:linear-gradient(90deg,#000 78%,transparent)}}
.nav button.on{color:var(--ink);font-weight:800;border-bottom-color:var(--red)}
.nav button:focus-visible{outline:2px solid var(--teal);outline-offset:3px}
.ticker{display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid var(--rule);font-size:13px;color:var(--ink-body);overflow:hidden}
.tag{flex:none;background:var(--red);color:#fff;font-size:11px;font-weight:800;letter-spacing:.14em;padding:3px 9px 3px 10px;border-radius:2px}
.ticker .items{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ticker .items.pg.on{display:block}.ticker .sep{color:var(--dim);margin:0 8px}
.cols{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:0 44px;padding-top:28px}
.side{border-left:1px solid var(--rule);padding-left:22px}
@media(max-width:900px){.cols{grid-template-columns:1fr}.side{border-left:0;padding-left:0;margin-top:36px}}
.kicker{font-size:11px;font-weight:800;letter-spacing:.2em;color:var(--red)}.kicker .km{color:var(--muted);letter-spacing:.14em;margin-left:8px}
.lead h2{font-size:clamp(23px,2.9vw,31px);line-height:1.38;font-weight:900;letter-spacing:-.015em;margin:10px 0 14px;text-wrap:balance}
.lead.has-img h2{font-size:clamp(20px,2.2vw,26px);line-height:1.42}
.story h3{text-wrap:pretty}
.byline{font-size:11.5px;letter-spacing:.04em;color:var(--muted)}
.outlets{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.outlets a{font-size:12px;color:var(--ink-soft);border:1px solid var(--rule);border-radius:999px;padding:3px 11px;background:var(--paper-3)}
.outlets a:hover{border-color:var(--ink);text-decoration:none;color:var(--ink)}
.lead{padding-bottom:26px}
.lead.has-img{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:26px;align-items:start}
.lead .ph{aspect-ratio:4/3;overflow:hidden;background:var(--paper-2);border:1px solid var(--rule)}
.lead .ph img{width:100%;height:100%;object-fit:contain;display:block}
@media(max-width:700px){.lead.has-img{grid-template-columns:1fr}.lead .ph{order:-1}}
.photos{margin:4px 0 30px;border-top:1px solid var(--ink)}
.pgrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px 14px;margin-top:14px}
@media(max-width:700px){.pgrid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.pgrid a{display:block;text-decoration:none}.pgrid a:hover .pt{text-decoration:underline}
.pgrid .ph{aspect-ratio:4/3;overflow:hidden;background:var(--paper-2);border:1px solid var(--rule)}
.pgrid img{width:100%;height:100%;object-fit:contain;display:block}.pgrid a:hover .ph{border-color:var(--ink)}
.pgrid .pt{font-size:13.5px;font-weight:700;line-height:1.45;margin-top:8px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pgrid .pm{font-size:11px;color:var(--muted);margin-top:2px}
.ofilter{display:flex;flex-wrap:wrap;gap:7px;padding:14px 0 6px}
.ofilter button{font:inherit;font-size:13px;border:1px solid var(--rule);border-radius:999px;padding:4px 12px;background:var(--paper-3);color:var(--ink-soft);cursor:pointer}
.ofilter button i{font-style:normal;font-size:11px;color:var(--dim);margin-left:5px}
.ofilter button.on{background:var(--ink);border-color:var(--ink);color:var(--paper)}.ofilter button.on i{color:var(--paper);opacity:.7}
.pgrid .pm b{font-weight:700;color:var(--ink-soft)}
.noimg-list{margin-top:26px}.noimg-list h3{font-size:13px;font-weight:700;color:var(--muted);letter-spacing:.06em;margin:0;padding-bottom:6px;border-bottom:1px solid var(--rule)}
.pgrid.g3{grid-template-columns:repeat(3,minmax(0,1fr))}
.pgrid .iph{aspect-ratio:16/10;background:var(--paper-2)}.pgrid.inter .pt{font-size:14.5px}@media(max-width:700px){.pgrid.g3{grid-template-columns:repeat(2,minmax(0,1fr))}}
.pgrid .gph{aspect-ratio:1/1;background:#fff}
.pgrid .noimg{display:flex;flex-direction:column;justify-content:space-between;padding:12px;background:var(--paper-2)}
.pgrid .noimg b{font-size:10.5px;letter-spacing:.2em;color:var(--red)}.pgrid .noimg span{font:900 15px/1.3 var(--brand);color:var(--ink-soft)}
.newest.gl li{grid-template-columns:82px minmax(0,1fr)}
.nav .srclink{font-size:14px;color:var(--muted);padding:3px 0 5px;white-space:nowrap}.nav .srclink:hover{color:var(--ink)}
.nav .gtab{position:relative;padding-left:14px}.nav .gtab::before{content:"";position:absolute;left:0;top:7px;width:1px;height:13px;background:var(--rule)}
.story{display:grid;grid-template-columns:34px minmax(0,1fr);gap:0 10px;padding:18px 0;border-top:1px solid var(--rule)}
.story .no{font:900 20px/1.3 var(--brand);color:var(--dim)}.story:nth-of-type(-n+3) .no{color:var(--red)}
.story h3{font-size:17px;line-height:1.45;font-weight:800;margin:0 0 4px}
.story .outlets{margin-top:9px}.story .outlets a{font-size:11.5px;padding:2px 9px}
.story.has-th{grid-template-columns:34px 150px minmax(0,1fr);gap:0 16px}
.story.has-th>.no{grid-column:1;grid-row:1}.story.has-th>.th{grid-column:2;grid-row:1}.story.has-th>div:not(.no){grid-column:3;grid-row:1}
.story .th{display:block;aspect-ratio:4/3;background:var(--paper-2);border:1px solid var(--rule);overflow:hidden;align-self:start}
.story .th img{width:100%;height:100%;object-fit:contain;display:block}.story .th:hover{border-color:var(--ink)}
@media(max-width:560px){.story.has-th{grid-template-columns:28px 92px minmax(0,1fr);gap:0 10px}.story h3{font-size:16px}}
.cnt{color:var(--red);font-weight:800}
.shead{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1.6px solid var(--ink);padding-bottom:7px;margin:0 0 4px}
.shead h2{font-size:16px;font-weight:800;margin:0}.shead .en{font-size:10.5px;letter-spacing:.24em;color:var(--muted)}
.side section+section{margin-top:34px}.side section.pg+section.pg{margin-top:0}.side section.pg.on~section:not(.pg){margin-top:34px}
.tr{display:grid;grid-template-columns:24px minmax(0,1fr);gap:0 8px;padding:12px 0;border-bottom:1px dotted var(--rule-dot)}
.tr .no{font-weight:800;color:var(--teal);font-size:13px;padding-top:1px}
.tr .kw{font-weight:800;font-size:15px}.tr .tf.rise{color:var(--teal)}.tr .tf{font-size:11px;color:var(--red);font-weight:700;margin-left:6px;letter-spacing:.04em}
.tr ul{margin:4px 0 0;padding:0;list-style:none}.tr li{font-size:12.5px;line-height:1.55;color:var(--ink-soft);padding:2px 0}
.tr li .o,.pop .o{color:var(--dim);font-size:11px;margin-left:5px}
.pop ol,.pop ul{margin:0;padding:0;list-style:none;counter-reset:p}.pop ul li{display:block}.pop ul li::before{content:none}
.note{font-size:11px;color:var(--dim);margin:8px 0 0;letter-spacing:.02em}
.pop li{counter-increment:p;display:grid;grid-template-columns:24px minmax(0,1fr);gap:0 8px;padding:9px 0;border-bottom:1px dotted var(--rule-dot);font-size:13.5px;line-height:1.5}
.pop li::before{content:counter(p);font-weight:800;color:var(--teal);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:7px;padding-top:12px}
.chips a,.chips span{font-size:12.5px;border:1px solid var(--rule);border-radius:999px;padding:3px 11px;color:var(--ink-soft);background:var(--paper-3)}
.chips i{font-style:normal;color:var(--dim);font-size:10.5px;margin-left:5px}
.shelf{margin-top:44px;border-top:1px solid var(--ink)}
.shelf .shead{border-bottom:1px solid var(--ink);padding:12px 0 8px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--ink);border-bottom:1px solid var(--ink);margin-top:44px}
.stats div{text-align:center;padding:16px 6px}.stats div+div{border-left:1px solid var(--rule)}
.stats b{display:block;font:900 26px/1.2 var(--brand)}.stats span{font-size:11.5px;color:var(--muted);letter-spacing:.06em}
@media(max-width:560px){.stats{grid-template-columns:repeat(2,1fr)}.stats div:nth-child(3){border-left:0}.stats div:nth-child(n+3){border-top:1px solid var(--rule)}}
.pg{display:none}.pg.on{display:block}
.brief{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;padding-top:24px}
.brief.pg.on{display:grid}.brief.pg:not(.on){display:none}@media(max-width:900px){.brief.pg.on{grid-template-columns:1fr}}
.card{background:var(--paper-3);border:1px solid var(--rule);border-top:3px solid var(--teal);padding:14px 16px 15px;display:flex;flex-direction:column;gap:6px}
.card:nth-child(2){border-top-color:var(--red)}.card:nth-child(3){border-top-color:#b8862b}
.card .lab{font-size:11px;font-weight:800;letter-spacing:.12em;color:var(--teal)}.card:nth-child(2) .lab{color:var(--red)}.card:nth-child(3) .lab{color:#b8862b}
.card .lab b{font:900 11px var(--brand);color:var(--dim);margin-right:8px}
.card .t{font-size:16.5px;font-weight:800;line-height:1.45}.card .m{font-size:11.5px;color:var(--muted)}
.rel{display:flex;flex-wrap:wrap;gap:4px 10px;margin-top:5px;font-size:11.5px;color:var(--teal)}
.rel::before{content:"함께 나온 말";color:var(--dim);font-size:10.5px;letter-spacing:.06em}
.st{font-size:11px;color:var(--dim);margin-left:6px}
.timeline{margin-top:44px;border-top:1px solid var(--ink)}
.bars{display:grid;grid-template-columns:repeat(24,minmax(0,1fr));gap:3px;align-items:end;height:96px;margin-top:16px;border-bottom:1px solid var(--rule)}
.bars span{background:var(--rule-dot);border-radius:2px 2px 0 0;min-height:2px;position:relative}
.bars span.now{background:var(--red)}.bars span:hover{background:var(--teal)}
.hrs{display:grid;grid-template-columns:repeat(24,minmax(0,1fr));gap:3px;font-size:10px;color:var(--dim);text-align:center;margin-top:4px}
.newest{list-style:none;padding:0;margin:14px 0 0}.newest li{display:grid;grid-template-columns:44px minmax(0,1fr);gap:8px;padding:7px 0;border-bottom:1px dotted var(--rule-dot);font-size:13.5px;line-height:1.5}
.newest time{font-size:11.5px;color:var(--muted);padding-top:2px;font-variant-numeric:tabular-nums}.newest .o{color:var(--dim);font-size:11px;margin-left:6px}.empty{color:var(--muted);font-size:13.5px;padding:14px 0}
details.status{margin-top:28px;font-size:12px;color:var(--muted)}details.status summary{cursor:pointer;letter-spacing:.06em}
details.status ul{columns:3 220px;padding-left:18px}details.status li.bad{color:var(--red)}
footer{margin-top:40px;padding-top:14px;border-top:1px solid var(--rule);font-size:11.5px;color:var(--muted);letter-spacing:.04em;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
"""

# 로고: 네모 따옴표(2026-10-23 시안 19). 왼쪽은 글자색(다크모드 따라감), 오른쪽은 빨강
LOGO_SVG = ('<svg class="mark" viewBox="0 0 104 88" aria-hidden="true">'
            '<rect x="0" y="0" width="44" height="44" fill="currentColor"/><polygon points="22,44 44,44 30,88 8,88" fill="currentColor"/>'
            '<rect x="60" y="0" width="44" height="44" fill="var(--red)"/><polygon points="82,44 104,44 90,88 68,88" fill="var(--red)"/></svg>')
FONT_LINK = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Do+Hyeon&family=Noto+Serif+KR:wght@900&display=swap">'

JS = """
const tabs=document.querySelectorAll('.nav button'),pgs=document.querySelectorAll('.pg');
function show(c){tabs.forEach(b=>{const on=b.dataset.c===c;b.classList.toggle('on',on);b.setAttribute('aria-selected',on)});
pgs.forEach(p=>p.classList.toggle('on',p.dataset.c===c));try{localStorage.setItem('trend_tab',c)}catch(e){}
const b=[...tabs].find(x=>x.dataset.c===c),n=b&&b.parentNode;  // 좁은 화면: 고른 탭이 보이게 가운데로
if(n&&n.scrollWidth>n.clientWidth)n.scrollTo({left:b.offsetLeft-n.offsetLeft-n.clientWidth/2+b.offsetWidth/2});}
const nav=document.querySelector('.nav');  // 오른쪽에 탭이 더 있으면 끝을 흐리게
function navFade(){if(nav)nav.classList.toggle('more',nav.scrollLeft+nav.clientWidth<nav.scrollWidth-4)}
if(nav){nav.addEventListener('scroll',navFade,{passive:true});addEventListener('resize',navFade)}
tabs.forEach(b=>b.onclick=()=>show(b.dataset.c));
let c='전체';try{c=localStorage.getItem('trend_tab')||'전체'}catch(e){}
if(![...tabs].some(b=>b.dataset.c===c))c=tabs[0]?tabs[0].dataset.c:'전체';show(c);navFade();
// 새로고침: data.json의 갱신 시각이 바뀌었으면 다시 불러오기, 아니면 '최신' 표시. 열려 있는 동안 5분마다 확인.
const built=document.body.dataset.built, rf=document.getElementById('rf'), age=document.getElementById('age');
function showAge(){const m=Math.max(0,Math.round((Date.now()-Date.parse(built))/60000));
age.textContent=(m<1?'방금':m<60?m+'분 전':Math.floor(m/60)+'시간 '+(m%60)+'분 전')+(m>=90?' · 갱신 지연 중':'');
age.style.color=m>=90?'var(--red)':'';age.style.fontWeight=m>=90?'700':'';}
showAge();setInterval(showAge,60000);
async function check(manual){try{rf.classList.add('busy');
const r=await fetch('data.json?t='+Date.now(),{cache:'no-store'});const d=await r.json();
if(d.built&&d.built!==built){location.replace(location.pathname+'?t='+Date.now()+location.hash);return}
if(manual){rf.textContent='✓ 최신입니다';setTimeout(()=>rf.textContent='↻ 새로고침',2000)}}
catch(e){if(manual)location.reload()}finally{rf.classList.remove('busy')}}
rf.addEventListener('click',()=>check(true));
setInterval(()=>{if(document.visibilityState==='visible')check(false)},300000);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')check(false)});
document.querySelectorAll('#gf button').forEach(b=>b.onclick=()=>{const o=b.dataset.o;
document.querySelectorAll('#gf button').forEach(x=>x.classList.toggle('on',x===b));
gfilter(o)});
document.querySelectorAll('#if button').forEach(b=>b.onclick=()=>{const o=b.dataset.o;
document.querySelectorAll('#if button').forEach(x=>x.classList.toggle('on',x===b));
document.querySelectorAll('#ig>a').forEach(a=>a.style.display=(!o||a.dataset.o===o)?'':'none')});
function gfilter(o){window._go=o||'';document.querySelectorAll('#gg>a').forEach(a=>a.style.display=(!o||a.dataset.o===o)?'':'none');
let n=0;document.querySelectorAll('#gl li').forEach(li=>{const v=li.dataset.img==='0'&&(!o||li.dataset.o===o);li.style.display=v?'':'none';if(v)n++});
const gl=document.getElementById('gl');if(gl)gl.style.display=n?'':'none';}
function gfail(img){const a=img.closest('a');const li=[...document.querySelectorAll('#gl li')].find(l=>l.querySelector('a').href===a.href);
if(li)li.dataset.img='0';a.remove();gfilter(window._go);}
gfilter('');
document.getElementById('st')?.addEventListener('click',()=>{const d=document.querySelector('details.status');d.open=true;d.scrollIntoView({behavior:'smooth'})});
"""

WEEK = "월화수목금토일"


def esc(s):
    return html.escape(str(s or ""), quote=True)


def ago(d, now):
    m = max(0, int((now - d).total_seconds() // 60))
    if m < 60:
        return f"{m}분 전"
    if m < 1440:
        return f"{m // 60}시간 전"
    return f"{m // 1440}일 전"


def link(a, t, title=None):
    tt = f' title="{esc(title)}"' if title else ""
    return f'<a href="{esc(a)}" target="_blank" rel="noopener noreferrer"{tt}>{esc(t)}</a>'


def hm(d):
    return d.strftime("%H:%M")


def span_txt(x):
    f, l = x.get("first"), x["latest"]
    rng = f"{hm(f)}~{hm(l)}" if f and hm(f) != hm(l) else hm(l)
    return f'{x["count"]}개 언론사 · {x.get("total", x["count"])}건 · {rng} 관측'


def outlets_html(c):
    return '<div class="outlets">' + "".join(link(l["url"], l["outlet"], l["title"]) for l in c["links"]) + "</div>"


def render_clusters(cs, now, label, photos_html=""):
    if not cs:
        return (f'<p class="empty">최근 {label} 관련 기사가 아직 없습니다.</p>' if label in NICHE
                else '<p class="empty">두 곳 이상이 함께 다룬 사건이 아직 없습니다.</p>')
    c = cs[0]
    ph = (f'<a class="ph" href="{esc(c["links"][0]["url"])}" target="_blank" rel="noopener noreferrer">'
          f'<img src="{esc(c["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest(\'.lead\').classList.remove(\'has-img\');this.parentNode.remove()"></a>') if c.get("img") else ""
    lead = (f'<article class="lead{" has-img" if ph else ""}"><div><div class="kicker">{esc(label)} 1위<span class="km">{span_txt(c)}</span></div>'
            f'<h2>{link(c["links"][0]["url"], c["title"])}</h2>'
            f'<div class="byline">같은 사건을 다룬 언론사</div>{outlets_html(c)}</div>{ph}</article>')
    def thumb(x):
        if not x.get("img"):
            return ""
        return (f'<a class="th" href="{esc(x["links"][0]["url"])}" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true">'
                f'<img src="{esc(x["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" '
                f'onerror="this.closest(\'.story\').classList.remove(\'has-th\');this.parentNode.remove()"></a>')
    rest = "".join(
        f'<article class="story{" has-th" if x.get("img") else ""}"><div class="no">{i}</div><div><h3>{link(x["links"][0]["url"], x["title"])}</h3>'
        f'<div class="byline"><span class="cnt">{x["count"]}개 언론사</span> · {x.get("total", x["count"])}건 · {ago(x["latest"], now)}</div>{outlets_html(x)}</div>{thumb(x)}</article>'
        for i, x in enumerate(cs[1:], 2))
    return lead + rest  # 사진 뉴스 칸은 목록에 통합(사진 있는 사건은 썸네일)


def started(t, now):
    if not t.get("start"):
        return ""
    d = datetime.fromisoformat(t["start"]) if isinstance(t["start"], str) else t["start"]
    return f'<span class="st">시작 {ago(d, now)}</span>'


def render_trends(trends, now=None):
    if not trends:
        return '<p class="empty">급상승 검색어를 가져오지 못했습니다.</p>'
    out = []
    for i, t in enumerate(trends, 1):
        news = "".join(f"<li>{link(n['url'], n['title'])}<span class='o'>{esc(n['outlet'])}</span></li>" for n in t["news"][:2] if n.get("url"))
        tf = (f'<span class="tf{" rise" if t.get("kind") == "rise" else ""}">{esc(t["traffic"])}</span>') if t.get("traffic") else ""
        rel = ('<div class="rel">' + "".join(f"<span>{esc(w)}</span>" for w in t.get("related", [])) + "</div>") if t.get("related") else ""
        nv = ""
        if t.get("naver"):  # 네이버 뉴스 검색의 가장 최근 기사 1건 + 최근 1시간 기사 수
            x = t["naver"][0]
            when = ago(datetime.fromisoformat(x["date"]), now) if now and x.get("date") else ""
            cnt = f'<b>1시간 {t["naver_1h"]}{"+" if t.get("naver_1h", 0) >= 20 else ""}건</b>' if t.get("naver_1h") else ""
            nv = (f'<div class="nv"><span class="nvt">네이버</span>{cnt}{link(x["url"], x["title"])}'
                  f'<span class="o">{esc(x["outlet"])}{" · " + when if when else ""}</span></div>')
        out.append(f'<div class="tr"><div class="no">{i}</div><div><div class="kw">{esc(t["keyword"])}{tf}{started(t, now) if now else ""}</div><ul>{news}</ul>{nv}{rel}</div></div>')
    return "".join(out)


def render_photos(p, label):
    ps = p.get("photos") or []
    if len(ps) < 2:
        return ""
    cells = "".join(
        f'<a href="{esc(x["url"])}" target="_blank" rel="noopener noreferrer"><div class="ph">'
        f'<img src="{esc(x["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest(\'a\').remove()"></div>'
        f'<div class="pt">{esc(x["title"])}</div><div class="pm">{esc(x["meta"])}</div></a>' for x in ps)
    return (f'<section class="photos"><div class="shead" style="padding-top:12px"><h2>사진으로 보는 뉴스{"" if label == "전체" else " · " + esc(label)}</h2>'
            f'<span class="en">PHOTOS · 언론사 썸네일</span></div><div class="pgrid">{cells}</div></section>')


def render_timeline(p, now, label):
    hs = p.get("hourly") or []
    if not hs:
        return ""
    top = max(c for _, c in hs) or 1
    bars = "".join(f'<span class="{"now" if i == len(hs) - 1 else ""}" style="height:{max(2, round(c / top * 100))}%" '
                   f'title="{datetime.fromisoformat(h).strftime("%H")}시 · {c}건"></span>' for i, (h, c) in enumerate(hs))
    hrs = "".join(f'<span>{datetime.fromisoformat(h).hour if i % 3 == 0 or i == len(hs) - 1 else ""}</span>' for i, (h, c) in enumerate(hs))
    last = hs[-1][1]
    li = "".join(f'<li><time>{datetime.fromisoformat(a["date"]).strftime("%H:%M")}</time><span>{link(a["url"], a["title"])}'
                 f'<span class="o">{esc(a["outlet"])}</span></span></li>' for a in p.get("latest", []))
    return (f'<section class="timeline"><div class="shead" style="padding-top:12px"><h2>시간대별 새 기사{"" if label == "전체" else " · " + esc(label)}</h2>'
            f'<span class="en">LAST 24H · 이번 시간 {last}건</span></div><div class="bars">{bars}</div><div class="hrs">{hrs}</div>'
            f'<ul class="newest">{li}</ul></section>')


def render_graphic(g):
    """그래픽 탭: 위에 언론사 선택 버튼, 아래에 그래픽 바둑판(최신순). 버튼으로 언론사별로 거른다."""
    if not g or not (g.get("yna") or g.get("others")):
        return ""
    items = list(g.get("yna", [])) + list(g.get("others", []))
    items.sort(key=lambda x: x.get("date") or "", reverse=True)

    def when(x):
        return datetime.fromisoformat(x["date"]).strftime("%m.%d %H:%M") if x.get("date") else ""

    cnt = Counter(x["outlet"] for x in items)
    outlets = sorted(cnt, key=lambda o: (-cnt[o], o))
    chips = (f'<button class="on" data-o="">전체<i>{len(items)}</i></button>'
             + "".join(f'<button data-o="{esc(o)}">{esc(o)}<i>{cnt[o]}</i></button>' for o in outlets))

    def tile(x):
        # 사진이 깨지면 칸을 지우고 같은 기사를 아래 목록에 보이게 한다
        ph = (f'<div class="ph gph"><img src="{esc(x["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" '
              f'onerror="gfail(this)"></div>')
        t = GRAPHIC_RX.sub("", x["title"]).strip() or x["title"]
        return (f'<a data-o="{esc(x["outlet"])}" href="{esc(x["url"])}" target="_blank" rel="noopener noreferrer">{ph}'
                f'<div class="pt">{esc(t)}</div><div class="pm"><b>{esc(x["outlet"])}</b> · {when(x)}</div></a>')

    def row(x):
        t = GRAPHIC_RX.sub("", x["title"]).strip() or x["title"]
        hide = ' style="display:none"' if x.get("img") else ""
        return (f'<li data-o="{esc(x["outlet"])}" data-img="{1 if x.get("img") else 0}"{hide}><time>{when(x)}</time>'
                f'<span>{link(x["url"], t)}<span class="o">{esc(x["outlet"])}</span></span></li>')

    more = " · ".join(link(u, f"{n} 그래픽 전체 보기 →") for n, u in (("연합뉴스", YNA_GRAPHIC), ("뉴시스", NEWSIS_GRAPHIC))
                      if n in cnt)
    return (f'<div class="pg" data-c="그래픽" role="tabpanel"><section class="photos" style="margin-top:0">'
            f'<div class="shead" style="padding-top:12px"><h2>그래픽 뉴스</h2><span class="en">GRAPHICS · 최근 2일 · {len(items)}건</span></div>'
            f'<div class="ofilter" id="gf" role="group" aria-label="언론사">{chips}</div>'
            f'<div class="pgrid g3" id="gg">{"".join(tile(x) for x in items if x.get("img"))}</div>'
            f'<div class="noimg-list" id="gl"><h3>사진 없는 그래픽 기사</h3><ul class="newest gl">{"".join(row(x) for x in items)}</ul></div>'
            f'<p class="note">{more}</p></section></div>')


def render_brief(trends, pages, now, key="전체"):
    cards = []
    if trends:
        t = trends[0]
        st = ""
        if t.get("start"):
            st = " · 시작 " + ago(datetime.fromisoformat(t["start"]) if isinstance(t["start"], str) else t["start"], now)
        u = t["news"][0]["url"] if t["news"] else "#"
        cards.append(f'<div class="card"><div class="lab"><b>01</b>지금 급상승</div><div class="t">{link(u, t["keyword"])}</div>'
                     f'<div class="m">Google 검색량 {esc(t.get("traffic") or "-")}{st}</div></div>')
    p = pages.get(key, {})
    if p.get("latest"):
        a = p["latest"][0]
        last = (p.get("hourly") or [[0, 0]])[-1][1]
        cards.append(f'<div class="card"><div class="lab"><b>02</b>방금 나온 기사</div><div class="t">{link(a["url"], a["title"])}</div>'
                     f'<div class="m">{esc(a["outlet"])} · {datetime.fromisoformat(a["date"]).strftime("%H:%M")} · 이번 시간 새 기사 {last}건</div></div>')
    if p.get("clusters"):
        c = p["clusters"][0]
        cards.append(f'<div class="card"><div class="lab"><b>03</b>가장 많이 다룬 사건</div><div class="t">{link(c["links"][0]["url"], c["title"])}</div>'
                     f'<div class="m">{span_txt(c)}</div></div>')
    return f'<div class="brief pg" data-c="{esc(key)}">{"".join(cards)}</div>' if cards else ""


def render_keywords(ks):
    if not ks:
        return '<p class="empty">아직 없음</p>'
    return '<div class="chips">' + "".join(f'<span>{esc(w)}<i>{k}</i></span>' for w, k in ks) + "</div>"


def render_portals(portals):
    out = []
    for name, rows in (portals or {}).items():
        if not rows:
            continue
        if "키워드" in name:
            body = '<div class="chips">' + "".join(link(r["url"], r["title"]) for r in rows) + "</div>"
            en = "ISSUES"
        else:
            def host(u):
                h = urllib.parse.urlsplit(u).netloc.lower()
                for pre in ("www.", "news.", "biz.", "m."):
                    h = h[len(pre):] if h.startswith(pre) else h
                return h
            body = ('<div class="pop"><ul>' + "".join(
                f"<li>{link(r['url'], r['title'])}<span class='o'>{esc(host(r['url']))}</span></li>" for r in rows)
                + '</ul><p class="note">ZUM 집계 · 각 언론사에서 최근 1시간 가장 많이 본 기사</p></div>')
            en = "LAST HOUR"
        out.append(f'<section><div class="shead"><h2>{esc(name)}</h2><span class="en">{en}</span></div>{body}</section>')
    return "".join(out)


def render_page(pages, trends, portals, status, now, nitems, hours, outlets=0, graphic=None, only=None, interactive=None,
                brand="지금 뜨는 뉴스", motto=None, title=None):
    ok = sum(1 for s in status if s["ok"])
    if only:  # 단독 페이지 (예: 주식 → stock.html)
        pages = {only: pages[only]}
        graphic, portals, interactive = None, {}, None
        nitems = sum(c for _, c in pages[only].get("hourly", [])) or nitems
    gpanel = render_graphic(graphic) + render_interactive(interactive or [])
    first_niche = next((x for x in NICHE if x in pages), None)  # 관심 탭 앞에 구분선
    tabs = "".join(f'<button role="tab" data-c="{esc(c)}"' + (' class="gtab"' if c == first_niche else "") + f'>{esc(c)}</button>'
                   for c in CATEGORIES if c in pages)
    if gpanel:
        tabs += '<button role="tab" data-c="그래픽" class="gtab">그래픽</button>' if graphic else ""
        tabs += '<button role="tab" data-c="인터랙티브">인터랙티브</button>' if interactive else ""
    if only:
        tabs = f'<button role="tab" data-c="{esc(only)}" style="display:none">{esc(only)}</button><a class="srclink" href="index.html">← 전체 뉴스</a>'
    else:
        tabs += '<a class="srclink" href="sources.html">출처</a>'
    def tr_of(c):
        return pages[c].get("trends") if pages[c].get("trends") is not None else trends
    tick_keys = list(pages) + (["그래픽"] if graphic else []) + (["인터랙티브"] if interactive else [])
    def tk(c):
        src = tr_of(c if c in pages else next(iter(pages)))
        return (f'<span class="items pg" data-c="{esc(c)}">' + '<span class="sep">/</span>'.join(esc(t["keyword"]) for t in src[:6]) + "</span>")
    ticker = '<div class="ticker"><span class="tag">급상승</span>' + "".join(tk(c) for c in tick_keys) + "</div>"
    body = []
    for c, p in pages.items():
        label = "오늘의 사건" if c == "전체" else ("증시" if c == "주식" else c)
        body.append(f'<div class="pg" data-c="{esc(c)}" role="tabpanel">{render_clusters(p["clusters"], now, label, render_photos(p, c))}{render_timeline(p, now, c)}'
                    f'<section class="shelf"><div class="shead"><h2>언론사 {2 if c in NICHE else 3}곳 이상이 쓴 낱말{"" if c == "전체" else " · " + esc(c)}</h2>'
                    f'<span class="en">KEYWORDS</span></div>{render_keywords(p["keywords"])}</section></div>')
    def side_tr(c):
        src = tr_of(c if c in pages else next(iter(pages)))
        first = c == "전체" or c not in pages
        h = "급상승 검색어" if first else f"급상승 · {c}"
        en = "GOOGLE TRENDS" if first else "검색어 + 기사 속 낱말"
        note = "" if first else '<p class="note">구글 급상승 검색어 중 이 분야 기사에 나온 것, 그리고 최근 3시간 이 분야 기사에서 갑자기 많이 쓰인 낱말입니다.</p>'
        return (f'<section class="pg" data-c="{esc(c)}"><div class="shead"><h2>{esc(h)}</h2><span class="en">{en}</span></div>'
                f'{render_trends(src, now)}{note}</section>')
    side = "".join(side_tr(c) for c in tick_keys) + render_portals(portals)
    allc = (pages.get("전체") or next(iter(pages.values()), {})).get("clusters", [])
    stats = (f'<div class="stats"><div><b>{nitems:,}</b><span>분석한 기사</span></div><div><b>{outlets}</b><span>언론사</span></div>'
             f'<div><b>{len(allc)}</b><span>여러 곳이 다룬 사건</span></div><div><b>{len(trends)}</b><span>급상승 검색어</span></div></div>')
    st = "".join(f'<li class="{"" if s["ok"] else "bad"}">{esc(s["name"])}: {"%d건" % s["count"] if s["ok"] else esc(s["error"])}</li>' for s in status)
    date = f'{now.year}년 {now.month}월 {now.day}일 {WEEK[now.weekday()]}요일 · {now.strftime("%H:%M")} 기준'
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{esc(title or brand)}</title>
<link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="icon" href="favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="apple-touch-icon.png"><meta name="theme-color" content="#1c1c1e">
<link rel="preconnect" href="https://cdn.jsdelivr.net"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
{FONT_LINK}
<style>{CSS}</style></head>
<body data-built="{esc(now.isoformat())}"><div class="w">
<div class="dateline"><span>{date} · <span id="age"></span></span><span class="dl-r"><button class="pill rf" id="rf" type="button" title="새 데이터가 있으면 바로 다시 불러옵니다">↻ 새로고침</button><button class="pill" id="st" type="button">수집 {ok}/{len(status)}</button></span></div>
<header class="titlerow"><h1 class="brand">{LOGO_SVG}<span>{esc(brand)}</span></h1><p class="motto">{esc(motto or f"여러 언론사가 함께 다룬 순서 · 최근 {hours}시간")}</p></header>
<nav class="nav" role="tablist">{tabs}</nav>
{ticker}
{render_brief(trends, pages, now, only or "전체")}
<div class="cols"><main>{"".join(body)}{gpanel}</main><aside class="side">{side}</aside></div>
{stats}
<details class="status"><summary>수집 상태 · {ok}/{len(status)} 성공</summary><ul>{st}</ul></details>
<footer><span>제목과 링크만 모읍니다. 기사 원문은 각 언론사에 있습니다.</span><a href="sources.html">뉴스 출처 보기 →</a></footer>
</div><script>{JS}</script></body></html>"""


# ---------------- 출처 페이지 ----------------
HOMEPAGES = {"연합뉴스": "https://www.yna.co.kr", "한겨레": "https://www.hani.co.kr", "조선일보": "https://www.chosun.com",
             "동아일보": "https://www.donga.com", "경향신문": "https://www.khan.co.kr", "매일경제": "https://www.mk.co.kr",
             "한국경제": "https://www.hankyung.com", "SBS": "https://news.sbs.co.kr", "JTBC": "https://news.jtbc.co.kr",
             "노컷뉴스": "https://www.nocutnews.co.kr", "오마이뉴스": "https://www.ohmynews.com", "국민일보": "https://www.kmib.co.kr",
             "세계일보": "https://www.segye.com", "구글뉴스": "https://news.google.com/home?hl=ko&gl=KR&ceid=KR:ko",
             "뉴시스": "https://www.newsis.com", "줌": "https://news.zum.com",
             "KBS": "https://news.kbs.co.kr", "MBC": "https://imnews.imbc.com",
             "파이낸셜뉴스": "https://www.fnnews.com", "이데일리": "https://www.edaily.co.kr"}
EXCLUDED = [("네이트 뉴스 랭킹", "robots.txt가 모든 수집기를 차단(User-agent: * Disallow: /)", "2026-10-19"),
            ("네이버 뉴스 랭킹", "robots.txt가 모든 수집기를 차단(User-agent: * Disallow: /)", "2026-10-19"),
            ("MBC 뉴스 사이트", "robots.txt가 모든 수집기를 차단 — 구글뉴스 검색으로 MBC 기사만 가져옴", "2026-10-19"),
            ("KBS 뉴스 사이트", "공개 RSS 없음, 기사 목록 API(/api)는 robots.txt 차단 — 구글뉴스 검색으로 KBS 기사만 가져옴", "2026-10-19"),
            ("노컷뉴스 분야별 RSS", "주소 404 — 전체 RSS만 사용", "2026-10-19"),
            ("오마이뉴스 연예 RSS", "주소 404", "2026-10-19"),
            ("매일경제 스포츠 RSS", "빈 응답", "2026-10-19"),
            ("중앙일보 디지털스페셜", "robots.txt가 /digitalspecial 을 차단", "2026-10-20"),
            ("한겨레·동아일보 인터랙티브", "모아 둔 목록 페이지가 없음(404)", "2026-10-20")]


def render_sources(feeds, status, now):
    """신문사별로 어떤 분야를 어떤 방식으로 가져오는지, 이번 수집 결과와 함께 적은 페이지"""
    st = {x["name"]: x for x in status}
    by = defaultdict(list)
    for f in feeds:
        by[f["outlet"]].append(f)
    rows = []
    for outlet in sorted(by, key=lambda o: (o.startswith("구글뉴스"), not re.match(r"[가-힣]", o), o)):
        cells = []
        n_ok = 0
        for c in CATEGORIES:
            f = next((x for x in by[outlet] if x["category"] == c), None)
            if not f:
                cells.append('<td class="na">—</td>')
                continue
            x = st.get(f"{outlet}·{c}")
            if x is None:
                cells.append(f'<td>{link(f["url"], "RSS")}</td>')
            elif x["ok"]:
                n_ok += 1
                cells.append(f'<td class="ok">{link(f["url"], str(x["count"]))}</td>')
            else:
                cells.append(f'<td class="bad" title="{esc(x["error"])}">{link(f["url"], "실패")}</td>')
        home = HOMEPAGES.get(outlet)
        name = link(home, outlet) if home else esc(outlet)
        if any(x.get("via") for x in by[outlet]):
            name += f'<span class="o" style="font-weight:400;margin-left:6px;color:var(--dim);font-size:11px">{esc(next(x["via"] for x in by[outlet] if x.get("via")))}</span>'
        rows.append(f'<tr><th>{name}</th>{"".join(cells)}<td class="sum">{n_ok}/{len(by[outlet])}</td></tr>')
    head = "".join(f"<th>{esc(c)}</th>" for c in CATEGORIES)

    def stline(name):
        x = st.get(name)
        if not x:
            return '<span class="na">—</span>'
        return f'<span class="ok">{x["count"]}건</span>' if x["ok"] else f'<span class="bad">{esc(x["error"])}</span>'
    extra = [
        ("구글 트렌드", "급상승 검색어 (대한민국)", "RSS", "trends.google.com/trending/rss?geo=KR", "구글 트렌드"),
        ("줌", "언론사별 많이 본 뉴스 · 이슈 키워드", "메인 페이지", "news.zum.com", "언론사별 많이 본 뉴스"),
        ("연합뉴스", "그래픽 목록", "목록 페이지", "yna.co.kr/graphic/index", "연합뉴스 그래픽"),
        ("뉴시스", "그래픽뉴스 목록", "목록 페이지", "newsis.com/photo/gralist", "뉴시스 그래픽"),
        ("경향신문", "인터랙티브 목록(1~2쪽)", "목록 페이지", "khan.co.kr/interactive", "경향신문 인터랙티브"),
        ("한국일보", "인터랙티브 목록(최근 24건)", "목록 페이지 속 데이터", "hankookilbo.com/interactive", "한국일보 인터랙티브"),
        ("조선일보", "인터랙티브 목록(최근 12건, 날짜는 주소의 연·월)", "목록 페이지", "chosun.com/interactive", "조선일보 인터랙티브"),
        ("네이버뉴스", "급상승 검색어별 최신 기사·최근 1시간 기사 수", "공식 검색 API(키 필요)", "openapi.naver.com/v1/search/news", "네이버뉴스·급상승 검색어"),
        ("네이버뉴스", "도서관 탭: '도서관' 검색 최신 100건", "공식 검색 API(키 필요)", "openapi.naver.com/v1/search/news", "네이버뉴스·도서관(도서관)"),
        ("네이버뉴스", "도서관 탭: '작은도서관' 검색 최신 100건", "공식 검색 API(키 필요)", "openapi.naver.com/v1/search/news", "네이버뉴스·도서관(작은도서관)"),
        ("네이버뉴스", "파주 탭: '파주' 검색 최신 100건", "공식 검색 API(키 필요)", "openapi.naver.com/v1/search/news", "네이버뉴스·파주(파주)"),
        ("네이버뉴스", "파주 탭: '운정' 검색 최신 100건", "공식 검색 API(키 필요)", "openapi.naver.com/v1/search/news", "네이버뉴스·파주(운정)"),
        ("구글뉴스", "제목에 [그래픽]이 들어간 최근 2일 기사", "검색 RSS", "news.google.com/rss/search", "그래픽 검색(구글뉴스)"),
        ("각 언론사", "사진 없는 그래픽 기사의 원문 대표 사진(og:image)", "구글 링크를 원문 주소로 푼 뒤 원문 페이지", "언론사별 기사 주소", "그래픽 원문 사진"),
        ("각 언론사", "도서관·파주 탭 상위 15건의 원문 대표 사진(og:image)", "구글 링크를 원문 주소로 푼 뒤 원문 페이지", "언론사별 기사 주소", "관심 탭 원문 사진"),
    ]
    erows = "".join(f'<tr><th>{esc(a)}</th><td>{esc(b)}</td><td>{esc(c)}</td><td class="mono">{esc(d)}</td><td>{stline(e)}</td></tr>'
                    for a, b, c, d, e in extra)
    xrows = "".join(f'<tr><th>{esc(a)}</th><td>{esc(b)}</td><td>{esc(c)}</td></tr>' for a, b, c in EXCLUDED)
    n_out = len(by)
    css = CSS + """
.doc{max-width:900px;margin:0 auto}.doc h2{font-size:18px;margin:40px 0 6px}.doc p.lead2{color:var(--ink-soft);font-size:14px;margin:6px 0 16px}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:10px}
th,td{padding:9px 8px;border-bottom:1px solid var(--rule);text-align:center;white-space:nowrap}
.scroll table{min-width:600px}.t2 td:nth-child(2){white-space:normal;min-width:180px}thead th{border-bottom:1.6px solid var(--ink);font-size:12px;letter-spacing:.06em;color:var(--muted);font-weight:700}
tbody th{text-align:left;font-weight:800;white-space:nowrap}td.ok a{color:var(--teal);font-weight:700}td.bad a,span.bad{color:var(--red);font-weight:700}span.ok{color:var(--teal);font-weight:700}
td.na,span.na{color:var(--dim)}td.sum{color:var(--muted);font-variant-numeric:tabular-nums}.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--ink-soft)}
.t2 td,.t2 th{text-align:left}.scroll{overflow-x:auto}.rules li{margin:4px 0;font-size:14px;color:var(--ink-body)}
"""
    date = f'{now.year}년 {now.month}월 {now.day}일 {WEEK[now.weekday()]}요일 · {now.strftime("%H:%M")} 기준'
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>뉴스 출처 · 지금 뜨는 뉴스</title>
<link rel="icon" href="favicon.svg" type="image/svg+xml"><link rel="icon" href="favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="apple-touch-icon.png"><meta name="theme-color" content="#1c1c1e">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
{FONT_LINK}
<style>{css}</style></head><body><div class="w">
<div class="dateline"><span>{date}</span><a class="pill" href="index.html">← 첫 화면</a></div>
<header class="titlerow"><h1 class="brand" style="font-size:clamp(32px,5.5vw,48px)">{LOGO_SVG}<span>뉴스 출처</span></h1><p class="motto">어느 신문사에서 무엇을 가져오는지</p></header>
<div class="doc">
<h2>신문사 RSS · {n_out}곳</h2>
<p class="lead2">신문사가 공개한 RSS에서 기사 <b>제목·링크·발행 시각·썸네일 주소</b>만 가져옵니다. 숫자는 이번 수집에서 받은 기사 수이고, 누르면 그 RSS 주소로 갑니다.</p>
<div class="scroll"><table><thead><tr><th style="text-align:left">신문사</th>{head}<th>성공</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>

<h2>그 밖의 출처</h2>
<p class="lead2">RSS가 아닌 곳은 robots.txt가 허용하는 목록 페이지만 읽습니다.</p>
<div class="scroll"><table class="t2"><thead><tr><th>출처</th><th>가져오는 것</th><th>방식</th><th>주소</th><th>이번 수집</th></tr></thead><tbody>{erows}</tbody></table></div>

<h2>가져오지 않는 곳</h2>
<div class="scroll"><table class="t2"><thead><tr><th>대상</th><th>이유</th><th>확인일</th></tr></thead><tbody>{xrows}</tbody></table></div>

<h2>원칙</h2>
<ul class="rules"><li>기사 본문과 이미지 파일은 저장하지 않습니다. 사진은 신문사 서버의 썸네일 주소를 그대로 띄웁니다.</li>
<li>모든 제목은 원문 기사로 연결됩니다.</li><li>robots.txt가 막은 페이지는 읽지 않습니다. 단, <b>구글뉴스는 예외</b>로 RSS 구독과 원문 주소 확인에 씁니다(2026-10-19 결정). 원문 언론사 페이지는 각 사이트의 robots.txt를 지킵니다.</li><li>1시간마다 다시 모읍니다.</li></ul>
</div>
<footer><span>제목과 링크만 모읍니다. 기사 원문은 각 언론사에 있습니다.</span><a href="index.html">지금 뜨는 뉴스 →</a></footer>
</div></body></html>"""


# ---------------- 알림·이력 ----------------
def telegram(msg):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10)
    except Exception as e:
        print("telegram 실패:", e, file=sys.stderr)


def copy_static(out):
    """static/ (파비콘 등)을 출력 폴더로 복사"""
    import shutil
    src = os.path.join(HERE, "static")
    if os.path.isdir(src):
        shutil.copytree(src, out, dirs_exist_ok=True)


def load_feeds():
    fp = os.path.join(HERE, "feeds.json")
    try:
        return json.load(open(fp, encoding="utf-8"))
    except Exception:
        return []


def save_history(data, now, keep_days=7):
    hd = os.path.join(HERE, "history")
    os.makedirs(hd, exist_ok=True)
    with open(os.path.join(hd, now.strftime("%Y-%m-%d-%H.json")), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y-%m-%d-%H")
    for fn in os.listdir(hd):
        if fn.endswith(".json") and fn[:-5] < cutoff:
            os.remove(os.path.join(hd, fn))


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--hours", type=int, default=36)
    ap.add_argument("--no-portal", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="수집 없이 site/data.json으로 화면만 다시 생성")
    ap.add_argument("--out", default=os.path.join(HERE, "site"))
    args = ap.parse_args()
    now = datetime.now(KST)
    t0 = time.time()

    if args.rebuild:
        d = json.load(open(os.path.join(args.out, "data.json"), encoding="utf-8"))
        built = datetime.fromisoformat(d["built"])
        pages = {c: dict(p, clusters=[dict(x, latest=datetime.fromisoformat(x["latest"]),
                                           first=datetime.fromisoformat(x.get("first") or x["latest"])) for x in p["clusters"]],
                         keywords=[k for k in p["keywords"] if not str(k[0]).isdigit()]) for c, p in d["pages"].items()}
        outlets = d.get("outlets") or len({l["outlet"] for p in pages.values() for c in p["clusters"] for l in c["links"]})
        page = render_page(pages, d["trends"], d.get("portals", {}), d["feeds"], built, d["articles"], d.get("hours", 36), outlets, d.get("graphic"),
                           interactive=d.get("interactive"))
        with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
            f.write(page)
        with open(os.path.join(args.out, "sources.html"), "w", encoding="utf-8") as f:
            f.write(render_sources(load_feeds(), d["feeds"], built))
        if "주식" in pages:
            with open(os.path.join(args.out, "stock.html"), "w", encoding="utf-8") as f:
                f.write(render_page(pages, stock_trends(d["trends"]), {}, d["feeds"], built, d["articles"], d.get("hours", 36), 0, None,
                                    only="주식", brand="주식 뉴스", title="주식 뉴스 · 지금 뜨는 뉴스",
                                    motto=f'증권 기사와 증시 낱말이 들어간 기사 · 여러 언론사가 함께 다룬 순서 · 최근 {d.get("hours", 36)}시간'))
        copy_static(args.out)
        print("화면만 다시 생성:", os.path.join(args.out, "index.html"))
        if args.open:
            import webbrowser
            webbrowser.open("file://" + os.path.abspath(os.path.join(args.out, "index.html")))
        return

    if args.demo:
        items, trends, portals, status, pstatus = demo_data(now)
        yna_g, g_search = demo_graphic(now)
        inter = [dict(g, outlet=("경향신문", "한국일보", "조선일보")[i % 3], title=g["title"] + " (인터랙티브 샘플)")
                 for i, g in enumerate(yna_g[:6])]
        nis_g = []
    else:
        fp = os.path.join(HERE, "feeds.json")
        feeds = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else []
        if not feeds:
            sys.exit("feeds.json이 없습니다.")
        items, status = [], []
        load_domains()
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(fetch_feed, feeds))
        for feed, its, err in results:
            name = f'{feed["outlet"]}·{feed["category"]}'
            status.append({"name": name, "ok": err is None and bool(its), "count": len(its), "error": err or ("0건" if not its else "")})
            items += its
        # 연합뉴스 RSS는 2026-10 하순부터 해외(GitHub 서버)에서 자주 응답이 없다(국내에선 정상, robots 허용).
        # 실패한 분야만 구글뉴스 검색(site:yna.co.kr)으로 대신 가져온다.
        yna_bad = {x["name"].split("·", 1)[1]: x for x in status if x["name"].startswith("연합뉴스·") and not x["ok"]}
        if yna_bad:
            for x in yna_bad.values():
                x["error"] = "실패 → 구글뉴스로 대체 (" + x["error"][:40] + ")"
            for cat, q in YNA_FALLBACK:
                if cat not in yna_bad:
                    continue
                feed = {"outlet": "연합뉴스", "category": cat, "url": gnews_search(q), "via": "구글뉴스 검색(대체)"}
                _, its, err = fetch_feed(feed)
                status.append({"name": f"연합뉴스·{cat}(구글뉴스 대체)", "ok": err is None and bool(its), "count": len(its),
                               "error": err or ("0건" if not its else "")})
                items += its
        if naver_keys():  # 네이버 뉴스 검색 API: 도서관·파주 탭 보강
            nv_items, nv_status = naver_niche()
            items += nv_items
            status += nv_status
        trends, terr = fetch_trends()
        if trends and naver_keys():
            n_ok = naver_trends(trends, now)
            status.append({"name": "네이버뉴스·급상승 검색어", "ok": n_ok > 0, "count": n_ok, "error": "" if n_ok else "검색 실패"})
        status.append({"name": "구글 트렌드", "ok": terr is None, "count": len(trends), "error": terr or ""})
        portals, pstatus = {}, []
        if not args.no_portal:
            for name, rows, err in fetch_zum():
                portals[name] = rows
                pstatus.append({"name": name, "ok": err is None, "count": len(rows), "error": err or ""})
        yna_g, gerr = fetch_yna_graphic()
        pstatus.append({"name": "연합뉴스 그래픽", "ok": gerr is None, "count": len(yna_g), "error": gerr or ""})
        nis_g, nerr = fetch_newsis_graphic()
        pstatus.append({"name": "뉴시스 그래픽", "ok": nerr is None, "count": len(nis_g), "error": nerr or ""})
        inter = []
        for nm, fn in (("경향신문", fetch_khan_interactive), ("한국일보", fetch_hankook_interactive),
                       ("조선일보", fetch_chosun_interactive)):
            rows, ierr = fn()
            pstatus.append({"name": f"{nm} 인터랙티브", "ok": ierr is None, "count": len(rows), "error": ierr or ""})
            inter += rows
        g_search, serr = fetch_graphic_search()
        pstatus.append({"name": "그래픽 검색(구글뉴스)", "ok": serr is None, "count": len(g_search), "error": serr or ""})
        ok_feeds = sum(1 for s in status if s["ok"])
        if ok_feeds == 0:
            telegram("[trend_site] 모든 피드 수집 실패")
            sys.exit("모든 피드 수집 실패")
        if ok_feeds / len(status) < 0.5:
            telegram(f"[trend_site] 피드 성공률 저조 {ok_feeds}/{len(status)}")

    raw = len(items)
    items = clean(items, args.hours, now)
    # 분야별 묶기: 전체 탭은 모든 기사, 분야 탭은 해당 분야 피드의 기사만
    pages, subs = {}, {}
    for c in CATEGORIES:
        niche_only = lambda a: a.get("cats", {a["category"]}) <= set(NICHE)  # 관심 탭 검색으로만 들어온 기사
        if c == "전체":
            sub = [a for a in items if not niche_only(a)]
        elif c == "주식":
            sub = [a for a in items if is_stock(a) and not niche_only(a)]
        elif c in NICHE:  # 그 탭 검색으로 온 기사 중 제목에 낱말이 있는 것 + 다른 피드 기사 중 제목에 낱말이 있는 것
            sub = [a for a in items if NICHE[c].search(a["title"])]
        elif c == "경제":  # 주식 기사는 주식 탭으로만 (두 탭이 겹치지 않게)
            sub = [a for a in items if "경제" in a.get("cats", {a["category"]}) and not is_stock(a)]
        else:
            sub = [a for a in items if c in a.get("cats", {a["category"]})]
        if not sub and c != "전체":
            continue
        cs = cluster(sub, min_outlets=1 if c in NICHE else 2)
        kws = keywords(sub, min_outlets=2 if c in NICHE else 3)
        if c in NICHE:
            kws = [k for k in kws if not NICHE[c].search(k[0])]
        pages[c] = {"clusters": cs, "keywords": kws, "hourly": hourly(sub, now), "latest": latest(sub),
                    "photos": photos(cs, sub)}
        subs[c] = sub
    if not args.demo:  # 관심 탭 상위 15건: 구글뉴스 링크를 원문으로 풀고 원문 대표 사진을 붙인다(그래픽 탭과 같은 방식, 캐시)
        rows = [{"url": cl["links"][0]["url"], "img": None, "_c": cl} for c in NICHE if c in pages
                for cl in pages[c]["clusters"][:15] if not cl.get("img") and GN_RX.search(cl["links"][0]["url"])]
        n_todo, n_img = enrich_graphics(rows)
        for r in rows:
            if r["img"]:
                r["_c"]["img"] = r["img"]
            if not GN_RX.search(r["url"]):
                r["_c"]["links"][0]["url"] = r["url"]
        pstatus.append({"name": "관심 탭 원문 사진", "ok": True, "count": n_img, "error": "" if n_img or not n_todo else f"{n_todo}건 중 0"})
    trends = trend_related(attach_news(trends, items), items)
    for c in pages:
        pages[c]["trends"] = channel_trends(c, subs[c], trends, now)
    graphic = {"yna": yna_g, "others": graphic_section(yna_g, g_search, items, extra=nis_g)}
    if not args.demo:
        n_todo, n_img = enrich_graphics(graphic["others"])
        pstatus.append({"name": "그래픽 원문 사진", "ok": True, "count": n_img, "error": "" if n_img else f"{n_todo}건 중 0"})

    os.makedirs(args.out, exist_ok=True)
    allstatus = status + pstatus
    page = render_page(pages, trends, portals, allstatus, now, len(items), args.hours, len({a['outlet'] for a in items}), graphic,
                       interactive=inter)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(args.out, "sources.html"), "w", encoding="utf-8") as f:
        f.write(render_sources(load_feeds(), allstatus, now))
    if "주식" in pages:
        with open(os.path.join(args.out, "stock.html"), "w", encoding="utf-8") as f:
            f.write(render_page(pages, pages["주식"].get("trends") or stock_trends(trends), {}, allstatus, now, len(items), args.hours, 0, None,
                                only="주식", brand="주식 뉴스", title="주식 뉴스 · 지금 뜨는 뉴스",
                                motto=f"증권 기사와 증시 낱말이 들어간 기사 · 여러 언론사가 함께 다룬 순서 · 최근 {args.hours}시간"))
    copy_static(args.out)
    data = {"built": now.isoformat(), "hours": args.hours, "articles": len(items), "raw": raw, "outlets": len({a["outlet"] for a in items}),
            "trends": trends,
            "pages": {c: dict(p, clusters=[dict(x, latest=x["latest"].isoformat(), first=x["first"].isoformat()) for x in p["clusters"]])
                      for c, p in pages.items()},
            "portals": portals, "graphic": graphic, "interactive": inter, "feeds": allstatus}
    with open(os.path.join(args.out, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    if not args.demo:
        save_history(data, now)
        save_domains()
    ok = sum(1 for s in allstatus if s["ok"])
    nimg = sum(1 for a in items if a.get("img"))
    print(f"완료 {time.time() - t0:.1f}s · 기사 {raw}→{len(items)}건(사진 {nimg}) · 피드 {ok}/{len(allstatus)} · 사건 {len(pages['전체']['clusters'])} · 검색어 {len(trends)}")
    for s in allstatus:
        if not s["ok"]:
            print("  실패:", s["name"], s["error"])
    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(os.path.join(args.out, "index.html")))


if __name__ == "__main__":
    main()

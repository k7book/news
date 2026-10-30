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
import argparse, hashlib, html, json, os, re, sys, time, urllib.request, urllib.parse, urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trend_site/0.2 (personal)"
TIMEOUT = 15
CATEGORIES = ["전체", "정치", "경제", "주식", "사회", "스포츠", "연예", "파주"]
# 관심 분야 탭: 구글뉴스 검색으로 모은 기사 + 수집한 모든 기사 중 제목에 해당 낱말이 있는 것.
# 다룬 곳이 적어서 한 언론사만 쓴 기사도 보여 준다. 이 탭에서만 쓰는 기사는 전체·주식 탭에 넣지 않는다.
NICHE = {
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


_IMG_RX = re.compile(r'<img[^>]+src=(?:["\']([^"\']+)["\']|([^\s>"\']+))', re.I)  # 따옴표 없는 src(한겨레)도


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
                return m.group(1) or m.group(2)
    return None


def item_desc(it, n=220):
    """item 요약: description/content:encoded/summary 의 글자만 (사진 표 등 태그는 버린다)"""
    for el in it.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag in ("description", "summary", "encoded") and el.text:
            t = re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(el.text)))
            t = re.sub(r"\s+", " ", t).strip()
            if len(t) >= 20:
                return t[:n] + ("…" if len(t) > n else "")
    return ""


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
        items.append({"title": text(it, "title"), "url": text(it, "link") or (it.find("guid").text if it.find("guid") is not None else ""),
                      "date": item_date(it), "img": item_image(it), "desc": item_desc(it) if not feed.get("by_source") else "",
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


HANGUL = re.compile(r"[가-힣]")


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
        # 한국어만: 구글 KR 피드에 외국 검색어(天気, lyon, บาร์ซ่า…)가 섞여 들어온다(2026-10-26).
        # 검색어에 한글이 있거나, 영문 검색어라도 함께 온 기사 제목이 한글이면(예: KT, BTS) 남긴다. 기사도 한글 제목만.
        ko_news = [n for n in news if HANGUL.search(n["title"] or "")]
        if not HANGUL.search(kw) and not ko_news:
            continue
        news = ko_news or news
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


# 다음뉴스(news.daum.net): robots.txt 없음(404) = 제한 없음 (2026-10-24 확인). 첫 화면의 '실시간 트렌드'와 '이 시각 주요뉴스'만.
DAUM_URL = "https://news.daum.net/"
DAUM_TREND, DAUM_MAIN = "다음 실시간 트렌드", "다음 이 시각 주요뉴스"


def fetch_daum():
    """다음뉴스 첫 화면 → [(이름, rows, 오류)]: 실시간 트렌드(검색어 10), 이 시각 주요뉴스(기사 10)"""
    names = (DAUM_TREND, DAUM_MAIN)
    try:
        if not robots_allows(DAUM_URL):
            return [(n, [], "robots.txt 차단") for n in names]
        body = http_get(DAUM_URL).decode("utf-8", "ignore")
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:60]}"
        return [(n, [], err) for n in names]
    # 1) 실시간 트렌드: <ol class="list_trendranking"> 안의 검색 링크
    trend, seen = [], set()
    i = body.find("list_trendranking")
    chunk = body[i:body.find("</ol>", i)] if i >= 0 else ""
    for li in re.findall(r"<li\b[^>]*>(.*?)</li>", chunk, re.S):
        m = re.search(r'<a href="([^"]+)"[^>]*data-tiara-copy="([^"]*)"', li)
        if not m:
            continue
        kw = html.unescape(m.group(2)).strip()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        badge = "신규" if "ico_new" in li else ("상승" if "ico_up" in li else "")
        trend.append({"title": kw, "url": html.unescape(m.group(1)), "badge": badge})
    # 2) 이 시각 주요뉴스: <ul class="list_newsheadline2"> 의 기사(제목·언론사·사진)
    main, seen = [], set()
    j = body.find("list_newsheadline2")
    chunk = body[j:body.find("</ul>", j)] if j >= 0 else ""
    for li in re.findall(r"<li\b[^>]*>(.*?)</li>", chunk, re.S):
        m = re.search(r'href="(https://v\.daum\.net/v/\d+)"', li)
        t = re.search(r'<strong class="tit_txt">(.*?)</strong>', li, re.S)
        if not m or not t or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        o = re.search(r'<span class="txt_info">(.*?)</span>', li, re.S)
        im = re.search(r'srcset="([^"\s]+)', li)
        img = html.unescape(im.group(1)) if im else None
        if img and img.startswith("//"):
            img = "https:" + img
        main.append({"title": _strip_tags(t.group(1)), "url": m.group(1), "outlet": _strip_tags(o.group(1)) if o else "", "img": img})
        if len(main) >= 10:
            break
    night = 1 <= datetime.now(KST).hour < 6
    return [(names[0], trend[:10], None if trend else ("새벽(01~06시)에는 다음이 제한적으로만 제공" if night else "실시간 트렌드를 찾지 못함(페이지 구조 변경?)")),
            (names[1], main, None if main else "주요뉴스를 찾지 못함(페이지 구조 변경?)")]


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


# ---------------- 책책책: 신문사 책 코너 (2026-10-24) ----------------
# 책 전용 RSS가 있는 곳은 그대로, 문화 RSS에 섞인 곳은 책 섹션 주소나 '[신간]' 같은 제목으로 골라낸다.
# RSS가 없는 곳(중앙·한국·문화·서울·국민·뉴시스·뉴스1 등)은 구글뉴스에서 제목에 '신간·새책'이 든 주요 언론사 기사만.
BOOK_TAB = "책책책"
BOOK_TITLE_RX = re.compile(r"\[(?:책과 ?삶|신간|새책|책|책꽂이|책의 향기|북리뷰|이 책|서평|금주의 신간|북스|책마을|.txt)[^\]]*\]|신간|새책|출간|펴냄|북토크|그림책|시집|소설집|에세이|문학상|부커상|노벨문학상")
BOOK_FEEDS = [  # (언론사, RSS, 거르는 방법: None=전부 / "title"=제목 / 그 밖=주소에 이 문자열)
    ("동아일보", "https://rss.donga.com/book.xml", None),
    ("조선일보", "https://www.chosun.com/arc/outboundfeeds/rss/category/culture-life/?outputType=xml", "/culture-life/book/"),
    ("한겨레", "https://www.hani.co.kr/rss/culture/", "/arti/culture/book/"),
    ("경향신문", "https://www.khan.co.kr/rss/rssdata/culture_news.xml", "title"),
    ("연합뉴스", "https://www.yna.co.kr/rss/culture.xml", "title"),
    ("독서신문", "https://www.readersnews.com/rss/allArticle.xml", "kw"),  # 전체 기사 RSS라 일반 문화 기사가 섞임 → 책 낱말 있는 것만
]
BOOK_KW_RX = re.compile(r"책|도서|출판|작가|저자|지음|펴냄|옮김|번역|『|《|문학|서점|도서관|신간|소설|시집|에세이|그림책|북페스티벌|독서")
BOOK_SEARCH = ["intitle:신간 when:3d", "intitle:새책 when:7d", "intitle:책꽂이 when:7d"]
BOOK_SEARCH_OUTLETS = {"중앙일보", "한국일보", "문화일보", "서울신문", "국민일보", "세계일보", "매일경제", "한국경제", "서울경제",
                       "뉴시스", "뉴스1", "파이낸셜뉴스", "이데일리", "머니투데이", "헤럴드경제", "아시아경제", "연합뉴스", "경향신문",
                       "조선일보", "동아일보", "한겨레", "독서신문", "KBS", "MBC", "SBS", "JTBC", "YTN", "노컷뉴스", "오마이뉴스"}
BOOK_HOME = {"동아일보": "https://www.donga.com/news/Section/Books", "조선일보": "https://www.chosun.com/culture-life/book/",
             "한겨레": "https://www.hani.co.kr/arti/culture/book", "경향신문": "https://www.khan.co.kr/culture/book/articles",
             "연합뉴스": "https://www.yna.co.kr/culture/books", "독서신문": "https://www.readersnews.com/",
             "중앙일보": "https://www.joongang.co.kr/culture/book", "한국일보": "https://www.hankookilbo.com/news/culture/book",
             "문화일보": "https://www.munhwa.com/culture/book", "서울신문": "https://www.seoul.co.kr/newsList/life/publication-literature/",
             "국민일보": "https://www.kmib.co.kr/list/737", "매일경제": "https://www.mk.co.kr/news/culture/book",
             "서울경제": "https://www.sedaily.com/culture/publishing", "뉴시스": "https://www.newsis.com/culture/list?cid=10700&scid=10704",
             "뉴스1": "https://www.news1.kr/life-culture/book"}


def fetch_books(now, days=7, limit=150):
    """신문사 책 코너 → (rows, status)"""
    feeds = [{"outlet": o, "category": BOOK_TAB, "url": u, "_how": how} for o, u, how in BOOK_FEEDS]
    feeds += [{"outlet": "구글뉴스", "category": BOOK_TAB, "url": gnews_search(q), "by_source": True, "_how": "search"} for q in BOOK_SEARCH]
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(fetch_feed, feeds))
    rows, status, seen = [], [], set()
    lo, hi = now - timedelta(days=days), now + timedelta(minutes=10)
    for feed, its, err in results:
        how, n = feed["_how"], 0
        for a in its:
            t = re.sub(r"\s+", " ", html.unescape(a.get("title") or "")).strip()
            if a.get("outlet") and t.endswith(" - " + a["outlet"]):
                t = t[: -len(a["outlet"]) - 3].rstrip()
            if not a.get("date"):  # 한겨레 문화 RSS는 발행 시각이 없다 → 사진 주소의 날짜(/2026/1023/)로 대신(그날 09시)
                m = re.search(r"/(20\d\d)/(\d\d)(\d\d)/", a.get("img") or "")
                if m:
                    a["date"] = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 9, tzinfo=KST)
            if not t or not a.get("date") or not (lo <= a["date"] <= hi) or not re.match(r"https?://", a.get("url") or ""):
                continue
            if how == "title" and not BOOK_TITLE_RX.search(t):
                continue
            if how == "search" and a["outlet"] not in BOOK_SEARCH_OUTLETS:
                continue
            if how == "kw" and not BOOK_KW_RX.search(t + " " + (a.get("desc") or "")):
                continue
            if how not in (None, "title", "search", "kw") and how not in a["url"]:
                continue
            if re.search(r"\[\d+화\]", t) or len(re.sub(r"\[[^\]]*\]|\W", "", t)) < 2:  # 연재소설, '[책꽂이]'처럼 제목이 빈 것
                continue
            key = (a["outlet"], re.sub(r"\W", "", t)[:40])
            if key in seen:
                continue
            seen.add(key)
            n += 1
            desc = a.get("desc") or ""
            if desc and re.sub(r"\W", "", desc)[:30] == re.sub(r"\W", "", t)[:30]:  # 제목을 되풀이만 하는 요약은 버린다
                desc = ""
            rows.append({"title": t, "url": a["url"], "outlet": a["outlet"], "img": a.get("img"), "date": a["date"].isoformat(), "desc": desc})
        name = f'{feed["outlet"]}·책' if how != "search" else f"구글뉴스 신간 검색({urllib.parse.unquote_plus(feed['url'].split('q=')[1].split('&')[0])})"
        status.append({"name": name, "ok": err is None and n > 0, "count": n, "error": err or ("0건" if not n else "")})
    rows.sort(key=lambda x: x["date"], reverse=True)
    return rows[:limit], status


# 기사 제목·요약에서 소개된 책 제목 뽑기: 『제목』, ‘제목’(출판사), ●제목(누구 지음), 저서 ‘제목’, [신간]·[새책] 뒤 제목 등
_BK = [
    re.compile(r"『([^』]{2,40})』"),
    re.compile(r"《([^》]{2,40})》"),
    re.compile(r"[‘'“\"]([^’'”\"]{2,40})[’'”\"]\s*\(([^)]{1,25})\)"),
    re.compile(r"●\s*([^(●=]{2,40}?)\s*\([^)]*지음[^)]*\)"),
    re.compile(r"(?:저서|책|신간|신작|소설|장편|단편집|에세이|그림책|시집|회고록|평전|산문집|번역서)\s*[‘'“\"]([^’'”\"]{2,40})[’'”\"]"),
]
_BK_TAG = re.compile(r"\[(?:\d+자\s*)?(?:신간|새책|책꽂이|새로 나왔어요)\]")
_BK_END = re.compile(r"(다|요|나|까|죠|…|\?|!|\.)$")
_BK_NOT = re.compile(r"(문학상|노벨상|부커상|베스트셀러)$")


def book_titles_of(x):
    t, d = x["title"], x.get("desc") or ""
    out = []
    for rx in _BK:
        for m in rx.finditer(t + " ‖ " + d):
            if rx.groups == 2 and not re.search(r"[가-힣A-Za-z]{2,}", m.group(2)):  # (64) 같은 나이 괄호는 제외
                continue
            out.append(m.group(1))
    if _BK_TAG.search(t):  # [새책] 평행우주 / 미래 식량을 설계하라 外 [신간]
        rest = re.sub(r"\s*(?:외|外)\s*$", "", _BK_TAG.sub("", t).strip())
        if len(rest) <= 20 and "," not in rest and not _BK_END.search(rest):
            out.append(rest.strip("‘’'\""))
    if re.search(r"신간|새책", t):  # …'트렌드 리터러시' / …'착한 아이는…'[신간]
        m = re.search(r"[‘'“\"]([^’'”\"]{2,40})[’'”\"]\s*(?:\[(?:신간|새책)\])?\s*$", t)
        if m:
            out.append(m.group(1))
    m = re.match(r"^\[[^\]]+\]\s*[‘']([^’']{2,40})[’']\s*(?:외|外)", t)  # [북카페] ‘왜 다시 아시아인가’ 외
    if m:
        out.append(m.group(1))
    # (2026-10-30 삭제) 한겨레 서평 '[.txt]' 제목 속 따옴표 낱말 규칙: '억울함'·'오리'처럼 책 제목이 아닌 경우가 대부분이었다.
    res, seen = [], set()
    for b in out:
        b = re.sub(r"\s*(?:…|\.{2,})\s*$", "", re.sub(r"\s+", " ", b))  # 끝 말줄임표(줄여 쓴 제목)
        b = re.sub(r"\s*(?:외|外)\s*$", "", b).strip(" ·,")
        k = re.sub(r"\W", "", b)
        if re.search(r"[…‘’“”'\"]", b):  # 말줄임표·따옴표가 안에 남은 것은 기사 제목 조각(예: 기억 잃은 엄마의 시간…'다이브 인)
            continue
        if 2 <= len(b) <= 40 and not re.fullmatch(r"[\d\s.,]+", b) and not _BK_NOT.search(b) and k not in seen:
            seen.add(k)
            res.append(b)
    # 한 기사에서 '왕의 탈출'과 '왕의 탈출 : 아관파천'이 함께 나오면 더 긴(부제까지 있는) 쪽만
    keys = [re.sub(r"\W", "", b).lower() for b in res]
    return [b for b, k in zip(res, keys) if not any(o != k and o.startswith(k) for o in keys)]


def book_titles(rows, top=60, info=None, only_found=True):
    """소개된 책 목록: 여러 언론사가 다룬 책이 위로, 같으면 최근 것이 위로. arts = 그 책을 다룬 기사 번호(rows 안)
    info(서점 정보)를 주면 제목 표기가 달라도 ISBN(없으면 서점 주소)이 같은 책은 한 권으로 합친다."""
    agg = {}
    for n, x in enumerate(rows):
        for b in book_titles_of(x):
            k = re.sub(r"\W", "", b).lower()
            e = agg.setdefault(k, {"title": b, "outlets": [], "url": x["url"], "date": x["date"], "arts": []})
            if x["outlet"] not in e["outlets"]:
                e["outlets"].append(x["outlet"])
            if n not in e["arts"]:
                e["arts"].append(n)
            if x["date"] > e["date"]:
                e["date"] = x["date"]
    if info is not None and info and only_found:  # 서점(예스24)에서 찾은 책만: 책 제목이 아닌 낱말·기사 사진 표지를 거른다
        agg = {k: e for k, e in agg.items() if (info.get(k) or {}).get("name")}
    if info:  # 예: '이루어질 수 없는 사랑…'(연합) = '이루어질 수 없는 사랑, 이루어질 수없는 사랑'(동아)
        merged, by_id = [], {}
        for e in sorted(agg.values(), key=lambda e: (len(e["arts"]), e["date"]), reverse=True):
            i = info.get(_bk_key(e["title"])) or {}
            bid = i.get("isbn") or i.get("url")
            m = by_id.get(bid) if bid else None
            if m is None:
                if bid:
                    by_id[bid] = e
                merged.append(e)
                continue
            m["outlets"] += [o for o in e["outlets"] if o not in m["outlets"]]
            m["arts"] += [n for n in e["arts"] if n not in m["arts"]]
            m["date"] = max(m["date"], e["date"])
        agg = {i: e for i, e in enumerate(merged)}
    # 기사마다 부제 표기가 다른 같은 책(앞부분 4자 이상이 같고 서점 정보가 다르지 않은 것)은 한 권으로
    es = sorted(agg.values(), key=lambda e: len(_bk_key(e["title"])), reverse=True)
    keep = []
    for e in es:
        k = _bk_key(e["title"])
        ie = ((info or {}).get(k) or {}).get("isbn")
        m = next((x for x in keep if len(k) >= 4 and _bk_key(x["title"]).startswith(k)
                  and (not ie or not ((info or {}).get(_bk_key(x["title"])) or {}).get("isbn")
                       or ie == ((info or {}).get(_bk_key(x["title"])) or {}).get("isbn"))), None)
        if m is None:
            keep.append(e)
            continue
        if ie and not ((info or {}).get(_bk_key(m["title"])) or {}).get("isbn"):
            m["title"] = e["title"]  # 서점 정보가 있는 표기를 쓴다
        m["outlets"] += [o for o in e["outlets"] if o not in m["outlets"]]
        m["arts"] += [n for n in e["arts"] if n not in m["arts"]]
        m["date"] = max(m["date"], e["date"])
    out = sorted(keep, key=lambda e: (len(e["outlets"]), e["date"]), reverse=True)
    return out[:top]


# 책 정보(표지·저자·출판사): 예스24 도서 검색 결과의 첫 일치 항목. robots.txt가 /Product/Search 허용 (2026-10-24 확인).
# 같은 책은 다시 찾지 않게 history/book_info.json에 보관한다.
BOOK_INFO = os.path.join(HERE, "history", "book_info.json")
YES24_SEARCH = "https://www.yes24.com/Product/Search?"


def _bk_key(t):
    return re.sub(r"\W", "", t).lower()


def _bk_match(title, name):
    """기사 속 책 이름과 서점 책 이름이 같은 책인가: 같거나, 뒤에 권 번호·세트·영문 부제만 붙은 경우(짧은 이름은 엄격히)"""
    want, got = _bk_key(re.sub(r"\(.*?\)", "", title)), _bk_key(re.sub(r"\(.*?\)", "", name))
    if not want or not got:
        return False
    if got == want or _bk_key(title) == _bk_key(name):  # 정희(正熙) = 정희正熙
        return True
    if got.startswith(want):
        rest = got[len(want):]
        return len(want) >= 8 or bool(re.fullmatch(r"\d+권?|세트|개정판|[a-z0-9]+", rest))
    sp = lambda x: re.sub(r"\s+", " ", re.sub(r"\(.*?\)", "", x)).strip().lower()
    if len(want) >= 4 and sp(name).endswith(" " + sp(title)):  # '더 늦기 전에 인생의 효율' ← '인생의 효율' (띄어쓰기 경계에서만)
        return True
    return False


def yes24_lookup(title):
    """예스24 도서 검색 → {name, author, pub, date, cover, url} 또는 {}"""
    url = YES24_SEARCH + urllib.parse.urlencode({"domain": "BOOK", "query": title})
    if not robots_allows(url):
        return {}
    body = http_get(url).decode("utf-8", "ignore")
    i = body.find('id="yesSchList"')
    if i < 0:
        return {}
    best = None
    for li in re.findall(r'<li\b[^>]*data-goods-no="(\d+)"[^>]*>(.*?)(?=<li\b[^>]*data-goods-no=|</ul>)', body[i:i + 250000], re.S)[:6]:
        no, blk = li
        m = re.search(r'class="gd_name"[^>]*>(.*?)</a>', blk, re.S)
        if not m:
            continue
        name = _strip_tags(m.group(1))
        if not _bk_match(title, name):
            continue
        au = re.search(r'class="authPub info_auth"[^>]*>(.*?)</span>', blk, re.S)
        pb = re.search(r'class="authPub info_pub"[^>]*>(.*?)</span>', blk, re.S)
        dt = re.search(r'class="authPub info_date"[^>]*>(.*?)</span>', blk, re.S)
        info = {"name": name, "author": re.sub(r"\s*/\s*", " · ", re.sub(r"\s+", " ", _strip_tags(au.group(1)))).strip() if au else "",
                "pub": _strip_tags(pb.group(1)) if pb else "", "date": _strip_tags(dt.group(1)) if dt else "",
                "cover": f"https://image.yes24.com/goods/{no}/L", "url": f"https://www.yes24.com/product/goods/{no}"}
        ym = re.search(r"(\d{4})년\s*(\d{1,2})월", info["date"])
        info["_ym"] = int(ym.group(1)) * 100 + int(ym.group(2)) if ym else 0
        if best is None or info["_ym"] > best["_ym"]:  # 같은 제목이 여럿이면 가장 최근 책
            best = info
    if best:
        best.pop("_ym", None)
    return best or {}


KYOBO_SEARCH = "https://search.kyobobook.co.kr/search?"  # robots.txt: /api/ 만 차단 (2026-10-24 확인)


def kyobo_isbn(title):
    """교보문고 검색 결과의 data-bid(ISBN-13) 중 제목이 맞는 첫 책"""
    url = KYOBO_SEARCH + urllib.parse.urlencode({"keyword": title, "gbCode": "TOT", "target": "total"})
    if not robots_allows(url):
        return ""
    body = http_get(url).decode("utf-8", "ignore")
    for isbn, name in re.findall(r'data-bid="(97[89]\d{10})" data-name="([^"]+)"', body)[:10]:
        if _bk_match(title, html.unescape(name)):
            return isbn
    return ""


def enrich_book_titles(rows, max_new=40, workers=4):
    """'이번 주 소개된 책'의 책마다 표지·저자·출판사 → {제목키: 정보}"""
    try:
        cache = json.load(open(BOOK_INFO, encoding="utf-8"))
    except Exception:
        cache = {}
    titles = [e["title"] for e in book_titles(rows)]
    today = datetime.now(KST).strftime("%Y-%m-%d")
    new = [t for t in titles if _bk_key(t) not in cache
           or (not cache[_bk_key(t)].get("name") and cache[_bk_key(t)].get("_t", "") < today)][:max_new]  # 못 찾은 책은 하루 한 번 다시

    def work(t):
        try:
            return t, (yes24_lookup(t) or {"_t": today}), None
        except Exception as e:
            return t, None, type(e).__name__
    with ThreadPoolExecutor(workers) as ex:
        for t, info, err in ex.map(work, new):
            if err is None:
                cache[_bk_key(t)] = info  # 못 찾은 것도 {}로 기억해 다시 찾지 않는다
    # ISBN: 책 정보가 있고 아직 ISBN을 찾아보지 않은 책만 (교보문고 검색)
    need = [t for t in titles if cache.get(_bk_key(t)) and "isbn" not in cache[_bk_key(t)]][:max_new]

    def isbn_work(t):
        try:
            return t, kyobo_isbn(t), None
        except Exception as e:
            return t, "", type(e).__name__
    with ThreadPoolExecutor(workers) as ex:
        for t, isbn, err in ex.map(isbn_work, need):
            if err is None:
                cache[_bk_key(t)]["isbn"] = isbn
    out = {_bk_key(t): cache[_bk_key(t)] for t in titles if cache.get(_bk_key(t)) and _bk_match(t, cache[_bk_key(t)].get("name", ""))}
    try:
        os.makedirs(os.path.dirname(BOOK_INFO), exist_ok=True)
        keep = dict(list(cache.items())[-800:])
        json.dump(keep, open(BOOK_INFO, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return out


def render_book_side(rows, info=None):
    """책책책 탭 오른쪽: 많이 소개된 책 TOP 20 (누르면 책 정보 창)"""
    bl = book_titles(rows or [], info=info or {})[:20]
    if not bl:
        return ""
    info = info or {}
    items = "".join(
        f'<li><span><button type="button" class="blink" data-book="{k}">『{esc((info.get(_bk_key(e["title"])) or {}).get("name") or e["title"])}』</button>'
        f'<span class="o">{esc("·".join(e["outlets"]))}</span></span></li>' for k, e in enumerate(bl))
    return (f'<section class="pg" data-c="{BOOK_TAB}"><div class="shead"><h2>많이 소개된 책</h2><span class="en">TOP {len(bl)}</span></div>'
            f'<div class="pop"><ol>{items}</ol><p class="note">이번 주 여러 신문이 함께 소개한 책 순서입니다. 누르면 책 정보 창이 뜹니다.</p></div></section>')


BOOK_CATS = ["신간 소개", "서평·책 이야기", "출판계 소식", "어린이·그림책", "기타"]


def book_cat(x, has_book):
    """책 기사 분류(제목 표시와 낱말로). 책 코너 기사라서 특별한 표시가 없으면 '서평·책 이야기'."""
    t = x["title"]
    if re.search(r"\[\d+화\]|\[편집자 레터\]", t):  # 연재소설, 편집자 칼럼
        return "기타"
    if re.search(r"그림책|어린이|동화|청소년|\[그림이 있는", t):
        return "어린이·그림책"
    if re.search(r"\[(?:\d+자\s*)?(?:신간|새책|책꽂이|새로 나왔어요|북카페|한줄읽기)|금주의 신간|새 책|신간|새책", t):
        return "신간 소개"
    if not has_book and re.search(r"인수|별세|문학상|노벨|부커|북페스티벌|도서전|서점|베스트셀러|출판사|출판계|협회|오픈|개최|선정|시상|수상", t):
        return "출판계 소식"
    return "서평·책 이야기"


PAJU_LIB = "https://lib.paju.go.kr/jalib/plusSearchResultList.do?"


_OC = {}


def outlet_color(o):
    """신문사마다 고정된 색 번호(0~7). 자주 나오는 신문은 서로 다른 색이 되게 미리 정해 둔다."""
    fixed = {"동아일보": 0, "조선일보": 1, "한겨레": 2, "경향신문": 3, "연합뉴스": 4, "독서신문": 5, "파이낸셜뉴스": 6, "뉴시스": 7,
             "매일경제": 3, "한국경제": 1, "뉴스1": 6, "이데일리": 4, "중앙일보": 5, "한국일보": 7}
    if o in fixed:
        return fixed[o]
    return _OC.setdefault(o, sum(ord(c) for c in o) % 8)


BOOK_COPY = '<button type="button" class="cpy" data-copy="{v}" aria-label="ISBN 복사" title="ISBN 복사"><svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg><span class="cpd">복사됨</span></button>'


def render_books(rows, now, info=None):
    """책책책 탭: ① 이번 주의 책 진열대(표지·제목·저자·ISBN·소개한 신문) ② 책을 누르면 책 정보 창(PC 가운데 창, 휴대폰 아래에서 올라오는 창)
    ③ 분류 버튼이 있는 책 기사 목록"""
    if not rows:
        return ""
    info = info or {}
    bl = book_titles(rows, info=info)
    linked = {}  # 기사 번호 → 책 번호
    for k, e in enumerate(bl):
        for n in e["arts"]:
            linked.setdefault(n, k)

    def when(iso):
        d = datetime.fromisoformat(iso)
        return f"{d.month}.{d.day} ({WEEK[d.weekday()]})"

    def isbn_line(isbn):
        return f'<div class="bki">ISBN: <span class="isbn">{esc(isbn)}</span>{BOOK_COPY.format(v=esc(isbn))}</div>' if isbn else ""

    cards, dets = [], []
    for k, e in enumerate(bl):
        i = info.get(_bk_key(e["title"])) or {}
        name = i.get("name") or e["title"]
        cover = i.get("cover") or next((rows[n]["img"] for n in e["arts"] if rows[n].get("img")), None)  # 서점에 없으면 기사 사진
        auth = re.split(r" · ", i.get("author") or "")[0]
        img = (f'<img src="{esc(cover)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML=\'<span class=bph>{esc(name)}</span>\'">'
               if cover else f'<span class="bph">{esc(name)}</span>')
        src = "·".join(e["outlets"])
        cards.append(f'<div class="bcard" data-n="{len(e["outlets"])}" data-d="{esc(e["date"])}">'
                     f'<button type="button" class="bopen" data-book="{k}" aria-label="『{esc(name)}』 책 정보">'
                     f'<span class="bcov">{img}</span><span class="bnm">{esc(name)}</span>'
                     + (f'<span class="bau">{esc(auth)}{" · " + esc(i["pub"]) if i.get("pub") else ""}</span>' if auth or i.get("pub") else "")
                     + f'</button>{isbn_line(i.get("isbn") or "")}'
                     f'<div class="bsrc{" hot" if len(e["outlets"]) > 1 else ""}">{esc(src)}</div></div>')
        q = urllib.parse.urlencode({"searchType": "SIMPLE", "searchCategory": "ALL", "searchLibrary": "ALL", "searchKey": "TITLE",
                                    "searchKeyword": re.sub(r"\(.*?\)", "", name).strip()})
        btns = "".join(f'<a class="bbtn" href="{esc(u)}" target="_blank" rel="noopener noreferrer">{esc(t)}</a>' for t, u in (
            (("파주시 도서관에서 찾기", PAJU_LIB + q),) + ((("예스24", i["url"]),) if i.get("url") else ())))
        arts = "".join(
            f'<li><b class="ot oc{outlet_color(rows[n]["outlet"])}">{esc(rows[n]["outlet"])}</b> {link(rows[n]["url"], rows[n]["title"])}<span class="o">{when(rows[n]["date"])}</span>'
            + (f'<p>{esc(rows[n]["desc"])}</p>' if rows[n].get("desc") else "") + "</li>" for n in e["arts"])
        big = f'<img src="{esc(cover)}" alt="『{esc(name)}』 표지" referrerpolicy="no-referrer" onerror="this.remove()">' if cover else ""
        dets.append(f'<template id="bd{k}"><div class="bdh">{f"<div class=bdc>{big}</div>" if big else ""}<div class="bdi">'
                    f'<h3>『{esc(name)}』</h3>'
                    + (f'<div class="bkm">{esc(i["author"])}</div>' if i.get("author") else "")
                    + (f'<div class="bkm">{esc(" · ".join(x for x in (i.get("pub"), i.get("date")) if x))}</div>' if i.get("pub") or i.get("date") else "")
                    + isbn_line(i.get("isbn") or "")
                    + f'<div class="bkm" style="margin-top:4px">소개: {esc(src)}</div><div class="bbtns">{btns}</div></div></div>'
                    f'<h4>이 책을 다룬 기사 {len(e["arts"])}건</h4><ul class="barts">{arts}</ul></template>')
    modal = ('<dialog id="bmodal" aria-label="책 정보"><button type="button" class="bclose" aria-label="닫기">✕</button><div id="bmbody"></div></dialog>'
             + "".join(dets))
    shelf = (f'<div class="shead" style="padding-top:12px"><h2>이번 주의 책</h2><span class="en">{len(bl)}권 · 최근 1주</span></div>'
             f'<div class="ofilter" id="bsort" role="group" aria-label="정렬"><button class="on" data-s="n">많이 소개된 순</button><button data-s="d">최신순</button></div>'
             f'<div class="shelf2" id="shelf">{"".join(cards)}</div>{modal}'
             f'<p class="note" style="margin-top:10px">신문 책 코너 기사에서 뽑은 책입니다. 표지·저자·출판사는 예스24, ISBN은 교보문고 검색(서점에 없는 책은 기사 사진). 책을 누르면 정보와 그 책을 다룬 기사가 뜹니다.</p>') if bl else ""

    cats = [book_cat(x, n in linked) for n, x in enumerate(rows)]
    cnt = Counter(cats)
    shown = sum(v for c, v in cnt.items() if c != "기타")
    chips = (f'<button class="on" data-k="">전체<i>{shown}</i></button>'
             + "".join(f'<button data-k="{esc(c)}">{esc(c)}<i>{cnt[c]}</i></button>' for c in BOOK_CATS if cnt.get(c)))

    def row(n, x):
        th = (f'<a class="bth" href="{esc(x["url"])}" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true">'
              f'<img src="{esc(x["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"></a>') if x.get("img") else ""
        bk = (f'<button type="button" class="blink sm" data-book="{linked[n]}">『{esc((info.get(_bk_key(bl[linked[n]]["title"])) or {}).get("name") or bl[linked[n]]["title"])}』 책 정보</button>'
              if n in linked else "")
        d = datetime.fromisoformat(x["date"])
        return (f'<li data-k="{esc(cats[n])}"{" hidden" if cats[n] == "기타" else ""}>{th}<div><div class="bt">{link(x["url"], x["title"])}</div>'
                + (f'<p class="bd">{esc(x["desc"])}</p>' if x.get("desc") else "")
                + f'<div class="pm"><b>{esc(x["outlet"])}</b> · {d.month}.{d.day} ({WEEK[d.weekday()]}) · {esc(cats[n])}{" · " + bk if bk else ""}</div></div></li>')
    homes = " · ".join(link(u, o) for o, u in BOOK_HOME.items())
    return (f'<div class="pg" data-c="{BOOK_TAB}" role="tabpanel"><section class="photos books" style="margin-top:0">{shelf}</section>'
            f'<section class="photos books"><div class="shead"><h2>책 기사</h2><span class="en">BOOK NEWS · {len(rows)}건</span></div>'
            f'<div class="ofilter" id="bcat" role="group" aria-label="분류">{chips}</div>'
            f'<ul class="blist" id="bl">{"".join(row(n, x) for n, x in enumerate(rows))}</ul>'
            f'<p class="note">연재소설·편집자 칼럼은 \'기타\'에 접어 두었습니다. 신문사 책 코너 바로 가기: {homes}</p></section></div>')


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


BOOK_META = os.path.join(HERE, "history", "book_meta.json")
_DESC_RX = [re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', re.I),
            re.compile(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:description["\']', re.I),
            re.compile(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)["\']', re.I)]


def og_meta(url):
    """원문 페이지의 대표 사진·소개글 (robots.txt가 막으면 빈 값)"""
    if not robots_allows(url):
        return {}
    page = http_get(url)[:400000].decode("utf-8", "ignore")
    out = {}
    for rx in _OG_RX:
        m = rx.search(page)
        if m and m.group(1).strip():
            img = html.unescape(m.group(1)).strip()
            out["img"] = urllib.parse.urljoin(url, "https:" + img if img.startswith("//") else img)
            break
    for rx in _DESC_RX:
        m = rx.search(page)
        if m and len(m.group(1).strip()) >= 20:
            d = re.sub(r"\s+", " ", html.unescape(html.unescape(m.group(1)))).strip()
            out["desc"] = d[:220] + ("…" if len(d) > 220 else "")
            break
    return out


def enrich_books(rows, workers=6, max_new=60, path=None, need_img=False):
    """요약이 없는 책 기사: (구글뉴스면 원문 주소로 풀고) 원문의 소개글·대표 사진을 붙인다. 결과는 history/book_meta.json에 캐시.
    path·need_img: 맥락·탐사 탭처럼 다른 캐시 파일을 쓰거나, 사진이 없는 글도 원문 대표 사진으로 채울 때."""
    path = path or BOOK_META
    try:
        cache = json.load(open(path, encoding="utf-8"))
    except Exception:
        cache = {}
    todo = [r for r in rows if not r.get("desc") or (need_img and not r.get("img"))]
    new = [r for r in todo if r["url"] not in cache][:max_new]

    def work(r):
        out = {}
        try:
            url = r["url"]
            if GN_RX.search(url):
                url = gn_decode(url) or ""
                out["url"] = url
            if url:
                out.update(og_meta(url))
        except Exception as e:
            out["err"] = type(e).__name__
        return r["url"], out
    with ThreadPoolExecutor(workers) as ex:
        for key, out in ex.map(work, new):
            if "err" not in out:
                cache[key] = out
    n = 0
    for r in todo:
        m = cache.get(r["url"]) or {}
        if m.get("url"):
            r["url"] = m["url"]
        if not r.get("desc") and m.get("desc") and re.sub(r"\W", "", m["desc"])[:30] != re.sub(r"\W", "", r["title"])[:30]:
            r["desc"] = m["desc"]
            n += 1
        if m.get("img") and (not r.get("img") or "/imgdb/child/" in r["img"]):  # 한겨레 RSS 사진은 300×180로 잘린 것 → 원문 원본으로
            r["img"] = m["img"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        keep = {r["url"]: cache[r["url"]] for r in rows if r["url"] in cache}  # 목록에 남은 것만 보관
        keep.update({k: v for k, v in cache.items() if k not in keep and len(keep) < 600})
        json.dump(keep, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return len(todo), n


# ── 맥락·탐사 탭: 해설·탐사 중심 독립 매체의 최근 1주 글 ──
# 2026-10-25 확인: 모두 RSS 제공, robots.txt 허용. 본문 전체가 RSS에 들어 있는 곳도 있지만 제목·요약 앞부분·링크·사진 주소만 쓴다.
DEEP_TAB = "맥락·탐사"
DEEP_FEEDS = [("슬로우뉴스", "https://slownews.kr/feed", "https://slownews.kr"),
              ("뉴스타파", "https://newstapa.org/rss", "https://newstapa.org"),
              ("셜록", "https://www.neosherlock.com/feed", "https://www.neosherlock.com"),
              ("시사IN", "https://cdn.sisain.co.kr/rss/gns_allArticle.xml", "https://www.sisain.co.kr"),
              ("뉴스하다", "https://newshada.org/feed/", "https://newshada.org"),
              ("뉴스버스", "https://www.newsverse.kr/rss/allArticle.xml", "https://www.newsverse.kr")]
DEEP_META = os.path.join(HERE, "history", "deep_meta.json")
DEEP_DESC = {"슬로우뉴스": "맥락과 통찰, 해법", "뉴스타파": "비영리 탐사보도", "셜록": "탐사보도",
             "시사IN": "주간 시사·심층", "뉴스하다": "인천·경기 비영리 탐사", "뉴스버스": "탐사·해설"}


def fetch_deep(now, days=7, per_outlet=25):
    """맥락·탐사 매체 RSS → (rows, status). 매체마다 최근 7일, 최대 25건."""
    feeds = [{"outlet": o, "category": DEEP_TAB, "url": u} for o, u, _ in DEEP_FEEDS]
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(fetch_feed, feeds))
    rows, status, seen = [], [], set()
    lo, hi = now - timedelta(days=days), now + timedelta(minutes=10)
    for feed, its, err in results:
        n = 0
        for a in sorted((a for a in its if a.get("date")), key=lambda a: a["date"], reverse=True):
            t = re.sub(r"\s+", " ", html.unescape(a.get("title") or "")).strip()
            if not t or not (lo <= a["date"] <= hi) or not re.match(r"https?://", a.get("url") or "") or n >= per_outlet:
                continue
            key = (a["outlet"], re.sub(r"\W", "", t)[:40])
            if key in seen:
                continue
            seen.add(key)
            n += 1
            desc = a.get("desc") or ""
            if desc and re.sub(r"\W", "", desc)[:30] == re.sub(r"\W", "", t)[:30]:
                desc = ""
            rows.append({"title": t, "url": a["url"], "outlet": a["outlet"], "img": a.get("img"), "date": a["date"].isoformat(), "desc": desc})
        status.append({"name": f'{feed["outlet"]}(맥락·탐사)', "ok": err is None and n > 0, "count": n, "error": err or ("최근 1주 0건" if not n else "")})
    rows.sort(key=lambda x: x["date"], reverse=True)
    return rows, status


def render_deep(rows, now):
    """맥락·탐사 탭 본문: 매체 버튼 + 글 목록(사진·제목·요약·매체·날짜)"""
    if not rows:
        return ""
    cnt = Counter(x["outlet"] for x in rows)
    chips = (f'<button class="on" data-k="">전체<i>{len(rows)}</i></button>'
             + "".join(f'<button data-k="{esc(o)}">{esc(o)}<i>{cnt[o]}</i></button>' for o, _, _ in DEEP_FEEDS if cnt.get(o)))

    def row(x):
        th = (f'<a class="dth" href="{esc(x["url"])}" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true">'
              f'<img src="{esc(x["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"></a>') if x.get("img") else ""
        d = datetime.fromisoformat(x["date"])
        return (f'<li data-k="{esc(x["outlet"])}">{th}<div><div class="pm top"><b class="ot oc{outlet_color(x["outlet"])}">{esc(x["outlet"])}</b>'
                f'{d.month}.{d.day} ({WEEK[d.weekday()]})</div><div class="bt">{link(x["url"], x["title"])}</div>'
                + (f'<p class="bd">{esc(x["desc"])}</p>' if x.get("desc") else "") + "</div></li>")
    return (f'<div class="pg" data-c="{DEEP_TAB}" role="tabpanel"><section class="photos books" style="margin-top:0">'
            f'<div class="shead" style="padding-top:12px"><h2>맥락·탐사</h2><span class="en">IN DEPTH · 최근 1주 {len(rows)}건</span></div>'
            f'<div class="ofilter" id="dcat" role="group" aria-label="매체">{chips}</div>'
            f'<ul class="blist dlist" id="dl">{"".join(row(x) for x in rows)}</ul>'
            f'<p class="note">속보보다 맥락·해설·탐사에 힘쓰는 독립 매체들의 최근 1주 글입니다. 각 매체 RSS의 제목과 요약 앞부분만 보여 주며, 글은 원문 사이트에서 읽습니다.</p>'
            f'</section></div>')


def render_deep_side(rows):
    """맥락·탐사 탭 오른쪽 칸: 매체 소개와 이번 주 글 수, 바로 가기"""
    cnt = Counter(x["outlet"] for x in rows or [])
    lis = "".join(f'<li><b class="ot oc{outlet_color(o)}">{esc(o)}</b> {link(h, DEEP_DESC.get(o, ""))}<span class="o">{cnt.get(o, 0)}건</span></li>'
                  for o, _, h in DEEP_FEEDS)
    return (f'<section class="pg" data-c="{DEEP_TAB}"><div class="shead"><h2>매체 바로 가기</h2><span class="en">이번 주 글 수</span></div>'
            f'<ul class="barts dside">{lis}</ul></section>')


# ── 파주 탭: 파주시청 소식(파주뉴스 주요뉴스 + 매일 보도자료 제목) ──
# 2026-10-27 확인: robots.txt는 /user/… 일부만 막고 /news/는 허용. RSS 없음 → 목록 HTML을 읽는다.
PAJU_NEWS = "https://www.paju.go.kr/news/user/"
PAJU_FOCUS = PAJU_NEWS + "BD_focusNewsList.do?q_currPage=1&q_rowPerPage=6"
PAJU_PRESS = PAJU_NEWS + "board/BD_board.list.do?bbsCd=1023&q_ctgCds=5226,5227,5229"
PAJU_PRESS_VIEW = PAJU_NEWS + "board/BD_board.view.do?seq={seq}&bbsCd=1023&q_ctgCds=5226,5227,5229"


def _plain(x):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()


def fetch_paju_city(days=3):
    """→ ({"focus": [{title,url,img,date,desc}], "press": [{date,url,titles:[...]}]}, status)"""
    out, status = {"focus": [], "press": []}, []
    try:
        if not robots_allows(PAJU_FOCUS):
            raise RuntimeError("robots.txt 차단")
        t = http_get(PAJU_FOCUS).decode("utf-8", "replace")
        for m in re.finditer(r'<li>\s*<a href="(BD_newsView\.do\?q_ctgCd=\d+&(?:amp;)?newsSeq=\d+)" class="thumb">(.*?)</li>', t, re.S):
            blk = m.group(2)
            title = _plain((re.search(r'<strong class="subject">(.*?)</strong>', blk, re.S) or [None, ""])[1])
            if not title:
                continue
            img = re.search(r'<img src="([^"]+)"', blk)
            raw = (re.search(r'<span class="content">(.*?)</span>', blk, re.S) or [None, ""])[1]
            # 요약은 '제목- 부제- 부제' 한 줄 + 빈 줄 + 본문 → 제목 줄은 버리고 본문부터
            parts = [_plain(x) for x in re.split(r"\n[ \t]*\n", raw) if _plain(x)]
            key = re.sub(r"\W", "", title)[:10]
            body = [x for x in parts if not re.sub(r"\W", "", x).startswith(key)]
            desc = " ".join(body) or _plain(raw)
            dm = re.search(r"(20\d\d)[.\-/](\d\d)[.\-/](\d\d)", blk[blk.find("</span>"):] if "</span>" in blk else blk)
            out["focus"].append({"title": title, "url": PAJU_NEWS + html.unescape(m.group(1)),
                                 "img": urllib.parse.urljoin(PAJU_NEWS, img.group(1)) if img else None,
                                 "date": "-".join(dm.groups()) if dm else "", "desc": desc[:200] + ("…" if len(desc) > 200 else "")})
        status.append({"name": "파주시청 주요뉴스", "ok": bool(out["focus"]), "count": len(out["focus"]), "error": "" if out["focus"] else "0건"})
    except Exception as e:
        status.append({"name": "파주시청 주요뉴스", "ok": False, "count": 0, "error": f"{type(e).__name__}: {str(e)[:60]}"})
    try:
        if not robots_allows(PAJU_PRESS):
            raise RuntimeError("robots.txt 차단")
        t = http_get(PAJU_PRESS).decode("utf-8", "replace")
        posts = re.findall(r"jsView\('1023',\s*'(\d{17})'[^>]*>\s*([^<]*?보도자료[^<]*?)\s*<", t)[:days]
        for seq, _ in posts:
            u = PAJU_PRESS_VIEW.format(seq=seq)
            v = http_get(u).decode("utf-8", "replace")
            body = (re.search(r'<div class="article-conetnt">(.*?)</div>', v, re.S) or [None, ""])[1]
            titles = []
            for line in re.split(r"<br\s*/?>|</p>|\n", body):
                m = re.match(r"\s*\d{1,2}\s*[.)]\s*(.+)", _plain(line))
                if m and len(m.group(1)) >= 6:
                    titles.append(m.group(1))
            if titles:
                out["press"].append({"date": f"{seq[:4]}-{seq[4:6]}-{seq[6:8]}", "time": f"{seq[8:10]}:{seq[10:12]}", "url": u, "titles": titles})
        n = sum(len(x["titles"]) for x in out["press"])
        status.append({"name": "파주시청 보도자료", "ok": n > 0, "count": n, "error": "" if n else "0건"})
    except Exception as e:
        status.append({"name": "파주시청 보도자료", "ok": False, "count": 0, "error": f"{type(e).__name__}: {str(e)[:60]}"})
    return out, status


def render_paju_city(pc):
    """파주 탭의 '파주뉴스' 화면: 파주시청 주요뉴스와 보도자료 제목을 날짜별 한 목록으로.
    같은 일이 양쪽에 있으면(제목 낱말이 많이 겹치면) 사진·요약이 있는 주요뉴스만 남긴다."""
    if not pc or not (pc.get("focus") or pc.get("press")):
        return ""
    rows = []  # (날짜, 순서, 종류, 항목)
    common = {"파주시", "파주", "개최", "추진", "운영", "실시", "모집", "진행", "참여", "시민"}

    def kw(t):  # 흔한 낱말·날짜 숫자를 뺀 제목 낱말
        return {w for w in words(t) if w not in common and not re.search(r"\d", w)}
    fw = [(x, kw(x["title"])) for x in pc.get("focus", [])]
    for x, _ in fw:
        rows.append((x.get("date") or "", f'{x.get("time", "")}', 0, "focus", x))
    for p in pc.get("press", []):
        for k, t in enumerate(p["titles"]):
            w = kw(t)
            dup = any(len(w & v) >= 2 and len(w & v) / len(w | v) >= 0.35 for _, v in fw)
            if not dup:
                rows.append((p["date"], p.get("time", ""), k + 1, "press", {"title": t, "url": p["url"]}))
    rows.sort(key=lambda r: (r[0], r[1], -r[2]), reverse=True)

    def md(iso):
        try:
            d = datetime.strptime(iso, "%Y-%m-%d")
            return f"{d.month}.{d.day} ({WEEK[d.weekday()]})"
        except Exception:
            return "날짜 없음"

    def item(kind, x):
        if kind == "focus":
            th = (f'<a class="dth" href="{esc(x["url"])}" target="_blank" rel="noopener noreferrer" tabindex="-1" aria-hidden="true">'
                  f'<img src="{esc(x["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"></a>') if x.get("img") else ""
            return (f'<li>{th}<div><div class="pm top"><b class="ot oc2">주요뉴스</b></div><div class="bt">{link(x["url"], x["title"])}</div>'
                    + (f'<p class="bd">{esc(x["desc"])}</p>' if x.get("desc") else "") + "</div></li>")
        return f'<li class="pr"><div><b class="ot oc0">보도자료</b> {link(x["url"], x["title"])}</div></li>'
    days, cur = [], None
    for d, _, _, kind, x in rows:
        if d != cur:
            days.append([d, []])
            cur = d
        days[-1][1].append(item(kind, x))
    nf = sum(1 for r in rows if r[3] == "focus")
    body = "".join(f'<h3 class="pday">{md(d)}<span>{len(its)}건</span></h3><ul class="blist dlist">{"".join(its)}</ul>' for d, its in days)
    return (f'<section class="photos books pcity" style="margin-top:0"><div class="shead"><h2>파주시청 소식</h2>'
            f'<span class="en">주요뉴스 {nf} · 보도자료 {len(rows) - nf}</span></div>{body}'
            f'<p class="note">파주시청 누리집 {link("https://www.paju.go.kr/news/index.do", "파주뉴스")}의 주요뉴스와 최근 3일 보도자료 제목입니다. '
            f'보도자료를 누르면 그날 보도자료 게시글(한글 파일 첨부)로 갑니다.</p></section>')


# ── 오늘의 차트 탭(옛 이름 비쥬얼뉴스): 해외 데이터 시각화(공식 피드) + 바로 가기 ──
# 2026-10-29 검토: Visual Capitalist(RSS 허용, 소규모 사이트 무료·원문 링크·출처 표시·그림 수정 금지),
# Our World in Data(CC BY, Atom 피드). Voronoi·Tableau Public·McKinsey·IMF는 약관(자동 수집 금지 등)·차단 때문에 링크만.
VISUAL_TAB = "오늘의 차트"
VISUAL_FEEDS = [("Visual Capitalist", "https://www.visualcapitalist.com/feed/"),
                ("Our World in Data", "https://ourworldindata.org/atom-data-insights.xml")]
VISUAL_CACHE = os.path.join(HERE, "history", "visual.json")
VISUAL_HOUR = 8  # 하루 한 번 받는 시각(한국 시간)
# 사이트에 직접 접속하지 않고 구글뉴스 검색 RSS로 제목·링크만(2026-10-19 구글뉴스 예외 결정 범위). 그림은 가져오지 않는다.
# (이름, 검색어, 원문 주소에 있어야 할 경로(구글 링크를 풀어 확인) 또는 None, 제목 끝에서 지울 꼬리)
VISUAL_GN = [("Voronoi", "site:voronoiapp.com when:30d", None, r"\s*-\s*Voronoi by Visual Capitalist$"),
             ("McKinsey", "site:mckinsey.com/featured-insights/charts when:30d", "/featured-insights/charts/", r"\s*-\s*McKinsey & Company$"),
             ("IMF", "site:imf.org/en/Blogs when:30d", "/Blogs/Articles/", r"\s*-\s*International Monetary Fund.*$")]
VISUAL_LINKS = [("Visual Capitalist", "https://www.visualcapitalist.com/", "경제·시장·지정학 인포그래픽"),
                ("Our World in Data", "https://ourworldindata.org/", "세계 데이터 차트 (CC BY)"),
                ("Voronoi", "https://www.voronoiapp.com/", "Visual Capitalist의 데이터 시각화 앱"),
                ("Tableau Public", "https://public.tableau.com/app/discover/viz-of-the-day", "오늘의 비주얼(Viz of the Day)"),
                ("McKinsey", "https://www.mckinsey.com/featured-insights/charts", "Chart of the Week (매주 수요일)"),
                ("IMF", "https://www.imf.org/en/Blogs/chart-of-the-week", "Chart of the Week")]
VISUAL_CREDIT = {"Visual Capitalist": "© Visual Capitalist", "Our World in Data": "Our World in Data · CC BY"}


def fetch_visual(now, days=30, limit=60):
    """→ (rows, status). rows = {title, url, outlet, img, date, cats}. 하루 한 번만 받는다(매일 8시 뒤 첫 갱신, 그 전엔 저장해 둔 것).
    피드에 최근 것만 나오므로 history/visual.json에 모아 30일 보관."""
    A = "{http://www.w3.org/2005/Atom}"
    try:
        cache = json.load(open(VISUAL_CACHE, encoding="utf-8"))
        if isinstance(cache, list):
            cache = {"fetched": "", "rows": cache}
    except Exception:
        cache = {"fetched": "", "rows": []}
    conf = hashlib.md5(json.dumps([VISUAL_FEEDS, VISUAL_GN, 3]).encode()).hexdigest()[:8]  # 출처 설정이 바뀌면 바로 다시 받기
    # 매일 아침 8시(한국 시간) 뒤 첫 갱신 때 한 번 받는다 → 미국에서 밤사이 올라온 차트가 아침에 들어온다
    anchor = now.replace(hour=VISUAL_HOUR, minute=0, second=0, microsecond=0)
    if now < anchor:
        anchor -= timedelta(days=1)
    try:
        fresh = datetime.fromisoformat(cache["fetched"]) >= anchor and cache.get("conf") == conf
    except Exception:
        fresh = False
    if fresh:
        cnt = Counter(x["outlet"] for x in cache["rows"])
        return cache["rows"], [{"name": f"오늘의 차트(하루 한 번 · {cache['fetched'][5:16].replace('T', ' ')} 받음)", "ok": True,
                                "count": len(cache["rows"]), "error": ""}]
    got, status = [], []
    for name, url in VISUAL_FEEDS:
        n = 0
        try:
            if not robots_allows(url):
                raise RuntimeError("robots.txt 차단")
            root = ET.fromstring(http_get(url))
            for it in root.iter("item"):  # RSS (Visual Capitalist)
                m = it.find("{http://search.yahoo.com/mrss/}content")
                d = parse_date(text(it, "pubDate"))
                got.append({"title": html.unescape(text(it, "title")), "url": text(it, "link"), "outlet": name,
                            "img": m.get("url") if m is not None else item_image(it), "date": d.isoformat() if d else "",
                            "cats": [c.text.strip() for c in it.findall("category") if c.text][:3]})
                n += 1
            for it in root.iter(A + "entry"):  # Atom (Our World in Data)
                ln = it.find(A + "link")
                c = it.find(A + "content")
                im = re.search(r'<img[^>]+src="([^"]+)"', c.text or "") if c is not None else None
                d = parse_date(text(it, A + "published") or text(it, A + "updated"))
                got.append({"title": html.unescape(text(it, A + "title")), "url": ln.get("href", "") if ln is not None else "",
                            "outlet": name, "img": re.sub(r"/w=\d+$", "/w=750", im.group(1)) if im else None,
                            "date": d.isoformat() if d else "", "cats": []})
                n += 1
            status.append({"name": f"{name}(오늘의 차트)", "ok": n > 0, "count": n, "error": "" if n else "0건"})
        except Exception as e:
            status.append({"name": f"{name}(오늘의 차트)", "ok": False, "count": 0, "error": f"{type(e).__name__}: {str(e)[:60]}"})
    for name, q, need, tail in VISUAL_GN:  # 제목·링크만 (구글뉴스)
        n = 0
        try:
            feed = {"outlet": name, "category": VISUAL_TAB, "url": "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(q) + "&hl=en-US&gl=US&ceid=US:en"}
            _, its, err = fetch_feed(feed)
            if err:
                raise RuntimeError(err)
            for a in its:
                raw = re.sub(r"\s+", " ", html.unescape(a.get("title") or "")).strip()
                if not need and not re.search(tail, raw):  # 주소로 확인하지 않는 곳은 제목 꼬리(매체 이름)로 확인
                    continue
                t = re.sub(tail, "", raw).strip()
                if not t or not a.get("date") or now - a["date"] > timedelta(days=days) or re.match(r"(IMF Blogs|Chart of the Week|Voronoi by)", t):
                    continue
                u = a["url"]
                if need:  # 원문 주소로 풀어 경로 확인(구글에만 묻는다)
                    known = next((x for x in cache["rows"] if x.get("gn") == u), None)
                    real = known["url"] if known else (gn_decode(u) or "")
                    if need.lower() not in real.lower():
                        continue
                    u2 = real
                else:
                    u2 = u
                got.append({"title": t, "url": u2, "gn": u, "outlet": name, "img": None, "date": a["date"].isoformat(), "cats": []})
                n += 1
            status.append({"name": f"{name}(오늘의 차트·구글뉴스 제목)", "ok": n > 0, "count": n, "error": "" if n else "0건"})
        except Exception as e:
            status.append({"name": f"{name}(오늘의 차트·구글뉴스 제목)", "ok": False, "count": 0, "error": f"{type(e).__name__}: {str(e)[:60]}"})
    by = {x["url"]: x for x in cache["rows"]}
    for x in got:
        if x["title"] and re.match(r"https?://", x["url"] or "") and x["date"]:
            by[x["url"]] = x
    lo = (now - timedelta(days=days)).isoformat()
    per = Counter()
    rows = []
    for x in sorted((x for x in by.values() if x["date"] >= lo), key=lambda x: x["date"], reverse=True):
        per[x["outlet"]] += 1
        if per[x["outlet"]] <= (limit if x.get("img") else 25):  # 제목만인 곳(Voronoi 등)은 매체마다 25건까지 → 다른 매체가 밀리지 않게
            rows.append(x)
    try:
        os.makedirs(os.path.dirname(VISUAL_CACHE), exist_ok=True)
        json.dump({"fetched": now.isoformat() if any(st["ok"] for st in status) else cache.get("fetched", ""), "conf": conf, "rows": rows},
                  open(VISUAL_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return rows, status


def render_visual(rows):
    """오늘의 차트 탭: 출처 버튼 + 그림 카드(누르면 원문). 그림은 잘라내지 않고 그대로 줄여 보여 준다."""
    if not rows:
        return ""
    per = Counter()
    rows = [x for x in rows if x.get("img") or (per.update([x["outlet"]]) or per[x["outlet"]] <= 20)]  # 제목만인 곳은 매체마다 최근 20건
    cnt = Counter(x["outlet"] for x in rows)
    order = [o for o, _ in VISUAL_FEEDS] + [g[0] for g in VISUAL_GN]
    chips = (f'<button class="on" data-o="">전체<i>{len(rows)}</i></button>'
             + "".join(f'<button data-o="{esc(o)}">{esc(o)}<i>{cnt[o]}</i></button>' for o in order if cnt.get(o)))
    pics = [x for x in rows if x.get("img")]
    plain = [x for x in rows if not x.get("img")]

    def line(x):
        d = datetime.fromisoformat(x["date"])
        return (f'<li data-o="{esc(x["outlet"])}"><b class="ot oc{outlet_color(x["outlet"])}">{esc(x["outlet"])}</b>'
                f'{link(x["url"], x["title"])}<span class="o">{d.month}.{d.day}</span></li>')

    def card(x):
        d = datetime.fromisoformat(x["date"])
        ph = (f'<div class="vph"><img src="{esc(x["img"])}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"></div>'
              if x.get("img") else "")
        cats = "".join(f"<span>{esc(c)}</span>" for c in x.get("cats", []))
        return (f'<a class="vcard" data-o="{esc(x["outlet"])}" href="{esc(x["url"])}" target="_blank" rel="noopener noreferrer">{ph}'
                f'<div class="vt">{esc(x["title"])}</div>' + (f'<div class="vcat">{cats}</div>' if cats else "")
                + f'<div class="pm"><b class="ot oc{outlet_color(x["outlet"])}">{esc(x["outlet"])}</b>{d.month}.{d.day} ({WEEK[d.weekday()]})</div></a>')
    return (f'<div class="pg" data-c="{VISUAL_TAB}" role="tabpanel"><section class="photos books" style="margin-top:0">'
            f'<div class="shead" style="padding-top:12px"><h2>오늘의 차트</h2><span class="en">DATA VISUAL · 최근 30일 {len(rows)}건</span></div>'
            f'<div class="ofilter" id="vf" role="group" aria-label="출처">{chips}</div><div class="vgrid" id="vg">{"".join(card(x) for x in pics)}</div>'
            + (f'<h3 class="pday" id="vlh">차트 제목 모음 <span>Voronoi · McKinsey · IMF</span></h3><ul class="slist vlist" id="vl">{"".join(line(x) for x in plain)}</ul>' if plain else "")
            + f'<p class="note">그림을 누르면 크게 볼 수 있고, 제목을 누르면 원문으로 갑니다. 매일 아침 8시에 한 번 받습니다. Visual Capitalist·Our World in Data는 공식 피드로 그림까지, '
            f'Voronoi·McKinsey(Chart of the Week)·IMF(블로그)는 이용약관·접속 차단 때문에 사이트에 직접 접속하지 않고 구글뉴스 검색으로 제목과 링크만 보여 줍니다. '
            f'그림은 원문에서 보세요. Visual Capitalist 그래픽은 Visual Capitalist, Our World in Data 차트는 CC BY 조건으로 출처를 밝혀 소개하며, 제목은 원문(영어) 그대로입니다.</p>'
            f'</section></div>')


def render_visual_side():
    lis = "".join(f'<li><b class="ot oc{outlet_color(n)}">{esc(n)}</b> {link(u, d)}</li>' for n, u, d in VISUAL_LINKS)
    return (f'<section class="pg" data-c="{VISUAL_TAB}"><div class="shead"><h2>차트 바로 가기</h2><span class="en">LINKS</span></div>'
            f'<ul class="barts dside vside">{lis}</ul><p class="note">Tableau Public 오늘의 비주얼은 이용약관상 콘텐츠를 가져올 수 없어 바로 가기만 둡니다.</p></section>')


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
    groups = [c["members"] for c in seeds if len({a["outlet"] for a in c["members"]}) >= min_outlets]
    for _ in range(3):  # 합친 뒤 다시 비교하면 더 합쳐질 수 있다 (변화가 없을 때까지, 최대 3번)
        n = len(groups)
        groups = merge_groups(groups)
        if len(groups) == n:
            break
    out = []
    for members in groups:
        by_outlet = {}
        for a in members:
            by_outlet.setdefault(a["outlet"], a)  # 언론사당 1건(가장 먼저)
        ms = list(by_outlet.values())
        rep = max(ms, key=lambda a: sum(len(a["_w"] & b["_w"]) for b in ms))
        out.append({"title": rep["title"], "count": len(ms), "outlets": sorted(by_outlet),
                    "latest": max(a["date"] for a in members), "first": min(a["date"] for a in members),
                    "total": len(members),
                    "img": rep.get("img") or next((a["img"] for a in members if a.get("img")), None),
                    "links": [{"outlet": a["outlet"], "url": a["url"], "title": a["title"]}
                              for a in sorted(ms, key=lambda a: a["outlet"])]})
    out.sort(key=lambda c: (c["count"], c["total"], c["latest"]), reverse=True)
    return out[:top]


def merge_groups(groups, jac_min=0.22, inter_min=3):
    """같은 사건이 두 묶음으로 갈라진 것 합치기 (예: '이 대통령, 최길수 … 임명안 재가' / '李, 최길수 … 임명').
    묶음마다 기사 제목 낱말을 모두 모아 비교한다. 큰 묶음부터, 원래 낱말끼리만 비교해 한 묶음이 계속 불어나지 않게 한다."""
    groups = sorted(groups, key=len, reverse=True)

    def gw(g):  # 화면에 보이는 대로 언론사당 첫 기사 제목만
        seen, ws = set(), set()
        for a in sorted(g, key=lambda a: a["date"]):
            if a["outlet"] not in seen:
                seen.add(a["outlet"])
                ws |= a["_w"]
        return ws
    W = [gw(g) for g in groups]
    used, out = [False] * len(groups), []
    for i, g in enumerate(groups):
        if used[i]:
            continue
        merged = list(g)
        for j in range(i + 1, len(groups)):
            if used[j]:
                continue
            inter = len(W[i] & W[j])
            if inter >= inter_min and inter / len(W[i] | W[j]) >= jac_min:
                merged += groups[j]
                used[j] = True
        out.append(merged)
    return out


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
        titles = [n["title"] for n in t["news"]] + [a["title"] for a in items if kw in a["title"].lower()][:30]
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
        ["손흥민", "한국시리즈", "환율", "예산안", "공천", "한파"])],
        "다음 이 시각 주요뉴스": [{"title": t, "url": f"https://example.com/d{i}", "outlet": o} for i, (t, o) in enumerate(
        [("정부, 내년 최저임금 인상률 확정 발표", "연합뉴스"), ("서울 첫 한파주의보", "뉴스1")])]}
    portals = {"다음 실시간 트렌드": [{"title": t, "url": f"https://example.com/t{i}", "badge": b} for i, (t, b) in enumerate(
        [("한국시리즈", "신규"), ("최저임금", ""), ("한파", "상승")])], **portals}
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
.brand a.home{display:inline-flex;align-items:center;gap:.28em;color:inherit;text-decoration:none}.brand a.home:focus-visible{outline:2px solid var(--teal);outline-offset:4px;border-radius:4px}.brand{display:inline-flex;align-items:center;gap:.28em;font:400 clamp(40px,7.5vw,64px)/1.1 "Do Hyeon",var(--sans);letter-spacing:.02em;margin:0}.blist{list-style:none;margin:0;padding:0}.blist li{display:flex;gap:14px;align-items:flex-start;padding:12px 0;border-bottom:1px dotted var(--rule-dot)}.blist li{gap:18px;padding:16px 0}.blist .bth{flex:none;width:140px;height:186px;background:var(--paper-2);display:block;border-radius:2px}@media(max-width:600px){.blist li{gap:12px}.blist .bth{width:104px;height:138px}}.blist .bth img{width:100%;height:100%;object-fit:contain;display:block}.blist .bt{font-size:16px;font-weight:700;line-height:1.45}.blist .bt a{color:var(--ink)}.blist .bd{margin:6px 0 0;font-size:13.5px;line-height:1.6;color:var(--ink-body);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.blist .pm{margin-top:6px;font-size:12px;font-weight:400;color:var(--muted)}.blist .pm b{font-weight:700;color:var(--ink-soft)}
.bklist{list-style:none;margin:0;padding:0}.bklist .bk{display:flex;gap:12px;align-items:flex-start;padding:11px 0;border-bottom:1px dotted var(--rule-dot)}.bklist .bkc{flex:none;width:76px;height:110px;background:var(--paper-2);display:block}.bklist .bkc img{width:100%;height:100%;object-fit:contain;display:block}.bklist .bkt{font-size:14.5px;font-weight:700;line-height:1.4}.bklist .bkt a{color:var(--ink)}.bklist .bkm{font-size:12px;color:var(--ink-soft);margin-top:3px;line-height:1.45}.bklist .bko{font-size:11.5px;color:var(--muted);margin-top:3px}.bklist .bki{display:flex;align-items:center;gap:4px;font-size:11.5px;color:var(--ink-soft);margin-top:2px;font-variant-numeric:tabular-nums;letter-spacing:.02em}.bklist .cpy{all:unset;cursor:pointer;display:inline-flex;align-items:center;gap:4px;padding:3px 4px;border-radius:4px;color:var(--muted)}.bklist .cpy:hover{color:var(--ink);background:var(--paper-2)}.bklist .cpy:focus-visible{outline:2px solid var(--teal);outline-offset:1px}.bklist .cpd{display:none;font-size:11px;color:var(--teal);font-weight:700;letter-spacing:0}.bklist .cpy.done .cpd{display:inline}
.shelf2{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:18px 14px;margin-top:6px}@media(max-width:600px){.shelf2{grid-template-columns:repeat(3,minmax(0,1fr));gap:14px 10px}}.bcard{display:flex;flex-direction:column;gap:3px;min-width:0}.bopen{all:unset;cursor:pointer;display:flex;flex-direction:column;gap:4px;min-width:0;border-radius:4px}.bopen:hover .bnm{text-decoration:underline}.bopen:focus-visible{outline:2px solid var(--teal);outline-offset:3px}.bcov{display:block;aspect-ratio:2/3;background:var(--paper-2);overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.18)}.bcov img{width:100%;height:100%;object-fit:contain;display:block}.bph{display:flex;align-items:center;justify-content:center;height:100%;padding:10px;box-sizing:border-box;text-align:center;font:400 15px/1.35 "Do Hyeon",var(--sans);color:var(--ink-soft);background:var(--paper-2)}.bnm{font-size:14px;font-weight:700;line-height:1.35;color:var(--ink);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.bau{font-size:11.5px;color:var(--muted);line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bsrc{font-size:11px;color:var(--muted);line-height:1.35}.bsrc.hot{color:var(--red);font-weight:700}.bcard .bki{display:flex;align-items:center;gap:2px;font-size:11px;color:var(--ink-soft);font-variant-numeric:tabular-nums;white-space:nowrap}.bcard .cpy,#bmodal .cpy{all:unset;cursor:pointer;display:inline-flex;align-items:center;gap:3px;padding:2px 3px;border-radius:4px;color:var(--muted)}.bcard .cpy:hover,#bmodal .cpy:hover{color:var(--ink);background:var(--paper-2)}.bcard .cpd,#bmodal .cpd{display:none;font-size:10.5px;color:var(--teal);font-weight:700}.bcard .cpy.done .cpd,#bmodal .cpy.done .cpd{display:inline}#bmodal{border:0;padding:22px 22px 18px;width:min(640px,92vw);max-height:86vh;overflow:auto;border-radius:10px;background:var(--paper-3);color:var(--ink);box-shadow:0 12px 40px rgba(0,0,0,.35)}#bmodal::backdrop{background:rgba(10,10,14,.55)}@media(max-width:600px){#bmodal{width:100vw;max-width:100vw;margin:auto 0 0;max-height:88vh;border-radius:14px 14px 0 0;padding:18px 16px 22px;animation:bup .22s ease-out}}@keyframes bup{from{transform:translateY(40px);opacity:.4}to{transform:none;opacity:1}}.bdh{display:flex;gap:16px;align-items:flex-start;position:relative}.bdc{flex:none;width:120px}.bdc img{width:100%;display:block;box-shadow:0 1px 4px rgba(0,0,0,.2)}.bdi{flex:1;min-width:0}.bdi h3{margin:0 0 6px;font-size:19px;line-height:1.35}.bdi .bkm{font-size:13px;color:var(--ink-soft);line-height:1.5}.bdi .bki{display:flex;align-items:center;gap:4px;font-size:12.5px;color:var(--ink-soft);margin-top:4px}.bbtns{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.bbtn{font-size:12.5px;border:1px solid var(--rule);border-radius:999px;padding:4px 11px;color:var(--ink);background:var(--paper)}.bbtn:hover{border-color:var(--ink)}.bclose{all:unset;cursor:pointer;position:sticky;float:right;top:0;margin:-10px -8px 0 8px;font-size:18px;color:var(--muted);padding:6px 10px;z-index:2}.bclose:hover{color:var(--ink)}#bmodal h4{margin:16px 0 4px;font-size:13px;letter-spacing:.04em;color:var(--muted)}.barts{list-style:none;margin:0;padding:0}.barts li{padding:9px 0;border-bottom:1px dotted var(--rule-dot);font-size:14px;line-height:1.5}.barts li b.ot{display:inline-block;font-size:11.5px;font-weight:800;padding:1px 7px;border-radius:4px;margin-right:6px;vertical-align:1px;color:var(--c);background:var(--cb)}.oc0{--c:#1d4ed8;--cb:#dbeafe}.oc1{--c:#b91c1c;--cb:#fee2e2}.oc2{--c:#047857;--cb:#d1fae5}.oc3{--c:#7c3aed;--cb:#ede9fe}.oc4{--c:#b45309;--cb:#fef3c7}.oc5{--c:#be185d;--cb:#fce7f3}.oc6{--c:#0e7490;--cb:#cffafe}.oc7{--c:#4d7c0f;--cb:#ecfccb}@media (prefers-color-scheme:dark){.oc0{--c:#93c5fd;--cb:#1e3a8a66}.oc1{--c:#fca5a5;--cb:#7f1d1d66}.oc2{--c:#6ee7b7;--cb:#064e3b66}.oc3{--c:#c4b5fd;--cb:#4c1d9566}.oc4{--c:#fcd34d;--cb:#78350f66}.oc5{--c:#f9a8d4;--cb:#83184366}.oc6{--c:#67e8f9;--cb:#164e6366}.oc7{--c:#bef264;--cb:#36531466}}.barts li .o{margin-left:6px;font-size:11.5px;color:var(--muted)}.barts li p{margin:3px 0 0;font-size:13px;color:var(--ink-body);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.blink{all:unset;cursor:pointer;color:var(--ink);font-weight:600}.blink:hover{text-decoration:underline}.blink.sm{font-size:12px;color:var(--teal);font-weight:700}@media(max-width:600px){.bdc{width:88px}.bdi h3{font-size:17px}}.pcity .ppress-wrap{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:4px 24px;margin-top:10px}.ppress h3{font-size:14px;margin:10px 0 4px;color:var(--ink)}.ppress h3 span{font-weight:400;color:var(--muted);font-size:12px}.ppress h3 a{font-weight:400;font-size:12px;color:var(--teal);margin-left:4px}.ppress ol{margin:0;padding-left:22px;font-size:13.5px;line-height:1.6;color:var(--ink-body)}.ppress li{padding:2px 0}.dlist .dth{flex:none;width:168px;height:108px;background:var(--paper-2);display:block;border-radius:3px;overflow:hidden}.dlist .dth img{width:100%;height:100%;object-fit:cover;display:block}@media(max-width:600px){.dlist .dth{width:104px;height:72px}}.dlist .pm.top{margin:0 0 4px}.dlist b.ot,.dside b.ot{display:inline-block;font-size:11.5px;font-weight:800;padding:1px 7px;border-radius:4px;margin-right:6px;vertical-align:1px;color:var(--c);background:var(--cb)}.dlist li[hidden]{display:none}.dside li{display:flex;align-items:baseline;gap:2px}.dside li .o{margin-left:auto}.blist .bth{width:100px!important;height:140px!important}@media(max-width:600px){.blist .bth{width:80px!important;height:112px!important}}
.brand .mark{height:.74em;width:auto;flex:none;transform:translateY(-.04em)}
.motto{margin:6px 0 0;font-size:12px;letter-spacing:.2em;color:var(--muted)}
.nav{display:flex;justify-content:center;flex-wrap:wrap;gap:6px 30px;padding:14px 0 12px;border-bottom:1px solid var(--ink);position:sticky;top:0;background:var(--paper);z-index:3}
.nav button{all:unset;cursor:pointer;font-size:16px;color:var(--ink-soft);padding:2px 1px 5px;border-bottom:2px solid transparent}
.nav button{white-space:nowrap}
@media(max-width:560px){.nav{justify-content:flex-start;flex-wrap:nowrap;overflow-x:auto;gap:22px;scrollbar-width:none}.nav::-webkit-scrollbar{display:none}
.nav.more{-webkit-mask-image:linear-gradient(90deg,#000 78%,transparent);mask-image:linear-gradient(90deg,#000 78%,transparent)}}
.dl-r .sbtn{display:inline-flex;align-items:center;gap:4px;cursor:pointer}@media(max-width:560px){.dl-r .sbtn span{display:none}}#smodal{border:0;padding:0;width:min(720px,94vw);max-height:84vh;margin:6vh auto auto;border-radius:12px;background:var(--paper-3);color:var(--ink);box-shadow:0 14px 44px rgba(0,0,0,.4);overflow:hidden;flex-direction:column}#smodal[open]{display:flex}#smodal::backdrop{background:rgba(10,10,14,.55)}@media(max-width:600px){#smodal{inset:0;width:auto;max-width:none;height:auto;max-height:none;margin:0;border-radius:0}}#sres{min-width:0}.slist li{overflow-wrap:anywhere}.shd{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--rule);color:var(--muted)}.shd input{flex:1;min-width:0;font:inherit;font-size:17px;border:0;outline:0;background:transparent;color:var(--ink)}.shd input::-webkit-search-cancel-button{cursor:pointer}.shd .sx{all:unset;cursor:pointer;font-size:13px;color:var(--muted);padding:4px 6px}.shd .sx:hover{color:var(--ink)}.sflt{padding:0 16px}.sflt .ofilter{padding:10px 0 0}.sflt .ofilter button{font-size:12.5px;padding:3px 11px}#stab:empty{display:none}.sinfo{padding:10px 16px 4px;font-size:12.5px;color:var(--muted)}#sres{overflow:auto;padding:0 16px 18px;flex:1}#sres .pday{position:sticky;top:0;background:var(--paper-3);z-index:1}.slist{list-style:none;margin:0;padding:0}.slist li{padding:9px 0;border-bottom:1px dotted var(--rule-dot);font-size:14.5px;line-height:1.5}.slist li b.ot{display:inline-block;font-size:11.5px;font-weight:800;padding:1px 7px;border-radius:4px;margin-right:7px;vertical-align:1px;color:var(--c);background:var(--cb)}.slist li a{color:var(--ink)}.slist li a:hover{text-decoration:underline}.slist li .o{margin-left:7px;font-size:11.5px;color:var(--muted);white-space:nowrap}.slist mark{background:var(--teal-tint);color:inherit;font-weight:700;padding:0 1px;border-radius:2px}.snone{margin:14px 0 0;font-size:14px}.snone a{color:var(--teal);font-weight:700}.smore{display:block;margin:14px auto 0;font:inherit;font-size:13px;border:1px solid var(--rule);border-radius:999px;padding:6px 16px;background:var(--paper);color:var(--ink);cursor:pointer}.shot{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding-top:14px}.shot b{font-size:12.5px;color:var(--muted);font-weight:400;margin-right:4px}.shot button{font:inherit;font-size:13px;border:1px solid var(--rule);border-radius:999px;padding:3px 11px;background:var(--paper);color:var(--ink);cursor:pointer}.shot button:hover{border-color:var(--ink)}.vgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px 18px;margin-top:10px}@media(max-width:900px){.vgrid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.vgrid{grid-template-columns:1fr}}.vcard{display:flex;flex-direction:column;gap:6px;color:var(--ink);min-width:0}.vcard[hidden]{display:none}.vcard:hover .vt{text-decoration:underline}.vph{aspect-ratio:4/3;background:#fff;border:1px solid var(--rule);border-radius:4px;overflow:hidden;display:flex;align-items:center;justify-content:center}.vph img{width:100%;height:100%;object-fit:contain;display:block}.vt{font-size:15px;font-weight:700;line-height:1.4}.vcat{display:flex;flex-wrap:wrap;gap:4px}.vcat span{font-size:11px;color:var(--muted);border:1px solid var(--rule);border-radius:3px;padding:0 5px}.vcard .pm{font-size:12px;color:var(--muted);display:flex;flex-wrap:wrap;align-items:center;gap:2px 6px}.vcard b.ot,.vside b.ot{display:inline-block;font-size:11px;font-weight:800;padding:1px 7px;border-radius:4px;color:var(--c);background:var(--cb)}.vcr{margin-left:auto;font-size:11px;color:var(--dim)}.vside li{display:block}.vlist li[hidden],#vl[hidden],#vlh[hidden]{display:none}#vlh{margin-top:28px}#gzoom{border:0;padding:0;background:transparent;max-width:96vw;max-height:96vh;overflow:visible}#gzoom[open]{display:flex;flex-direction:column;align-items:center;gap:10px}#gzoom::backdrop{background:rgba(8,8,12,.86)}#gzoom .bclose{position:fixed;top:12px;right:14px;float:none;margin:0;color:#fff;font-size:24px;z-index:3}.gzimg{cursor:zoom-out}.gzimg img{display:block;max-width:94vw;max-height:80vh;object-fit:contain;background:#fff;border-radius:4px;box-shadow:0 10px 40px rgba(0,0,0,.5)}.gzcap{max-width:min(860px,94vw);color:#eee;text-align:center}.gzt{font-size:16px;font-weight:700;line-height:1.45}.gzm{font-size:12.5px;color:#bbb;margin-top:4px}.gzm a{color:#7dd3c8;margin-left:6px}#gg .gph img,#vg .vph img{cursor:zoom-in}.subtabs{display:flex;gap:0;margin:16px 0 22px;border:1px solid var(--ink);border-radius:999px;overflow:hidden;width:max-content}.subtabs button{all:unset;cursor:pointer;font-size:14.5px;font-weight:700;padding:6px 20px;color:var(--ink-soft)}.subtabs button+button{border-left:1px solid var(--ink)}.subtabs button.on{background:var(--ink);color:var(--paper)}.subtabs button:focus-visible{outline:2px solid var(--teal);outline-offset:-3px}.subv[hidden]{display:none}.pday{font-size:15px;margin:18px 0 0;padding-bottom:6px;border-bottom:1px solid var(--rule);color:var(--ink)}.pday span{font-weight:400;font-size:12px;color:var(--muted);margin-left:6px}.dlist li.pr{padding:9px 0;font-size:14.5px;line-height:1.55}.dlist li.pr b.ot{margin-right:8px}.dlist li.pr a{color:var(--ink)}.jump{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:12px 0 4px}.jump::before{content:"바로 가기";flex:none;white-space:nowrap;font-size:12px;color:var(--muted);margin-right:2px}.jump button{flex:none}.jump button{font:inherit;font-size:12.5px;border:1px solid var(--rule);border-radius:999px;padding:3px 11px;background:var(--paper-3);color:var(--ink-soft);cursor:pointer;white-space:nowrap}.jump button:hover{border-color:var(--ink);color:var(--ink)}@media(max-width:600px){.jump{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}.jump::-webkit-scrollbar{display:none}}.totop{position:fixed;right:16px;bottom:18px;width:42px;height:42px;border-radius:50%;border:1px solid var(--rule);background:var(--paper-3);color:var(--ink);font-size:18px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25);opacity:0;pointer-events:none;transition:opacity .2s;z-index:4}.totop.on{opacity:1;pointer-events:auto}
.nav button.on{color:var(--ink);font-weight:800;border-bottom-color:var(--red)}
.nav button:focus-visible{outline:2px solid var(--teal);outline-offset:3px}
.ticker{display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid var(--rule);font-size:13px;color:var(--ink-body);overflow:hidden}
.tag{flex:none;background:var(--red);color:#fff;font-size:11px;font-weight:800;letter-spacing:.14em;padding:3px 9px 3px 10px;border-radius:2px}
.ticker .items{flex:1;min-width:0;white-space:nowrap;overflow-x:auto;scrollbar-width:none}.ticker .items::-webkit-scrollbar{display:none}.ticker .tk{all:unset;cursor:pointer;color:var(--ink-body)}.ticker .tk:hover{color:var(--ink);text-decoration:underline}.ticker .items.pg.on{display:block}.ticker .sep{color:var(--dim);margin:0 8px}
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
.pop .bdg{white-space:nowrap;margin-left:6px;font-size:10.5px;font-weight:800;color:var(--red);border:1px solid var(--red);border-radius:3px;padding:0 4px;vertical-align:1px}
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


ZOOM_DIALOG = ('<dialog id="gzoom" aria-label="그림 크게 보기"><button type="button" class="bclose" id="gzclose" aria-label="닫기">✕</button>'
               '<div class="gzimg"><img id="gzi" alt=""></div><div class="gzcap"><div id="gzt" class="gzt"></div>'
               '<div class="gzm"><span id="gzm"></span> <a id="gzl" target="_blank" rel="noopener noreferrer">원문 보기 →</a></div></div></dialog>')
SEARCH_ICON = '<svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/></svg>'
SEARCH_ICON_SM = SEARCH_ICON.replace('width="19" height="19"', 'width="14" height="14"')
SEARCH_BTN = f'<button type="button" class="pill sbtn" id="sopen" aria-label="기사 검색" title="기사 검색 ( / )">{SEARCH_ICON_SM}<span>검색</span></button>'
SEARCH_DIALOG = (
    '<dialog id="smodal" aria-label="기사 검색"><div class="shd">' + SEARCH_ICON
    + '<input id="sq" type="search" placeholder="기사 제목·언론사 검색" autocomplete="off" enterkeyhint="search" aria-label="검색어">'
    '<button type="button" class="sx" id="sclose" aria-label="닫기">닫기</button></div>'
    '<div class="sflt"><div class="ofilter" id="speriod" role="group" aria-label="기간"><button class="on" data-d="15">15일</button>'
    '<button data-d="7">7일</button><button data-d="3">3일</button><button data-d="1">오늘</button></div>'
    '<div class="ofilter" id="stab" role="group" aria-label="분야"></div></div>'
    '<div id="sinfo" class="sinfo" aria-live="polite"></div><div id="sres"></div></dialog>')

JS = """
const tabs=document.querySelectorAll('.nav button[role=tab]'),pgs=document.querySelectorAll('.pg');
function show(c){tabs.forEach(b=>{const on=b.dataset.c===c;b.classList.toggle('on',on);b.setAttribute('aria-selected',on)});
pgs.forEach(p=>p.classList.toggle('on',p.dataset.c===c));try{localStorage.setItem('trend_tab',c)}catch(e){}
const b=[...tabs].find(x=>x.dataset.c===c),n=b&&b.parentNode;  // 좁은 화면: 고른 탭이 보이게 가운데로
if(n&&n.scrollWidth>n.clientWidth)n.scrollTo({left:b.offsetLeft-n.offsetLeft-n.clientWidth/2+b.offsetWidth/2});}
const nav=document.querySelector('.nav');  // 오른쪽에 탭이 더 있으면 끝을 흐리게
function navFade(){if(nav)nav.classList.toggle('more',nav.scrollLeft+nav.clientWidth<nav.scrollWidth-4)}
if(nav){nav.addEventListener('scroll',navFade,{passive:true});addEventListener('resize',navFade)}
tabs.forEach(b=>b.onclick=()=>show(b.dataset.c));
let c='전체';try{c=localStorage.getItem('trend_tab')||'전체'}catch(e){}
if(location.hash==='#home'){c='전체';try{history.replaceState(null,'',location.pathname+location.search)}catch(e){}}
document.querySelectorAll('a.home').forEach(a=>a.addEventListener('click',e=>{if(!tabs.length||![...tabs].some(b=>b.dataset.c==='전체'))return;
e.preventDefault();const sm=document.getElementById('smodal');if(sm&&sm.open)sm.close();show('전체');scrollTo({top:0,behavior:'smooth'})}));
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
document.addEventListener('click',async e=>{const b=e.target.closest('.cpy');if(!b)return;e.stopPropagation();const v=b.dataset.copy;
try{await navigator.clipboard.writeText(v)}catch(e){const t=document.createElement('textarea');t.value=v;document.body.appendChild(t);t.select();try{document.execCommand('copy')}catch(_){}t.remove()}
b.classList.add('done');setTimeout(()=>b.classList.remove('done'),1500)});
const bm=document.getElementById('bmodal');
function openBook(k){const t=document.getElementById('bd'+k);if(!bm||!t)return;const body=document.getElementById('bmbody');
body.innerHTML='';body.appendChild(t.content.cloneNode(true));bm.scrollTop=0;
if(typeof bm.showModal==='function'){if(!bm.open)bm.showModal()}else{bm.setAttribute('open','')}}
document.addEventListener('click',e=>{const b=e.target.closest('[data-book]');if(b){e.preventDefault();openBook(b.dataset.book)}});
if(bm){bm.querySelector('.bclose').addEventListener('click',()=>bm.close());bm.addEventListener('click',e=>{if(e.target===bm)bm.close()})}
document.querySelectorAll('#bsort button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#bsort button').forEach(x=>x.classList.toggle('on',x===b));
const sh=document.getElementById('shelf');const cs=[...sh.querySelectorAll('.bcard')];const by=b.dataset.s;
cs.sort((p,q)=>by==='d'?q.dataset.d.localeCompare(p.dataset.d):(q.dataset.n-p.dataset.n)||q.dataset.d.localeCompare(p.dataset.d));cs.forEach(c=>sh.appendChild(c))});
document.querySelectorAll('#bcat button').forEach(b=>b.onclick=()=>{const k=b.dataset.k;
document.querySelectorAll('#bcat button').forEach(x=>x.classList.toggle('on',x===b));
document.querySelectorAll('#bl>li').forEach(li=>li.hidden=k?li.dataset.k!==k:li.dataset.k==='기타')});
(function(){const dlg=document.getElementById('smodal'),btn=document.getElementById('sopen');if(!dlg||!btn)return;
const q=document.getElementById('sq'),info=document.getElementById('sinfo'),res=document.getElementById('sres'),tb=document.getElementById('stab');
let D=null,H=null,loading=null,days=15,tab=-1,limit=200,timer=0;const W='일월화수목금토';
const esc=t=>t.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){if(D)return D;if(!loading)loading=fetch('search.json?v='+encodeURIComponent(document.body.dataset.built||'')).then(r=>r.json()).then(d=>{D=d;
H=d.r.map(r=>{const h=(r[0]+' '+d.o[r[1]]).toLowerCase();return[h,h.replace(/\\s+/g,'')]});return d});return loading}
function kst(m){return new Date((m+540)*60000)}
function hl(t,terms){let h=esc(t);terms.forEach(w=>{if(!w)return;const re=new RegExp(w.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&'),'gi');h=h.replace(re,x=>'<mark>'+x+'</mark>')});return h}
function hot(){const hs=(D&&D.hot)||[];return hs.length?'<div class="shot"><b>지금 뜨는 말</b>'+hs.map(k=>'<button type="button" data-k="'+esc(k)+'">'+esc(k)+'</button>').join('')+'</div>':''}
function run(){if(!D){info.textContent='검색 목록을 불러오는 중…';load().then(run).catch(()=>{info.textContent='검색 목록을 불러오지 못했습니다. 잠시 뒤 다시 시도해 주세요.'});return}
const raw=q.value.trim();const terms=raw.toLowerCase().split(/\\s+/).filter(Boolean);
try{history.replaceState(null,'',raw?'#q='+encodeURIComponent(raw):location.pathname+location.search)}catch(e){}
if(!terms.length){tb.innerHTML='';res.innerHTML=hot();info.textContent='최근 '+D.days+'일 기사 '+D.r.length.toLocaleString()+'건에서 찾습니다. 낱말 여러 개는 띄어 쓰면 모두 들어간 기사만 나옵니다.';return}
const lo=days===1?(()=>{const n=kst(Math.floor(Date.now()/60000));return Math.floor(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate())/60000)-540})():Math.floor(Date.now()/60000)-days*1440;
const hits=[];for(let i=0;i<D.r.length;i++){const r=D.r[i];if(r[3]<lo)continue;const h=H[i];if(terms.every(w=>h[0].includes(w)||h[1].includes(w.replace(/\\s+/g,''))))hits.push(r)}
const cnt={};hits.forEach(r=>cnt[r[2]]=(cnt[r[2]]||0)+1);if(tab>=0&&!cnt[tab])tab=-1;
tb.innerHTML=hits.length?'<button class="'+(tab<0?'on':'')+'" data-t="-1">전체<i>'+hits.length+'</i></button>'+D.t.map((t,i)=>cnt[i]?'<button class="'+(tab===i?'on':'')+'" data-t="'+i+'">'+esc(t)+'<i>'+cnt[i]+'</i></button>':'').join(''):'';
const list=tab<0?hits:hits.filter(r=>r[2]===tab);
info.textContent=list.length?('‘'+raw+'’ '+list.length.toLocaleString()+'건'+(list.length>limit?' · 최근 '+limit+'건 표시':'')):('‘'+raw+'’ 기사가 최근 '+(days===1?'오늘':days+'일')+' 안에 없습니다.');
let out='',cur='';list.slice(0,limit).forEach(r=>{const d=kst(r[3]),day=(d.getUTCMonth()+1)+'.'+d.getUTCDate()+' ('+W[d.getUTCDay()]+')';
if(day!==cur){out+=(cur?'</ul>':'')+'<h3 class="pday">'+day+'</h3><ul class="slist">';cur=day}
const u=r[4].startsWith('g:')?'https://news.google.com/rss/articles/'+r[4].slice(2)+'?oc=5':/^https?:/.test(r[4])?r[4]:'https://'+r[4];
out+='<li><b class="ot oc'+D.oc[r[1]]+'">'+esc(D.o[r[1]])+'</b><a href="'+esc(u)+'" target="_blank" rel="noopener noreferrer">'+hl(r[0],terms)+'</a><span class="o">'+String(d.getUTCHours()).padStart(2,'0')+':'+String(d.getUTCMinutes()).padStart(2,'0')+' · '+esc(D.t[r[2]])+'</span></li>'});
if(cur)out+='</ul>';if(list.length>limit)out+='<button type="button" class="smore">더 보기 ('+(list.length-limit).toLocaleString()+'건 더)</button>';
res.innerHTML=out||('<p class="snone"><a href="https://news.google.com/search?q='+encodeURIComponent(raw)+'&hl=ko&gl=KR&ceid=KR:ko" target="_blank" rel="noopener noreferrer">‘'+esc(raw)+'’ 구글 뉴스에서 찾아보기 →</a></p>'+hot())}
function open(v){if(typeof v==='string')q.value=v;if(!dlg.open){if(dlg.showModal)dlg.showModal();else dlg.setAttribute('open','')}limit=200;run();setTimeout(()=>q.focus(),30)}
btn.onclick=()=>open();document.getElementById('sclose').onclick=()=>dlg.close();
dlg.addEventListener('click',e=>{if(e.target===dlg)dlg.close()});
dlg.addEventListener('close',()=>{try{history.replaceState(null,'',location.pathname+location.search)}catch(e){}});
q.addEventListener('input',()=>{clearTimeout(timer);limit=200;timer=setTimeout(run,120)});
document.getElementById('speriod').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;days=+b.dataset.d;
[...b.parentNode.children].forEach(x=>x.classList.toggle('on',x===b));limit=200;run()});
tb.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;tab=+b.dataset.t;limit=200;run()});
res.addEventListener('click',e=>{const k=e.target.closest('.shot button');if(k){q.value=k.dataset.k;run();return}
if(e.target.closest('.smore')){limit+=200;run()}});
addEventListener('keydown',e=>{if(e.key==='/'&&!dlg.open&&!/INPUT|TEXTAREA/.test((document.activeElement||{}).tagName||'')){e.preventDefault();open()}});
const m=location.hash.match(/^#q=(.*)$/);if(m)open(decodeURIComponent(m[1]));
document.addEventListener('click',e=>{const k=e.target.closest('.ticker .tk');if(k)open(k.dataset.k)});})();
(function(){const nav=document.querySelector('.nav');const off=()=>(nav?nav.offsetHeight:0)+10;
const go=el=>scrollTo({top:el.getBoundingClientRect().top+scrollY-off(),behavior:'smooth'});
const lab=t=>t.replace(/\\s*·.*$/,'').replace(/^언론사 \\d+곳 이상이 쓴 낱말/,'많이 쓴 낱말').replace(/^시간대별 새 기사/,'시간대별 기사');
document.querySelectorAll('.pg[role=tabpanel]').forEach(p=>{const subs=[...p.querySelectorAll(':scope > .subv')];
(subs.length?subs:[p]).forEach(box=>{const t=[];
const lead=box.querySelector(':scope > article');if(lead)t.push([lead,p.dataset.c==='전체'?'오늘의 사건':'사건 묶음']);
box.querySelectorAll('section').forEach(s=>{if(s.closest('.subv, .pg[role=tabpanel]')!==box)return;const h=s.querySelector('.shead h2');if(h)t.push([s,lab(h.innerText)])});
if(t.length<2)return;const j=document.createElement('nav');j.className='jump';j.setAttribute('aria-label','이 탭 안에서 바로 가기');
t.forEach(([el,l])=>{const b=document.createElement('button');b.type='button';b.textContent=l;b.onclick=()=>go(el);j.appendChild(b)});box.prepend(j)});
const st=p.querySelector(':scope > .subtabs');if(st){const bs=[...st.querySelectorAll('button')];
const show=v=>{bs.forEach(b=>{const on=b.dataset.v===v;b.classList.toggle('on',on);b.setAttribute('aria-selected',on)});subs.forEach(x=>x.hidden=x.dataset.v!==v);
try{localStorage.setItem('sub_'+p.dataset.c,v)}catch(e){}};
bs.forEach(b=>b.onclick=()=>{show(b.dataset.v);if(st.getBoundingClientRect().top<0)go(st)});
try{const v=localStorage.getItem('sub_'+p.dataset.c);if(v&&bs.some(b=>b.dataset.v===v))show(v)}catch(e){}}});
const up=document.createElement('button');up.type='button';up.className='totop';up.setAttribute('aria-label','맨 위로');up.textContent='↑';
up.onclick=()=>scrollTo({top:0,behavior:'smooth'});document.body.appendChild(up);
addEventListener('scroll',()=>up.classList.toggle('on',scrollY>700),{passive:true})})();
document.querySelectorAll('#vf button').forEach(b=>b.onclick=()=>{const o=b.dataset.o;
document.querySelectorAll('#vf button').forEach(x=>x.classList.toggle('on',x===b));
document.querySelectorAll('#vg>a,#vl>li').forEach(a=>a.hidden=!!o&&a.dataset.o!==o);
const vl=document.getElementById('vl'),vh=document.getElementById('vlh');if(vl&&vh){const any=[...vl.children].some(li=>!li.hidden);vl.hidden=!any;vh.hidden=!any}});
document.querySelectorAll('#dcat button').forEach(b=>b.onclick=()=>{const k=b.dataset.k;
document.querySelectorAll('#dcat button').forEach(x=>x.classList.toggle('on',x===b));
document.querySelectorAll('#dl>li').forEach(li=>li.hidden=!!k&&li.dataset.k!==k)});
document.querySelectorAll('#if button').forEach(b=>b.onclick=()=>{const o=b.dataset.o;
document.querySelectorAll('#if button').forEach(x=>x.classList.toggle('on',x===b));
document.querySelectorAll('#ig>a').forEach(a=>a.style.display=(!o||a.dataset.o===o)?'':'none')});
function gfilter(o){window._go=o||'';document.querySelectorAll('#gg>a').forEach(a=>a.style.display=(!o||a.dataset.o===o)?'':'none');
let n=0;document.querySelectorAll('#gl li').forEach(li=>{const v=li.dataset.img==='0'&&(!o||li.dataset.o===o);li.style.display=v?'':'none';if(v)n++});
const gl=document.getElementById('gl');if(gl)gl.style.display=n?'':'none';}
(function(){const z=document.getElementById('gzoom');if(!z)return;const zi=document.getElementById('gzi');
// 더 큰 그림 후보: 연합 _P1→_P4(원본), 기사 CMS 썸네일(/thumbnail/…_v150.jpg)→원본(/photo/….jpg, .png). 다 안 되면 원래 그림
const cands=u=>{const out=[];if(/_P[123]\\.jpg/i.test(u))out.push(u.replace(/_P[123]\\.jpg/i,'_P4.jpg'));
if(/ourworldindata\\.org\\/cdn-cgi\\/imagedelivery\\/.*\\/w=\\d+$/.test(u))out.push(u.replace(/\\/w=\\d+$/,'/w=1350'));
const m=u.match(/^(.*)\\/news\\/thumbnail\\/(\\d+)\\/([\\w-]+?)_v\\d+\\.(jpg|jpeg|png|gif)/i);if(m){const base=m[1]+'/news/photo/'+m[2]+'/'+m[3];out.push(base+'.'+m[4],base+'.png',base+'.jpg')}
return [...new Set(out),u]};
document.addEventListener('click',e=>{const img=e.target.closest('#gg .gph img, #vg .vph img');if(!img)return;e.preventDefault();
const a=img.closest('a');const list=cands(img.currentSrc||img.src);let k=0;zi.removeAttribute('src');zi.onerror=()=>{if(++k<list.length)zi.src=list[k]};zi.src=list[0];
zi.alt=(a.querySelector('.pt, .vt')?.textContent||'').trim();document.getElementById('gzt').textContent=zi.alt;
const pm=a.querySelector('.pm');document.getElementById('gzm').textContent=pm?[...pm.childNodes].map(n=>n.textContent.replace(/\\s+/g,' ').trim()).filter(x=>x&&x!=='·').join(' · '):'';document.getElementById('gzl').href=a.href;
if(z.showModal)z.showModal();else z.setAttribute('open','')});
document.getElementById('gzclose').onclick=()=>z.close();z.addEventListener('click',e=>{if(e.target===z||e.target.closest('.gzimg'))z.close()});
z.addEventListener('close',()=>{if(!z.open)zi.onerror=null})})();
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
        out.append(f'<div class="tr"><div class="no">{i}</div><div><div class="kw">{esc(t["keyword"])}{tf}{started(t, now) if now else ""}</div><ul>{news}</ul>{rel}</div></div>')
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
            f'<p class="note">그림을 누르면 크게 볼 수 있고, 제목을 누르면 원문 기사로 갑니다. {more}</p></section></div>')


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
        if name == DAUM_TREND:
            if len({r.get("badge") for r in rows}) == 1:  # 모두 '신규'처럼 같으면 표시가 의미 없으니 뺀다
                rows = [dict(r, badge="") for r in rows]
            body = ('<div class="pop"><ol>' + "".join(
                f"<li><span>{link(r['url'], r['title'])}" + (f"<span class='bdg'>{esc(r['badge'])}</span>" if r.get("badge") else "") + "</span></li>"
                for r in rows) + '</ol><p class="note">다음 집계 · 다음 검색에서 지금 많이 찾는 말 (베타)</p></div>')
            en = "DAUM TRENDS"
        elif name == DAUM_MAIN:
            body = ('<div class="pop"><ul>' + "".join(
                f"<li>{link(r['url'], r['title'])}<span class='o'>{esc(r.get('outlet', ''))}</span></li>" for r in rows)
                + '</ul><p class="note">다음뉴스 첫 화면의 주요뉴스 · 누르면 다음뉴스 기사로 갑니다</p></div>')
            en = "DAUM"
        elif "키워드" in name:
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
                brand="지금 뜨는 뉴스", motto=None, title=None, books=None, book_info=None, deep=None, paju_city=None, visual=None):
    ok = sum(1 for s in status if s["ok"])
    if only:  # 단독 페이지 (예: 주식 → stock.html)
        pages = {only: pages[only]}
        graphic, portals, interactive, books, deep, visual = None, {}, None, None, None, None
        nitems = sum(c for _, c in pages[only].get("hourly", [])) or nitems
    gpanel = render_deep(deep or [], now) + render_visual(visual or []) + render_books(books or [], now, book_info) + render_graphic(graphic) + render_interactive(interactive or [])
    first_niche = next((x for x in NICHE if x in pages), None)  # 관심 탭 앞에 구분선
    tabs = "".join(f'<button role="tab" data-c="{esc(c)}"' + (' class="gtab"' if c == first_niche else "") + f'>{esc(c)}</button>'
                   for c in CATEGORIES if c in pages)
    if gpanel:
        tabs += f'<button role="tab" data-c="{DEEP_TAB}" class="gtab">{DEEP_TAB}</button>' if deep else ""
        tabs += f'<button role="tab" data-c="{VISUAL_TAB}"{"" if deep else " class=gtab"}>{VISUAL_TAB}</button>' if visual else ""
        tabs += f'<button role="tab" data-c="{BOOK_TAB}"{"" if deep or visual else " class=gtab"}>{BOOK_TAB}</button>' if books else ""
        tabs += ('<button role="tab" data-c="그래픽">그래픽</button>' if books else '<button role="tab" data-c="그래픽" class="gtab">그래픽</button>') if graphic else ""
        tabs += '<button role="tab" data-c="인터랙티브">인터랙티브</button>' if interactive else ""
    if only:
        tabs = f'<button role="tab" data-c="{esc(only)}" style="display:none">{esc(only)}</button><a class="srclink" href="index.html">← 전체 뉴스</a>'
    else:
        tabs += '<a class="srclink" href="sources.html">출처</a>'
    def tr_of(c):
        return pages[c].get("trends") if pages[c].get("trends") is not None else trends
    tick_keys = list(pages) + ([DEEP_TAB] if deep else []) + ([VISUAL_TAB] if visual else []) + ([BOOK_TAB] if books else []) + (["그래픽"] if graphic else []) + (["인터랙티브"] if interactive else [])
    def tk(c):
        src = tr_of(c if c in pages else next(iter(pages)))
        kws = [t["keyword"] for t in src]
        if c == "전체" or c not in pages:  # 전체: 구글 급상승에 다음 실시간 트렌드·줌 이슈 키워드를 더해 최대 12개(한 줄)
            for name in (DAUM_TREND, "줌 이슈 키워드"):
                kws += [r["title"] for r in (portals or {}).get(name, []) if r.get("title")]
        seen, uniq = set(), []
        for k in kws:
            kk = re.sub(r"\s+", "", k).lower()
            if kk and kk not in seen:
                seen.add(kk)
                uniq.append(k)
        return (f'<span class="items pg" data-c="{esc(c)}">' + '<span class="sep">/</span>'.join(
            f'<button type="button" class="tk" data-k="{esc(k)}" title="‘{esc(k)}’ 기사 검색">{esc(k)}</button>' for k in uniq[:12]) + "</span>")
    ticker = '<div class="ticker"><span class="tag">급상승</span>' + "".join(tk(c) for c in tick_keys) + "</div>"
    body = []
    for c, p in pages.items():
        label = "오늘의 사건" if c == "전체" else ("증시" if c == "주식" else c)
        main = (f'{render_clusters(p["clusters"], now, label, render_photos(p, c))}{render_timeline(p, now, c)}'
                f'<section class="shelf"><div class="shead"><h2>언론사 {2 if c in NICHE else 3}곳 이상이 쓴 낱말{"" if c == "전체" else " · " + esc(c)}</h2>'
                f'<span class="en">KEYWORDS</span></div>{render_keywords(p["keywords"])}</section>')
        city = render_paju_city(paju_city) if c == "파주" else ""
        if city:  # 파주 탭: 신문뉴스 | 파주뉴스(파주시청) 전환
            main = ('<div class="subtabs" role="tablist" aria-label="파주 소식 보기"><button type="button" role="tab" class="on" aria-selected="true" data-v="news">신문뉴스</button>'
                    '<button type="button" role="tab" aria-selected="false" data-v="city">파주뉴스</button></div>'
                    f'<div class="subv" data-v="news">{main}</div><div class="subv" data-v="city" hidden>{city}</div>')
        body.append(f'<div class="pg" data-c="{esc(c)}" role="tabpanel">{main}</div>')
    def side_tr(c):
        src = tr_of(c if c in pages else next(iter(pages)))
        first = c == "전체" or c not in pages
        h = "급상승 검색어" if first else f"급상승 · {c}"
        en = "GOOGLE TRENDS" if first else "검색어 + 기사 속 낱말"
        note = "" if first else '<p class="note">구글 급상승 검색어 중 이 분야 기사에 나온 것, 그리고 최근 3시간 이 분야 기사에서 갑자기 많이 쓰인 낱말입니다.</p>'
        return (f'<section class="pg" data-c="{esc(c)}"><div class="shead"><h2>{esc(h)}</h2><span class="en">{en}</span></div>'
                f'{render_trends(src, now)}{note}</section>')
    side = "".join(render_book_side(books, book_info) if c == BOOK_TAB else render_deep_side(deep) if c == DEEP_TAB else render_visual_side() if c == VISUAL_TAB else side_tr(c)
                   for c in tick_keys) + render_portals(portals)
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
<div class="dateline"><span>{date} · <span id="age"></span></span><span class="dl-r"><button class="pill rf" id="rf" type="button" title="새 데이터가 있으면 바로 다시 불러옵니다">↻ 새로고침</button><button class="pill" id="st" type="button">수집 {ok}/{len(status)}</button>{"" if only else SEARCH_BTN}</span></div>
<header class="titlerow"><h1 class="brand"><a class="home" href="index.html#home" title="첫 화면으로">{LOGO_SVG}<span>{esc(brand)}</span></a></h1><p class="motto">{esc(motto or f"여러 언론사가 함께 다룬 순서 · 최근 {hours}시간")}</p></header>
<nav class="nav" role="tablist">{tabs}</nav>{"" if only else SEARCH_DIALOG + ZOOM_DIALOG}
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
EXCLUDED = [("네이트 뉴스 랭킹", "robots.txt가 모든 수집기를 차단(User-agent: * Disallow: /) — 검색엔진만 허용", "2026-10-24 재확인"),
            ("네이버 뉴스 랭킹", "robots.txt가 모든 수집기를 차단(User-agent: * Disallow: /)", "2026-10-19"),
            ("네이버 뉴스 검색 API", "개발자센터 신규 신청 종료(2026-07-30), 바뀐 약관(9-07)이 결과 저장·캐싱·AI 입력 금지", "2026-10-24"),
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
        ("다음", "실시간 트렌드(검색어 10)", "다음뉴스 첫 화면", "news.daum.net", DAUM_TREND),
        ("다음", "이 시각 주요뉴스(기사 10)", "다음뉴스 첫 화면", "news.daum.net", DAUM_MAIN),
        ("연합뉴스", "그래픽 목록", "목록 페이지", "yna.co.kr/graphic/index", "연합뉴스 그래픽"),
        ("뉴시스", "그래픽뉴스 목록", "목록 페이지", "newsis.com/photo/gralist", "뉴시스 그래픽"),
        ("경향신문", "인터랙티브 목록(1~2쪽)", "목록 페이지", "khan.co.kr/interactive", "경향신문 인터랙티브"),
        ("한국일보", "인터랙티브 목록(최근 24건)", "목록 페이지 속 데이터", "hankookilbo.com/interactive", "한국일보 인터랙티브"),
        ("조선일보", "인터랙티브 목록(최근 12건, 날짜는 주소의 연·월)", "목록 페이지", "chosun.com/interactive", "조선일보 인터랙티브"),
        ("동아일보", "책책책: 책의 향기 (책 전용 RSS)", "RSS", "rss.donga.com/book.xml", "동아일보·책"),
        ("조선일보", "책책책: 문화·라이프 RSS 중 책 섹션 기사", "RSS", "chosun.com …/culture-life/", "조선일보·책"),
        ("한겨레", "책책책: 문화 RSS 중 '책과 생각' 기사", "RSS", "hani.co.kr/rss/culture/", "한겨레·책"),
        ("경향신문", "책책책: 문화 RSS 중 [책과 삶]·[신간] 등", "RSS", "khan.co.kr …/culture_news.xml", "경향신문·책"),
        ("연합뉴스", "책책책: 문화 RSS 중 [신간]·출간 등", "RSS", "yna.co.kr/rss/culture.xml", "연합뉴스·책"),
        ("독서신문", "책책책: 전체 기사 (책 전문지)", "RSS", "readersnews.com/rss/allArticle.xml", "독서신문·책"),
        ("예스24", "책책책 '이번 주 소개된 책'의 표지·저자·출판사·출간 연월 (책마다 한 번 검색, 저장)", "도서 검색 페이지", "yes24.com/Product/Search", "책 정보(예스24 검색)"),
        ("교보문고", "책책책 '이번 주 소개된 책'의 ISBN (책마다 한 번 검색, 저장)", "도서 검색 페이지", "search.kyobobook.co.kr", "책 정보(예스24 검색)"),
        ("구글뉴스", "책책책: 제목에 신간·새책·책꽂이가 든 주요 언론사 기사", "검색 RSS", "news.google.com/rss/search", "구글뉴스 신간 검색(intitle:신간 when:3d)"),
        ("구글뉴스", "제목에 [그래픽]이 들어간 최근 2일 기사", "검색 RSS", "news.google.com/rss/search", "그래픽 검색(구글뉴스)"),
        ("각 언론사", "사진 없는 그래픽 기사의 원문 대표 사진(og:image)", "구글 링크를 원문 주소로 푼 뒤 원문 페이지", "언론사별 기사 주소", "그래픽 원문 사진"),
        ("각 언론사", "파주 탭 상위 15건의 원문 대표 사진(og:image)", "구글 링크를 원문 주소로 푼 뒤 원문 페이지", "언론사별 기사 주소", "관심 탭 원문 사진"),
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
<header class="titlerow"><h1 class="brand" style="font-size:clamp(32px,5.5vw,48px)"><a class="home" href="index.html#home" title="첫 화면으로">{LOGO_SVG}<span>뉴스 출처</span></a></h1><p class="motto">어느 신문사에서 무엇을 가져오는지</p></header>
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


# ── 검색: 최근 15일 기사 목록(site/search.json) ──
# 매시간 지금 화면의 기사(사건 묶음 링크·책·맥락·탐사·파주시청)를 history/search_index.json에 모아 두고 15일이 지난 것은 뺀다.
# 처음 한 번은 history/의 시간별 기록(최대 7일)으로 채운다. 날짜는 그 기사가 처음 보인 시각과 사건 묶음 최근 시각 중 이른 쪽.
SEARCH_INDEX = os.path.join(HERE, "history", "search_index.json")
SEARCH_DAYS = 15


def _search_rows(d):
    """data.json 하나 → {url: [제목, 언론사, ISO 시각, 탭]}"""
    built = d.get("built") or ""
    out = {}

    def add(url, title, outlet, date, tab):
        if not url or not title or not date:
            return
        r = out.get(url)
        if r:
            if date < r[2]:
                r[2] = date
            if r[3] == "전체" and tab != "전체":
                r[3] = tab
            return
        title = re.sub(r"\s*[-|]\s*(?:https?://|www\.)\S+\s*$", "", re.sub(r"\s+", " ", title)).strip()  # 끝에 붙은 주소 꼬리 제거
        out[url] = [title, outlet or "", date, tab]
    for c, p in (d.get("pages") or {}).items():
        for cl in p.get("clusters", []):
            dt = min(x for x in (cl.get("latest") or built, built) if x)
            for l in cl.get("links", []):
                add(l.get("url"), l.get("title"), l.get("outlet"), dt, c)
    for x in d.get("books") or []:
        add(x.get("url"), x.get("title"), x.get("outlet"), x.get("date"), BOOK_TAB)
    for x in d.get("deep") or []:
        add(x.get("url"), x.get("title"), x.get("outlet"), x.get("date"), DEEP_TAB)
    for x in d.get("visual") or []:
        add(x.get("url"), x.get("title"), x.get("outlet"), x.get("date"), VISUAL_TAB)
    pc = d.get("paju_city") or {}
    for x in pc.get("focus", []):
        if x.get("date"):
            add(x.get("url"), x.get("title"), "파주시청", x["date"] + "T09:00:00+09:00", "파주")
    for pr in pc.get("press", []):
        for k, t in enumerate(pr.get("titles", [])):
            add(pr["url"] + f"#{k + 1}", t, "파주시청", f'{pr["date"]}T{pr.get("time") or "09:00"}:00+09:00', "파주")
    return out


def build_search(data, now, out_dir, persist=True):
    """→ 검색 목록 기사 수. site/search.json = {built, days, o:[언론사], oc:[색 번호], t:[탭], hot:[급상승], r:[[제목, 언론사 번호, 탭 번호, 분(유닉스), 주소]]}"""
    idx = {}
    if persist:
        try:
            idx = json.load(open(SEARCH_INDEX, encoding="utf-8"))
        except Exception:  # 처음: 시간별 기록으로 채우기
            hd = os.path.join(HERE, "history")
            for fn in sorted(os.listdir(hd)) if os.path.isdir(hd) else []:
                if re.match(r"20\d\d-\d\d-\d\d-\d\d\.json$", fn):
                    try:
                        for u, r in _search_rows(json.load(open(os.path.join(hd, fn), encoding="utf-8"))).items():
                            if u not in idx or r[2] < idx[u][2]:
                                idx[u] = r if u not in idx else [idx[u][0], idx[u][1], r[2], idx[u][3]]
                    except Exception:
                        pass
    for r in idx.values():  # 탭 이름 바뀐 것(2026-10-29 비쥬얼뉴스 → 오늘의 차트)
        if len(r) > 3 and r[3] == "비쥬얼뉴스":
            r[3] = VISUAL_TAB
    for u, r in _search_rows(data).items():
        if u in idx:
            old = idx[u]
            idx[u] = [old[0], old[1], min(old[2], r[2]), r[3] if old[3] == "전체" else old[3]]
        else:
            idx[u] = r
    cutoff = (now - timedelta(days=SEARCH_DAYS)).isoformat()
    lo = now - timedelta(days=SEARCH_DAYS)
    keep = {}
    for u, r in idx.items():
        try:
            if datetime.fromisoformat(r[2]) >= lo:
                keep[u] = r
        except Exception:
            pass
    if persist:
        try:
            os.makedirs(os.path.dirname(SEARCH_INDEX), exist_ok=True)
            json.dump(keep, open(SEARCH_INDEX, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass
    seen, rows = set(), []
    for u, r in sorted(keep.items(), key=lambda kv: kv[1][2], reverse=True):
        k = (r[1], re.sub(r"\W", "", r[0]))
        if k in seen:  # 같은 언론사·같은 제목(주소만 다른 것)은 하나만
            continue
        seen.add(k)
        rows.append((u, r))
    outlets = sorted({r[1] for _, r in rows})
    for _, r in rows:  # '전체' 탭에만 있던 기사는 검색 화면에서 '종합'으로(분야 버튼의 '전체'와 헷갈리지 않게)
        if r[3] == "전체":
            r[3] = "종합"
    tabs = [c for c in ["종합"] + CATEGORIES[1:] if any(r[3] == c for _, r in rows)] + [t for t in (DEEP_TAB, VISUAL_TAB, BOOK_TAB) if any(r[3] == t for _, r in rows)]
    oi, ti = {o: i for i, o in enumerate(outlets)}, {t: i for i, t in enumerate(tabs)}
    try:
        gnc = json.load(open(GN_CACHE, encoding="utf-8"))
    except Exception:
        gnc = {}

    def short(u):  # 주소 줄이기: 구글뉴스는 원문 주소(풀어 둔 것) 또는 'g:아이디', 나머지는 https:// 빼기
        u = u.split("#")[0]
        m = GN_RX.search(u)
        if m:
            o = (gnc.get(m.group(1)) or {}).get("url")
            return re.sub(r"^https://", "", o) if o else "g:" + m.group(1)
        return re.sub(r"^https://", "", u)
    out = {"built": now.isoformat(), "days": SEARCH_DAYS, "o": outlets, "oc": [outlet_color(o) for o in outlets], "t": tabs,
           "hot": [t["keyword"] for t in (data.get("trends") or [])[:8]],
           "r": [[r[0], oi[r[1]], ti.get(r[3], 0), int(datetime.fromisoformat(r[2]).timestamp() // 60), short(u)] for u, r in rows if r[3] in ti]}
    with open(os.path.join(out_dir, "search.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    return len(out["r"])


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
                           interactive=d.get("interactive"), books=d.get("books"), book_info=d.get("book_info"), deep=d.get("deep"), paju_city=d.get("paju_city"), visual=d.get("visual"))
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
        book_info = {}
        deep = []
        visual = [{"title": "Sample: The World's Biggest Economies", "url": "https://www.visualcapitalist.com/", "outlet": "Visual Capitalist",
                   "img": None, "date": now.isoformat(), "cats": ["Economy"]}]
        paju_city = {"focus": [{"title": "파주시, 샘플 주요뉴스", "url": "https://www.paju.go.kr/news/index.do", "img": None,
                                "date": now.strftime("%Y-%m-%d"), "desc": "파주시가 샘플 소식을 알렸다."}],
                     "press": [{"date": now.strftime("%Y-%m-%d"), "url": "https://www.paju.go.kr/news/index.do",
                                "titles": ["파주시, 샘플 보도자료 하나", "파주시, 샘플 보도자료 둘"]}]}
        books = [{"title": f"[신간] 샘플 책 {i}", "url": f"https://example.com/b{i}", "outlet": ("동아일보", "한겨레", "독서신문")[i % 3],
                  "img": yna_g[i % 6]["img"] if i % 2 else None, "date": (now - timedelta(hours=5 * i)).isoformat()} for i in range(7)]
    else:
        fp = os.path.join(HERE, "feeds.json")
        feeds = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else []
        if not feeds:
            sys.exit("feeds.json이 없습니다.")
        items, status = [], []
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
        trends, terr = fetch_trends()
        status.append({"name": "구글 트렌드", "ok": terr is None, "count": len(trends), "error": terr or ""})
        portals, pstatus = {}, []
        if not args.no_portal:
            daum = fetch_daum()
            for name, rows, err in [daum[0]] + fetch_zum() + [daum[1]]:  # 다음 트렌드 → 줌 → 다음 주요뉴스 순으로 보인다
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
        books, bstatus = fetch_books(now)
        pstatus += bstatus
        n_todo, n_desc = enrich_books(books)
        book_info = enrich_book_titles(books)
        pstatus.append({"name": "책 정보(예스24 검색)", "ok": bool(book_info), "count": len(book_info), "error": "" if book_info else "0권"})
        pstatus.append({"name": "책책책 원문 소개글", "ok": True, "count": n_desc, "error": "" if n_desc or not n_todo else f"{n_todo}건 중 0"})
        paju_city, cstatus = fetch_paju_city()
        pstatus += cstatus
        visual, vstatus = fetch_visual(now)
        pstatus += vstatus
        deep, dstatus = fetch_deep(now)
        pstatus += dstatus
        n_todo, n_dimg = enrich_books(deep, path=DEEP_META, need_img=True)
        pstatus.append({"name": "맥락·탐사 원문 사진·소개글", "ok": True, "count": n_dimg, "error": ""})
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
                       interactive=inter, books=books, book_info=book_info, deep=deep, paju_city=paju_city, visual=visual)
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
            "portals": portals, "graphic": graphic, "interactive": inter, "books": books, "book_info": book_info, "deep": deep, "paju_city": paju_city, "visual": visual, "feeds": allstatus}
    with open(os.path.join(args.out, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    try:
        n_search = build_search(data, now, args.out, persist=not args.demo)
        print(f"검색 목록 {n_search:,}건 (최근 {SEARCH_DAYS}일)")
    except Exception as e:
        print("검색 목록 실패:", type(e).__name__, e)
    if not args.demo:
        save_history(data, now)
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

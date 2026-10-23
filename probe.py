import json, re, html, urllib.request, urllib.parse, urllib.robotparser, ssl, sys
from concurrent.futures import ThreadPoolExecutor
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trend_site/0.2 (personal)"
def get(u, t=20):
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return r.status, r.geturl(), r.read()
_rb = {}
def allowed(u):
    p = urllib.parse.urlsplit(u); base = f"{p.scheme}://{p.netloc}"
    if base not in _rb:
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            st, _, body = get(base + "/robots.txt", 15); rp.parse(body.decode("utf-8", "ignore").splitlines())
        except urllib.error.HTTPError as e:
            rp.allow_all = e.code >= 400 and e.code not in (401, 403); rp.disallow_all = e.code in (401, 403)
        except Exception:
            rp.allow_all = True
        _rb[base] = rp
    return _rb[base].can_fetch("*", u)
KW = re.compile(r"(^|[^가-힣])(책|북|출판|신간|새책|책과|책의|문학|서평|북스|책마을|책꽂이|북리뷰)")
SITES = {
 "연합뉴스": ["https://www.yna.co.kr/culture/all", "https://www.yna.co.kr/"],
 "한겨레": ["https://www.hani.co.kr/arti/culture/book"],
 "경향신문": ["https://www.khan.co.kr/culture", "https://www.khan.co.kr/"],
 "조선일보": ["https://www.chosun.com/culture-life/book/", "https://www.chosun.com/culture-life/"],
 "동아일보": ["https://www.donga.com/news/Culture", "https://www.donga.com/"],
 "중앙일보": ["https://www.joongang.co.kr/culture", "https://www.joongang.co.kr/"],
 "한국일보": ["https://www.hankookilbo.com/News/Culture", "https://www.hankookilbo.com/"],
 "국민일보": ["https://www.kmib.co.kr/news/index.asp?sid1=cul", "https://www.kmib.co.kr/"],
 "세계일보": ["https://www.segye.com/newsList/0101060000000", "https://www.segye.com/"],
 "서울신문": ["https://www.seoul.co.kr/news/culture", "https://www.seoul.co.kr/"],
 "문화일보": ["https://www.munhwa.com/culture", "https://www.munhwa.com/"],
 "매일경제": ["https://www.mk.co.kr/news/culture/", "https://www.mk.co.kr/"],
 "한국경제": ["https://www.hankyung.com/culture", "https://www.hankyung.com/"],
 "서울경제": ["https://www.sedaily.com/NewsList/GC", "https://www.sedaily.com/"],
 "뉴시스": ["https://www.newsis.com/culture/list/?cid=10700&scid=10701", "https://www.newsis.com/"],
 "뉴스1": ["https://www.news1.kr/culture", "https://www.news1.kr/"],
 "독서신문": ["https://www.readersnews.com/"],
 "채널예스": ["https://ch.yes24.com/"],
 "출판저널": ["https://www.publishingjournal.kr/", "http://www.pubjournal.com/"],
 "북데일리": ["https://www.bookdaily.co.kr/"],
}
RSS = {
 "연합뉴스": ["https://www.yna.co.kr/rss/culture.xml"],
 "한겨레": ["https://www.hani.co.kr/rss/culture/"],
 "경향신문": ["https://www.khan.co.kr/rss/rssdata/culture_news.xml", "https://www.khan.co.kr/rss/rssdata/kh_book.xml"],
 "조선일보": ["https://www.chosun.com/arc/outboundfeeds/rss/category/culture-life/book/?outputType=xml", "https://www.chosun.com/arc/outboundfeeds/rss/category/culture-life/?outputType=xml"],
 "동아일보": ["https://rss.donga.com/culture.xml", "https://rss.donga.com/book.xml"],
 "중앙일보": ["https://rss.joins.com/joins_culture_list.xml"],
 "국민일보": ["https://www.kmib.co.kr/rss/data/kmibCulRss.xml"],
 "세계일보": ["https://www.segye.com/Articles/RSSList/segye_culture.xml"],
 "서울신문": ["https://www.seoul.co.kr/xml/rss/rss_culture.xml"],
 "문화일보": ["https://www.munhwa.com/rss/culture.xml"],
 "매일경제": ["https://www.mk.co.kr/rss/30000023/", "https://www.mk.co.kr/rss/50300009/"],
 "한국경제": ["https://www.hankyung.com/feed/culture", "https://www.hankyung.com/feed/book"],
 "독서신문": ["https://www.readersnews.com/rss/allArticle.xml"],
 "북데일리": ["https://www.bookdaily.co.kr/rss/allArticle.xml"],
 "채널예스": ["https://ch.yes24.com/Rss", "https://ch.yes24.com/rss/article"],
 "국립중앙도서관 사서추천": ["https://www.nl.go.kr/NL/contents/N50603000000.do"],
}
def probe_page(name, u):
    out = {"url": u}
    try:
        out["robots_ok"] = allowed(u)
        st, fu, b = get(u); t = b.decode("utf-8", "ignore")
        out["status"], out["final"] = st, fu
        out["title"] = (re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I) or [None, ""])[1].strip()[:80]
        links = []
        for m in re.finditer(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', t, re.S | re.I):
            href, txt = html.unescape(m.group(1)), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
            if (KW.search(txt) and len(txt) <= 14) or re.search(r"/book|books|publish|BOOK|Book", href):
                links.append((txt[:30], urllib.parse.urljoin(fu, href)))
        seen = set(); uniq = []
        for x in links:
            if x[1] not in seen:
                seen.add(x[1]); uniq.append(x)
        out["book_links"] = uniq[:25]
        out["shin_titles"] = [re.sub(r"<[^>]+>", "", x)[:60] for x in re.findall(r">([^<]{0,20}(?:\[신간\]|\[새책\]|신간|새책)[^<]{0,50})<", t)][:6]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
    return name, out
def probe_rss(name, u):
    out = {"url": u}
    try:
        out["robots_ok"] = allowed(u)
        st, fu, b = get(u); t = b.decode("utf-8", "ignore")
        its = t.split("<item")[1:]
        out["status"], out["items"] = st, len(its)
        def g(i, tag):
            m = re.search(rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", i, re.S); return (m.group(1).strip() if m else "")
        out["sample"] = [(g(i, "title")[:50], g(i, "link")[:90], g(i, "category")[:20]) for i in its[:8]]
        out["book_like"] = sum(1 for i in its if re.search(r"/book|신간|새책|책|출판|작가|소설|시집", g(i, "title") + g(i, "link") + g(i, "category")))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:80]}"
    return name, out
jobs = [(probe_page, n, u) for n, us in SITES.items() for u in us] + [(probe_rss, n, u) for n, us in RSS.items() for u in us]
with ThreadPoolExecutor(8) as ex:
    res = list(ex.map(lambda j: (j[0].__name__,) + j[0](j[1], j[2]), jobs))
# 구글뉴스 검색: 제목에 [신간]/[새책]
g = []
for q in ["intitle:신간 when:3d", "intitle:새책 when:7d", "intitle:책꽂이 when:7d"]:
    try:
        st, _, b = get("https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(q) + "&hl=ko&gl=KR&ceid=KR:ko")
        t = b.decode("utf-8", "ignore"); its = t.split("<item>")[1:]
        srcs = {}
        for i in its:
            s = (re.search(r"<source[^>]*>(.*?)</source>", i) or [None, "?"])[1]; srcs[s] = srcs.get(s, 0) + 1
        g.append({"q": q, "n": len(its), "sources": sorted(srcs.items(), key=lambda x: -x[1])[:25]})
    except Exception as e:
        g.append({"q": q, "error": str(e)[:80]})
json.dump({"res": res, "google": g}, open("probe_out.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("done", len(res))

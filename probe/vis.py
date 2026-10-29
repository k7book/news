import re, json, urllib.request, urllib.parse, xml.etree.ElementTree as ET
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trend_site/0.2 (personal)"
def get(u):
    r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=20)
    return r.status, r.headers.get("content-type", ""), r.read(2_000_000).decode("utf-8", "replace")
def gn(q):
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(q) + "&hl=en-US&gl=US&ceid=US:en"
C = {
 "imf_rss1": "https://www.imf.org/external/rss/feeds.aspx?category=Blogs",
 "imf_rss2": "https://www.imf.org/en/Blogs/rss",
 "imf_rss3": "https://www.imf.org/en/rss-list/feed?category=IMFBlog",
 "imf_page": "https://www.imf.org/en/Blogs/chart-of-the-week",
 "gn_imf": gn("site:imf.org \"chart of the week\""),
 "gn_imf2": gn("site:imf.org/en/Blogs when:30d"),
 "gn_mck": gn("site:mckinsey.com \"chart\" when:30d"),
 "gn_mck2": gn("McKinsey \"Chart of the Week\""),
 "gn_vor": gn("site:voronoiapp.com"),
 "gn_tab": gn("Tableau Public \"Viz of the Day\""),
 "tab_api": "https://public.tableau.com/public/apis/bff/discover/v2/vizzes/viz-of-the-day?startIndex=0&limit=12",
 "vc": "https://www.visualcapitalist.com/feed/",
 "owid": "https://ourworldindata.org/atom-data-insights.xml",
}
for k, u in C.items():
    try:
        s, ct, t = get(u)
        info = {"s": s, "ct": ct[:30], "len": len(t)}
        if "<rss" in t[:500] or "<feed" in t[:500]:
            root = ET.fromstring(re.sub(r"&(?!#?\w+;)", "&amp;", t))
            its = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            info["n"] = len(its)
            info["t"] = [((i.findtext("title") or i.findtext("{http://www.w3.org/2005/Atom}title") or "")[:70], (i.findtext("pubDate") or "")[:16], (i.findtext("source") or "")[:20]) for i in its[:4]]
        elif "json" in ct:
            j = json.loads(t); info["n"] = len(j.get("contents", [])); info["t"] = [x.get("title") for x in j.get("contents", [])[:3]]
        else:
            info["head"] = re.sub(r"\s+", " ", t[:200])
    except Exception as e:
        info = {"err": f"{type(e).__name__}: {str(e)[:80]}"}
    print("PROBE", json.dumps({k: info}, ensure_ascii=False))

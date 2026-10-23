import json, re, html, urllib.request, urllib.parse
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) trend_site/0.2 (personal)"
def get(u, t=20):
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "ko"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return r.status, r.read().decode("utf-8", "ignore")
out = {}
for t in ["평행우주", "가호", "헬터 스켈터", "장하석의 과학하는 마음"]:
    o = {}
    try:
        st, b = get("https://www.yes24.com/Product/Search?" + urllib.parse.urlencode({"domain": "BOOK", "query": t}))
        o["yes24_len"] = len(b)
        i = b.find('class="gd_name"'); o["yes24_chunk"] = re.sub(r"\s+", " ", b[max(0, i-2500):i+2500]) if i >= 0 else b[:1500]
    except Exception as e:
        o["yes24"] = str(e)[:80]
    try:
        st, b = get("https://search.kyobobook.co.kr/search?" + urllib.parse.urlencode({"keyword": t, "gbCode": "TOT", "target": "total"}))
        o["kyobo_len"] = len(b)
        i = b.find("prod_info"); o["kyobo_chunk"] = re.sub(r"\s+", " ", b[max(0, i-1500):i+2500]) if i >= 0 else b[:1500]
    except Exception as e:
        o["kyobo"] = str(e)[:80]
    out[t] = o
json.dump(out, open("probe_out.json", "w"), ensure_ascii=False, indent=1)

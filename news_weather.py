# -*- coding: utf-8 -*-
"""날씨 탭 (weather.html): 파주 운정 동네 날씨 자료를 site/weather.json 으로 저장한다.

- 기상청 초단기실황·초단기예보·단기예보·중기예보·자외선지수, 에어코리아(운정 측정소), 구글 뉴스 '날씨'
- 열쇠: 환경변수 DATA_GO_KR_KEY (공공데이터포털 일반 인증키 Decoding). GitHub Actions 에서는 Secrets 의 DATA_GO_KR_KEY.
  열쇠가 없으면 뉴스만 담고 화면에 '열쇠 없음'을 보여 준다.
- 화면은 static/weather.html (weather.json 을 읽어 그림), 아이콘은 static/wx/ (Meteocons, MIT)
- 표준 라이브러리만 사용. 내데스크(desk/weather.py)와 같은 자료 모양을 쓴다.
"""
import json, math, os, threading, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
LOC = {"name": "파주시 운정1동", "lat": 37.7183, "lon": 126.7580, "air_station": "운정",
       "mid_land": "11B00000", "mid_ta": "11B20305", "area_no": "4148000000"}
KMA = "http://apis.data.go.kr/1360000"


def now_kst():
    return datetime.now(KST).replace(tzinfo=None)


def grid(lat, lon):
    """위경도 → 기상청 격자(nx, ny)"""
    RE, GRID, SLAT1, SLAT2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180.0
    re_ = RE / GRID
    sl1, sl2, olon, olat = SLAT1 * D, SLAT2 * D, OLON * D, OLAT * D
    sn = math.log(math.cos(sl1) / math.cos(sl2)) / math.log(math.tan(math.pi * .25 + sl2 * .5) / math.tan(math.pi * .25 + sl1 * .5))
    sf = (math.tan(math.pi * .25 + sl1 * .5) ** sn) * math.cos(sl1) / sn
    ro = re_ * sf / (math.tan(math.pi * .25 + olat * .5) ** sn)
    ra = re_ * sf / (math.tan(math.pi * .25 + lat * D * .5) ** sn)
    th = lon * D - olon
    th = (th + 2 * math.pi) % (2 * math.pi)
    if th > math.pi:
        th -= 2 * math.pi
    th *= sn
    return int(ra * math.sin(th) + XO + .5), int(ro - ra * math.cos(th) + YO + .5)


def get(url, params, timeout=20):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "news-weather/1.0", "Accept": "application/json,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        msg = raw[:200]
        try:
            root = ET.fromstring(raw)
            msg = " ".join(x.text or "" for x in root.iter() if x.tag in ("returnAuthMsg", "errMsg", "resultMsg")) or msg
        except Exception:
            pass
        raise RuntimeError(msg.strip())


def items(d):
    h = (d.get("response") or {}).get("header") or {}
    if h.get("resultCode") not in (None, "00", "0000"):
        raise RuntimeError(f"{h.get('resultCode')} {h.get('resultMsg')}")
    it = (((d.get("response") or {}).get("body") or {}).get("items") or {})
    it = it.get("item", it) if isinstance(it, dict) else it
    return it if isinstance(it, list) else ([it] if it else [])


def ncst(key, nx, ny):
    t = now_kst() - timedelta(minutes=45)
    for back in (0, 1):
        b = t - timedelta(hours=back)
        it = items(get(KMA + "/VilageFcstInfoService_2.0/getUltraSrtNcst", {"serviceKey": key, "pageNo": 1, "numOfRows": 50, "dataType": "JSON",
                                                                          "base_date": b.strftime("%Y%m%d"), "base_time": b.strftime("%H00"), "nx": nx, "ny": ny}))
        if it:
            out = {i["category"]: i["obsrValue"] for i in it}
            out["at"] = it[0]["baseDate"] + it[0]["baseTime"]
            return out
    return {}


def ultra(key, nx, ny):
    t = now_kst() - timedelta(minutes=45)
    out = {}
    for i in items(get(KMA + "/VilageFcstInfoService_2.0/getUltraSrtFcst", {"serviceKey": key, "pageNo": 1, "numOfRows": 200, "dataType": "JSON",
                                                                         "base_date": t.strftime("%Y%m%d"), "base_time": t.strftime("%H30"), "nx": nx, "ny": ny})):
        out.setdefault(i["fcstDate"] + i["fcstTime"], {})[i["category"]] = i["fcstValue"]
    return out


def vilage(key, nx, ny):
    now = now_kst() - timedelta(minutes=15)
    h = max([s for s in (2, 5, 8, 11, 14, 17, 20, 23) if s <= now.hour], default=None)
    base = now if h is not None else now - timedelta(days=1)
    h = 23 if h is None else h
    out = {}
    for i in items(get(KMA + "/VilageFcstInfoService_2.0/getVilageFcst", {"serviceKey": key, "pageNo": 1, "numOfRows": 1500, "dataType": "JSON",
                                                                       "base_date": base.strftime("%Y%m%d"), "base_time": f"{h:02d}00", "nx": nx, "ny": ny})):
        out.setdefault(i["fcstDate"] + i["fcstTime"], {})[i["category"]] = i["fcstValue"]
    return {"base": base.strftime("%Y%m%d") + f"{h:02d}00", "hours": out}


def mid(key):
    now = now_kst()
    tm = now.strftime("%Y%m%d") + ("1800" if now.hour >= 18 else "0600") if now.hour >= 6 else (now - timedelta(days=1)).strftime("%Y%m%d") + "1800"
    land = items(get(KMA + "/MidFcstInfoService/getMidLandFcst", {"serviceKey": key, "pageNo": 1, "numOfRows": 10, "dataType": "JSON", "regId": LOC["mid_land"], "tmFc": tm}))
    ta = items(get(KMA + "/MidFcstInfoService/getMidTa", {"serviceKey": key, "pageNo": 1, "numOfRows": 10, "dataType": "JSON", "regId": LOC["mid_ta"], "tmFc": tm}))
    land, ta = (land[0] if land else {}), (ta[0] if ta else {})
    base = datetime.strptime(tm[:8], "%Y%m%d")
    days = []
    for n in range(3, 11):
        day = {"date": (base + timedelta(days=n)).strftime("%Y%m%d")}
        if f"wf{n}Am" in land:
            day.update(am=land.get(f"wf{n}Am"), pm=land.get(f"wf{n}Pm"), popAm=land.get(f"rnSt{n}Am"), popPm=land.get(f"rnSt{n}Pm"))
        elif f"wf{n}" in land:
            day.update(am=land.get(f"wf{n}"), pm=land.get(f"wf{n}"), popAm=land.get(f"rnSt{n}"), popPm=land.get(f"rnSt{n}"))
        if f"taMin{n}" in ta:
            day.update(tmin=ta.get(f"taMin{n}"), tmax=ta.get(f"taMax{n}"))
        if len(day) > 1:
            days.append(day)
    return {"tmFc": tm, "days": days}


def air(key):
    for i in items(get("http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMsrstnAcctoRltmMesureDnsty",
                       {"serviceKey": key, "returnType": "json", "numOfRows": 3, "pageNo": 1, "stationName": LOC["air_station"], "dataTerm": "DAILY", "ver": "1.3"})):
        if str(i.get("pm10Value", "-")).strip("-") or str(i.get("pm25Value", "-")).strip("-"):
            return {k: i.get(k) for k in ("dataTime", "pm10Value", "pm25Value")} | {"station": LOC["air_station"]}
    return {"station": LOC["air_station"]}


def uv(key):
    now = now_kst()
    for back in range(0, 13):
        try:
            it = items(get(KMA + "/LivingWthrIdxServiceV5/getUVIdxV5", {"serviceKey": key, "pageNo": 1, "numOfRows": 10, "dataType": "JSON",
                                                                        "areaNo": LOC["area_no"], "time": (now - timedelta(hours=back)).strftime("%Y%m%d%H")}))
        except Exception:
            continue
        if it:
            return it[0]
    raise RuntimeError("자외선지수 없음")


def news():
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": "날씨 when:1d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 news-weather"}), timeout=20) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):
        title, src = (it.findtext("title") or "").strip(), (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        out.append({"title": title, "url": it.findtext("link") or "", "source": src, "pub": it.findtext("pubDate") or ""})
        if len(out) >= 14:
            break
    return out


def festivals(key):
    """파주시 축제·행사 (한국관광공사 국문 관광정보 KorService2 searchFestival2). 이번 달에 열리는 것만, 시작일 순."""
    now = now_kst()
    month0 = now.replace(day=1)
    nxt = (month0 + timedelta(days=32)).replace(day=1)
    start = (month0 - timedelta(days=365)).strftime("%Y%m%d")   # 1년 안에 시작해 아직 하는 행사도 잡도록
    base = {"serviceKey": key, "MobileOS": "ETC", "MobileApp": "news-rajin", "_type": "json", "numOfRows": 300, "pageNo": 1,
            "arrange": "A", "eventStartDate": start}
    rows, errs = [], []
    for extra in ({"lDongRegnCd": "41", "lDongSignguCd": "480"}, {"areaCode": "31"}):
        try:
            rows = items(get("https://apis.data.go.kr/B551011/KorService2/searchFestival2", base | extra))
            rows = [r for r in rows if "파주" in (r.get("addr1") or "") + (r.get("addr2") or "")]
            if rows:
                break
        except Exception as e:
            errs.append(str(e)[:120])
    if not rows and errs:
        raise RuntimeError(errs[-1])
    out = []
    m0, m1 = month0.strftime("%Y%m%d"), (nxt - timedelta(days=1)).strftime("%Y%m%d")
    for r in rows:
        s_, e_ = str(r.get("eventstartdate") or ""), str(r.get("eventenddate") or r.get("eventstartdate") or "")
        if not s_ or e_ < m0 or s_ > m1:
            continue
        out.append({"title": r.get("title", ""), "addr": (r.get("addr1") or "").replace("경기도 ", ""), "start": s_, "end": e_,
                    "img": (r.get("firstimage") or r.get("firstimage2") or "").replace("http://", "https://"), "id": r.get("contentid", ""),
                    "tel": r.get("tel", "")})
    today = now.strftime("%Y%m%d")
    out.sort(key=lambda x: (x["end"] < today, x["start"]))
    return {"month": now.month, "items": out[:30]}


def culture(key):
    """파주 공연·전시 (한국문화정보원 한눈에보는문화정보 cultureinfo/area2, XML). 이번 달에 열리는 것."""
    now = now_kst()
    month0 = now.replace(day=1)
    m1 = ((month0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)).strftime("%Y%m%d")
    out, page = [], 1
    while page <= 5:
        q = urllib.parse.urlencode({"serviceKey": key, "PageNo": page, "numOfrows": 100, "sido": "경기", "gugun": "파주시",
                                    "from": month0.strftime("%Y%m%d"), "to": m1})
        req = urllib.request.Request("https://apis.data.go.kr/B553457/cultureinfo/area2?" + q, headers={"User-Agent": "news-weather/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            root = ET.fromstring(r.read())
        code = (root.findtext(".//resultCode") or "").strip()
        if code and code not in ("00", "0000"):
            raise RuntimeError(f"{code} {root.findtext('.//resultMsg')}")
        its = root.findall(".//item")
        for it in its:
            g = lambda k: (it.findtext(k) or "").strip()
            if "파주" not in g("sigungu") + g("place") + g("area") and g("sigungu"):
                continue
            out.append({"title": g("title"), "addr": g("place"), "start": g("startDate").replace(".", "").replace("-", ""),
                        "end": g("endDate").replace(".", "").replace("-", ""), "img": g("thumbnail").replace("http://", "https://"),
                        "id": g("seq"), "kind": g("realmName")})
        total = int(root.findtext(".//totalCount") or 0)
        if page * 100 >= total or not its:
            break
        page += 1
    return out


# 전국 주요 도시 (이름, 위도, 경도) — 지도 위 위치에도 씀
CITIES = [("서울", 37.5665, 126.9780), ("인천", 37.4563, 126.7052), ("파주", 37.7183, 126.7580), ("수원", 37.2636, 127.0286), ("춘천", 37.8813, 127.7298),
          ("강릉", 37.7519, 128.8761), ("청주", 36.6424, 127.4890), ("대전", 36.3504, 127.3845), ("전주", 35.8242, 127.1480), ("광주", 35.1595, 126.8526),
          ("대구", 35.8714, 128.6014), ("울산", 35.5384, 129.3114), ("부산", 35.1796, 129.0756), ("제주", 33.4996, 126.5312), ("안동", 36.5684, 128.7294), ("목포", 34.8118, 126.3922)]


def nation(key):
    """전국 날씨: 도시마다 초단기실황(기온) + 초단기예보(하늘)"""
    out = []

    def one(name, lat, lon):
        nx, ny = grid(lat, lon)
        try:
            n = ncst(key, nx, ny)
            u = ultra(key, nx, ny)
            k = sorted(u)[0] if u else None
            out.append({"name": name, "lat": lat, "lon": lon, "t": n.get("T1H"), "rn": n.get("RN1"), "pty": n.get("PTY"),
                        "sky": (u[k].get("SKY") if k else None), "upty": (u[k].get("PTY") if k else None)})
        except Exception as e:
            out.append({"name": name, "lat": lat, "lon": lon, "err": str(e)[:80]})

    ths = [threading.Thread(target=one, args=c, daemon=True) for c in CITIES]
    for t in ths:
        t.start()
    for t in ths:
        t.join(40)
    order = [c[0] for c in CITIES]
    return sorted(out, key=lambda x: order.index(x["name"]))


def past(key, days=7):
    """지난 날씨: 기상청 종관기상관측(ASOS) 일자료 — 파주와 가장 가까운 문산(99) 관측소. 어제까지."""
    now = now_kst()
    end, start = now - timedelta(days=1), now - timedelta(days=days)
    it = items(get("http://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList",
                   {"serviceKey": key, "pageNo": 1, "numOfRows": 31, "dataType": "JSON", "dataCd": "ASOS", "dateCd": "DAY",
                    "startDt": start.strftime("%Y%m%d"), "endDt": end.strftime("%Y%m%d"), "stnIds": "99"}))
    return [{k: r.get(k) for k in ("tm", "avgTa", "minTa", "maxTa", "sumRn", "avgRhm", "avgWs", "sumSsHr", "ddMes", "iscs")} for r in it]


def collect():
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    nx, ny = grid(LOC["lat"], LOC["lon"])
    res = {"loc": {"name": LOC["name"], "lat": LOC["lat"], "lon": LOC["lon"], "nx": nx, "ny": ny, "air_station": LOC["air_station"]},
           "need_key": not key, "errors": {}, "built": datetime.now(KST).isoformat(timespec="seconds")}
    jobs = {"news": news}
    if key:
        jobs.update(ncst=lambda: ncst(key, nx, ny), ultra=lambda: ultra(key, nx, ny), vilage=lambda: vilage(key, nx, ny),
                    mid=lambda: mid(key), air=lambda: air(key), uv=lambda: uv(key), nation=lambda: nation(key), past=lambda: past(key, 15))

    def one(name, fn):
        try:
            res[name] = fn()
        except Exception as e:  # 한 곳이 실패해도 나머지는 보여 준다
            res["errors"][name] = (str(e) or type(e).__name__)[:200]

    ths = [threading.Thread(target=one, args=(n, f), daemon=True) for n, f in jobs.items()]
    for t in ths:
        t.start()
    for t in ths:
        t.join(40)
    return res


def build(out_dir):
    """site/weather.json 저장. 실패해도 뉴스 사이트 생성은 계속되도록 예외를 밖으로 내지 않는다."""
    t0 = time.time()
    try:
        d = collect()
        with open(os.path.join(out_dir, "weather.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        print(f"날씨: {'열쇠 없음' if d['need_key'] else '받음'} · 오류 {list(d['errors']) or '없음'} · {time.time() - t0:.1f}초")
        key = os.environ.get("DATA_GO_KR_KEY", "").strip()
        fest = {"month": now_kst().month, "items": [], "need_key": not key}
        if key:
            try:
                fest.update(festivals(key))
            except Exception as e:
                fest["error"] = str(e)[:200]
            try:                       # 공연·전시 (한국문화정보원) — 활용신청 전이면 건너뜀
                seen = {x["title"] for x in fest["items"]}
                more = [x for x in culture(key) if x["title"] not in seen]
                today = now_kst().strftime("%Y%m%d")
                fest["items"] = sorted(fest["items"] + more, key=lambda x: (x["end"] < today, x["start"]))[:40]
                fest["culture"] = len(more)
            except Exception as e:
                fest["culture_error"] = str(e)[:200]
        with open(os.path.join(out_dir, "paju_fest.json"), "w", encoding="utf-8") as f:
            json.dump(fest, f, ensure_ascii=False, separators=(",", ":"))
        print(f"파주 축제·행사: {len(fest['items'])}건 (공연·전시 {fest.get('culture', 0)}){' · 축제 오류 ' + fest['error'] if fest.get('error') else ''}{' · 공연전시 오류 ' + fest['culture_error'] if fest.get('culture_error') else ''}")
    except Exception as e:
        print("날씨 실패:", e)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "site")
    os.makedirs(out, exist_ok=True)
    build(out)

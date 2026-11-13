# 지금 뜨는 뉴스 · 날씨 아이콘 (자체 디자인) — 스타일 3종
import os,sys
f=lambda v:f"{v:.2f}".rstrip('0').rstrip('.')
STY={
 "flat":dict(sun="#FFB300",ray="#FFB300",moon="#FFCB3D",cl="#EEF2F7",cl2="#C3CDD9",clr="#A7B4C4",drop="#2E7CF6",snow="#5AB4FF",bolt="#FFC107",fog="#9AA7B6",ol=None,grad=False),
 "gloss":dict(sun="#FFB300",ray="#FFC233",moon="#FFD45C",cl="#FFFFFF",cl2="#C9D3E0",clr="#AAB8CB",drop="#2E7CF6",snow="#64BCFF",bolt="#FFC107",fog="#9AA7B6",ol=None,grad=True),
 "chunky":dict(sun="#FFC21A",ray="#FF9F1A",moon="#FFD84D",cl="#FFFFFF",cl2="#D5DDE7",clr="#B9C5D3",drop="#3A86FF",snow="#7CC8FF",bolt="#FFC21A",fog="#8E9BAA",ol="#33404F",grad=False),
}
def lighten(h,a):
    r,g,b=int(h[1:3],16),int(h[3:5],16),int(h[5:7],16)
    m=lambda c:int(c+(255-c)*a) if a>0 else int(c*(1+a))
    return "#%02x%02x%02x"%(m(r),m(g),m(b))
class S:
    def __init__(s,st): s.c=STY[st]; s.d=[]; s.n=0
    def gid(s,col,ang=(0,0,0,1),k=.35):
        if not s.c["grad"]: return col
        s.n+=1; i=f"g{s.n}"
        s.d.append(f'<linearGradient id="{i}" x1="{ang[0]}" y1="{ang[1]}" x2="{ang[2]}" y2="{ang[3]}"><stop offset="0" stop-color="{lighten(col,k)}"/><stop offset="1" stop-color="{lighten(col,-.12)}"/></linearGradient>')
        return f"url(#{i})"
    def olw(s,w=2.4): return f' stroke="{s.c["ol"]}" stroke-width="{w}" stroke-linejoin="round"' if s.c["ol"] else ""
    # parts
    def sun(s,cx,cy,r,rays=True):
        c=s.c; o=""
        if rays:
            for k in range(8):
                import math;a=k*math.pi/4;r1,r2=r+3.2,r+7.2
                o+=f'<line x1="{f(cx+r1*math.cos(a))}" y1="{f(cy+r1*math.sin(a))}" x2="{f(cx+r2*math.cos(a))}" y2="{f(cy+r2*math.sin(a))}" stroke="{c["ray"] if not c["ol"] else c["ray"]}" stroke-width="{3.2 if c["ol"] else 2.8}" stroke-linecap="round"/>'
        o+=f'<circle cx="{f(cx)}" cy="{f(cy)}" r="{f(r)}" fill="{s.gid(c["sun"],k=.45)}"{s.olw()}/>'
        if c["grad"]: o+=f'<ellipse cx="{f(cx-r*.35)}" cy="{f(cy-r*.4)}" rx="{f(r*.38)}" ry="{f(r*.24)}" fill="#fff" opacity=".45" transform="rotate(-35 {f(cx-r*.35)} {f(cy-r*.4)})"/>'
        return o
    def moon(s,cx,cy,r):
        c=s.c
        # crescent: outer circle (cx,cy,r), cut by circle (cx+.55r, cy-.45r, .82r)
        import math
        ox,oy,orr=cx+.55*r,cy-.45*r,.82*r
        # intersection points
        dx,dy=ox-cx,oy-cy;d=math.hypot(dx,dy);a=(r*r-orr*orr+d*d)/(2*d);h=math.sqrt(r*r-a*a)
        px,py=cx+a*dx/d,cy+a*dy/d
        x1,y1=px+h*dy/d,py-h*dx/d; x2,y2=px-h*dy/d,py+h*dx/d
        p=f'M{f(x1)},{f(y1)}A{f(r)},{f(r)} 0 1 0 {f(x2)},{f(y2)}A{f(orr)},{f(orr)} 0 0 1 {f(x1)},{f(y1)}Z'
        o=f'<path d="{p}" fill="{s.gid(c["moon"],k=.4)}"{s.olw()}/>'
        return o
    def cloud(s,tx=0,ty=0,sc=1,col=None,dark=False):
        c=s.c; col=col or c["cl"]
        parts=['<circle cx="24" cy="36" r="9"/>','<circle cx="35" cy="29.5" r="12"/>','<circle cx="45.5" cy="37" r="8"/>','<rect x="24" y="33" width="21.5" height="12"/>']
        g=f'transform="translate({f(tx)} {f(ty)}) scale({f(sc)})"'
        o=""
        if c["ol"]: o+=f'<g {g} fill="{c["ol"]}" stroke="{c["ol"]}" stroke-width="{f(4.8/sc)}" stroke-linejoin="round">{"".join(parts)}</g>'
        fill=col
        if c["grad"]:
            s.n+=1;i=f"g{s.n}"
            s.d.append(f'<linearGradient id="{i}" x1="0" y1="17" x2="0" y2="46" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{lighten(col,.5)}"/><stop offset="1" stop-color="{lighten(col,-.14)}"/></linearGradient>');fill=f"url(#{i})"
        elif not c["ol"]:
            o+=f'<g {g} fill="{lighten(col,-.16)}" transform-origin="0 0"><g transform="translate(0 1.6)">{"".join(parts)}</g></g>'
        o+=f'<g {g} fill="{fill}">{"".join(parts)}</g>'
        if c["grad"]: o+=f'<g {g}><ellipse cx="31" cy="23" rx="6" ry="3" fill="#fff" opacity=".7" transform="rotate(-20 31 23)"/></g>'
        return o
    def drop(s,cx,ty,w,h,rot=0):
        c=s.c;r=w/2;b=ty+h-r
        p=f'M{f(cx)},{f(ty)}C{f(cx+r*.45)},{f(ty+h*.35)} {f(cx+r)},{f(b-r*.55)} {f(cx+r)},{f(b)}A{f(r)},{f(r)} 0 0 1 {f(cx-r)},{f(b)}C{f(cx-r)},{f(b-r*.55)} {f(cx-r*.45)},{f(ty+h*.35)} {f(cx)},{f(ty)}Z'
        t=f' transform="rotate({rot} {f(cx)} {f(b)})"' if rot else ""
        return f'<path d="{p}" fill="{s.gid(c["drop"],k=.45)}"{s.olw(1.6)}{t}/>'
    def flake(s,cx,cy,r):
        c=s.c;import math;o=""
        w=2.6 if c["ol"] else 2.2
        for k in range(3):
            a=k*math.pi/3+math.pi/2
            o+=f'<line x1="{f(cx-r*math.cos(a))}" y1="{f(cy-r*math.sin(a))}" x2="{f(cx+r*math.cos(a))}" y2="{f(cy+r*math.sin(a))}" stroke="{c["snow"]}" stroke-width="{w}" stroke-linecap="round"/>'
        return o
    def bolt(s,tx=0,ty=0):
        c=s.c
        return f'<polygon points="33,36 25,50 31,50 27,61 40,45 33.5,45 38,36" fill="{s.gid(c["bolt"],k=.4)}"{s.olw(1.8)} transform="translate({tx} {ty})"/>'
    def fogl(s,y,x1,x2):
        c=s.c
        return f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{c["fog"]}" stroke-width="{3.4 if c["ol"] else 3}" stroke-linecap="round"/>'
    def svg(s,body):
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs>{"".join(s.d)}</defs>{body}</svg>\n'
RAIN={"light":[(25,42,6,10),(40,42,6,10)],
      "mid":[(21,41,6,10),(32,46,6,10),(43,41,6,10)],
      "heavy":[(17,40,5.8,13,20),(27,46,5.8,13,20),(37,40,5.8,13,20),(47,46,5.8,13,20)]}
def icons(st):
    out={}
    def mk(name,fn):
        s=S(st); out[name]=s.svg(fn(s))
    mk("clear-day",lambda s:s.sun(32,32,12))
    mk("clear-night",lambda s:s.moon(32,32,15))
    mk("partly-cloudy-day",lambda s:s.sun(23,22,9.5)+s.cloud(5,8,.92))
    mk("partly-cloudy-night",lambda s:s.moon(22,22,11)+s.cloud(5,8,.92))
    mk("overcast",lambda s:s.cloud(-8,-4,.85,s.c["cl2"])+s.cloud(4,6,.92))
    mk("cloudy-dark",lambda s:s.cloud(-8,-4,.85,lighten(s.c["clr"],-.1))+s.cloud(4,6,.92,s.c["cl2"]))
    for lv,ds in RAIN.items():
        nm={"light":"rain-light","mid":"rain","heavy":"rain-heavy"}[lv]
        mk(nm,lambda s,ds=ds:"".join(s.drop(*d) for d in ds)+s.cloud(0,-8,1,s.c["clr"] if lv=="heavy" else s.c["cl2"]))
    mk("drizzle",lambda s:"".join(s.drop(*d) for d in RAIN["light"])+s.cloud(0,-8,1,s.c["cl2"]))
    for dn in ("day","night"):
        cel=(lambda s:s.sun(22,18,8.5)) if dn=="day" else (lambda s:s.moon(21,18,10))
        mk(f"partly-cloudy-{dn}-rain",lambda s,cel=cel:cel(s)+"".join(s.drop(*d) for d in [(22,44,6,10),(33,49,6,10),(44,44,6,10)])+s.cloud(2,-6,.95))
        mk(f"partly-cloudy-{dn}-snow",lambda s,cel=cel:cel(s)+s.flake(23,50,5)+s.flake(35,55,5)+s.flake(47,50,5)+s.cloud(2,-6,.95))
        mk(f"partly-cloudy-{dn}-sleet",lambda s,cel=cel:cel(s)+s.drop(24,45,5.6,9.5)+s.flake(36,53,5)+s.drop(47,45,5.6,9.5)+s.cloud(2,-6,.95))
    mk("snow",lambda s:s.flake(21,47,5.5)+s.flake(33,54,5.5)+s.flake(45,47,5.5)+s.cloud(0,-8,1))
    mk("sleet",lambda s:s.drop(21,41,6,10)+s.flake(33,52,5.5)+s.drop(45,41,6,10)+s.cloud(0,-8,1,s.c["cl2"]))
    mk("thunderstorms-rain",lambda s:s.drop(18,41,6,10,15)+s.drop(48,41,6,10,15)+s.bolt(0,0)+s.cloud(0,-10,1,s.c["clr"]))
    mk("fog",lambda s:s.cloud(0,-12,1,s.c["cl2"])+s.fogl(42,14,50)+s.fogl(49,18,46)+s.fogl(56,22,42))
    mk("not-available",lambda s:s.cloud(0,-4,1,s.c["cl2"])+f'<text x="35" y="40" text-anchor="middle" font-size="16" font-weight="800" fill="#7b8794" font-family="sans-serif">?</text>')
    out["overcast-day"]=out["overcast"]; out["overcast-night"]=out["overcast"]
    return out
if __name__=="__main__":
    st=sys.argv[1]; d=sys.argv[2]; os.makedirs(d,exist_ok=True)
    for k,v in icons(st).items(): open(f"{d}/{k}.svg","w").write(v)

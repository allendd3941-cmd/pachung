#!/usr/bin/env python
"""由 v1_slim_stats.json 產生 HTML 分析報告（含 SVG 圖表）。

用法（於專案根目錄執行）：
  python analysis/analyze_v1_slim.py   # 先產生統計
  python analysis/build_report.py      # 再產生報告
輸出：
  analysis/v1_slim_report.html
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

STATS = Path("analysis/v1_slim_stats.json")
HS_STATS = Path("analysis/v1_hotspot_stats.json")
HS_MAP = Path("analysis/v1_hotspot_map.json")
OUT = Path("analysis/v1_slim_report.html")

CITIES = {
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市",
    "嘉義市", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣",
    "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣",
}

# 藍色 sequential ramp（100→700），熱圖用
RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
        "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]

F = "{:,}".format


def fmt_pct(x: float, nd: int = 1) -> str:
    return f"{x*100:.{nd}f}%"


def rounded_hbar(x: float, y: float, w: float, h: float, fill: str, tip: str, r: float = 4) -> str:
    """水平 bar：右端 4px 圓角、左端貼齊基線。"""
    if w < r:
        return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w,1):.1f}" height="{h:.1f}" '
                f'fill="{fill}" data-tip="{escape(tip, quote=True)}"/>')
    d = (f"M{x:.1f},{y:.1f} H{x+w-r:.1f} A{r},{r} 0 0 1 {x+w:.1f},{y+r:.1f} "
         f"V{y+h-r:.1f} A{r},{r} 0 0 1 {x+w-r:.1f},{y+h:.1f} H{x:.1f} Z")
    return f'<path d="{d}" fill="{fill}" data-tip="{escape(tip, quote=True)}"/>'


def seg_rect(x: float, y: float, w: float, h: float, fill: str, tip: str, round_right: bool = False) -> str:
    if round_right:
        return rounded_hbar(x, y, w, h, fill, tip)
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w,0.5):.1f}" height="{h:.1f}" '
            f'fill="{fill}" data-tip="{escape(tip, quote=True)}"/>')


def hbar_chart(items, total_w=880, label_w=210, bar_h=18, row_gap=10, value_fmt=F):
    """單色水平 bar chart。items: [(label, value, tip)]"""
    vmax = max(v for _, v, _ in items) or 1
    plot_w = total_w - label_w - 80
    rows = []
    y = 4
    for label, v, tip in items:
        w = plot_w * v / vmax
        rows.append(f'<text x="{label_w-10}" y="{y+bar_h/2}" class="lab" text-anchor="end" dominant-baseline="central">{escape(str(label))}</text>')
        rows.append(rounded_hbar(label_w, y, w, bar_h, "var(--s1)", tip))
        rows.append(f'<text x="{label_w+w+8}" y="{y+bar_h/2}" class="val" dominant-baseline="central">{value_fmt(v)}</text>')
        y += bar_h + row_gap
    h = y + 4
    return f'<svg viewBox="0 0 {total_w} {h}" role="img">{"".join(rows)}</svg>'


def cat_hbar_chart(items, colors, total_w=880, label_w=210, bar_h=18, row_gap=10, value_fmt=F):
    """雙色（依類別）水平 bar chart。items: [(label, value, cat_idx, tip)]"""
    vmax = max(v for _, v, _, _ in items) or 1
    plot_w = total_w - label_w - 80
    rows = []
    y = 4
    for label, v, ci, tip in items:
        w = plot_w * v / vmax
        rows.append(f'<text x="{label_w-10}" y="{y+bar_h/2}" class="lab" text-anchor="end" dominant-baseline="central">{escape(str(label))}</text>')
        rows.append(rounded_hbar(label_w, y, w, bar_h, colors[ci], tip))
        rows.append(f'<text x="{label_w+w+8}" y="{y+bar_h/2}" class="val" dominant-baseline="central">{value_fmt(v)}</text>')
        y += bar_h + row_gap
    h = y + 4
    return f'<svg viewBox="0 0 {total_w} {h}" role="img">{"".join(rows)}</svg>'


def stacked100_chart(rows_data, series_names, series_colors, total_w=880, label_w=210, bar_h=18, row_gap=10, end_label=None):
    """100% 堆疊水平 bar。rows_data: [(label, [v1, v2, ...], row_total)]
    end_label: callable(row) -> 尾端文字"""
    plot_w = total_w - label_w - 84
    gap = 2
    out = []
    y = 4
    for label, values, row_total in rows_data:
        out.append(f'<text x="{label_w-10}" y="{y+bar_h/2}" class="lab" text-anchor="end" dominant-baseline="central">{escape(str(label))}</text>')
        x = label_w
        nz = [(i, v) for i, v in enumerate(values) if v > 0]
        for j, (i, v) in enumerate(nz):
            share = v / row_total
            w = plot_w * share - (gap if j < len(nz) - 1 else 0)
            tip = f"{label}｜{series_names[i]}：{F(v)} 筆（{fmt_pct(share)}）"
            out.append(seg_rect(x, y, w, bar_h, series_colors[i], tip, round_right=(j == len(nz) - 1)))
            x += plot_w * share
        if end_label:
            out.append(f'<text x="{label_w+plot_w+8}" y="{y+bar_h/2}" class="val" dominant-baseline="central">{end_label((label, values, row_total))}</text>')
        y += bar_h + row_gap
    h = y + 4
    return f'<svg viewBox="0 0 {total_w} {h}" role="img">{"".join(out)}</svg>'


def column_chart(items, total_w=880, plot_h=200, value_fmt=F, label_every=1, note_keys=()):
    """直條圖。items: [(label, value, tip)]；note_keys 的條用斜紋標註（資料未完成年度）。"""
    vmax = max(v for _, v, _ in items) or 1
    n = len(items)
    pad_l, pad_b, pad_t = 52, 26, 8
    plot_w = total_w - pad_l - 12
    slot = plot_w / n
    bw = min(24, slot * 0.62)
    out = []
    # y 軸格線（4 條，取整）
    step = _nice_step(vmax / 4)
    v = step
    while v <= vmax * 1.02:
        yy = pad_t + plot_h * (1 - v / vmax)
        out.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{total_w-12}" y2="{yy:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l-8}" y="{yy:.1f}" class="tick" text-anchor="end" dominant-baseline="central">{_compact(v)}</text>')
        v += step
    for idx, (label, val, tip) in enumerate(items):
        x = pad_l + slot * idx + (slot - bw) / 2
        h = plot_h * val / vmax
        y = pad_t + plot_h - h
        r = min(4, bw / 2, h)
        d = (f"M{x:.1f},{y+r:.1f} A{r},{r} 0 0 1 {x+r:.1f},{y:.1f} H{x+bw-r:.1f} "
             f"A{r},{r} 0 0 1 {x+bw:.1f},{y+r:.1f} V{y+h:.1f} H{x:.1f} Z")
        cls = ' class="incomplete"' if label in note_keys else ""
        out.append(f'<path d="{d}" fill="var(--s1)"{cls} data-tip="{escape(tip, quote=True)}"/>')
        if idx % label_every == 0:
            out.append(f'<text x="{x+bw/2:.1f}" y="{pad_t+plot_h+16}" class="tick" text-anchor="middle">{escape(str(label))}</text>')
    out.append(f'<line x1="{pad_l}" y1="{pad_t+plot_h}" x2="{total_w-12}" y2="{pad_t+plot_h}" class="axis"/>')
    return f'<svg viewBox="0 0 {total_w} {plot_h+pad_t+pad_b}" role="img">{"".join(out)}</svg>'


def line_chart(items, total_w=880, plot_h=200, y_is_pct=True, note_keys=()):
    """折線圖。items: [(label, value, tip)]"""
    vmax = max(v for _, v, _ in items)
    vmax = vmax * 1.15
    n = len(items)
    pad_l, pad_b, pad_t = 52, 26, 8
    plot_w = total_w - pad_l - 16
    out = []
    step = _nice_step(vmax / 4)
    v = step
    while v <= vmax * 1.02:
        yy = pad_t + plot_h * (1 - v / vmax)
        out.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{total_w-16}" y2="{yy:.1f}" class="grid"/>')
        lab = fmt_pct(v, 0) if y_is_pct else _compact(v)
        out.append(f'<text x="{pad_l-8}" y="{yy:.1f}" class="tick" text-anchor="end" dominant-baseline="central">{lab}</text>')
        v += step
    pts = []
    for idx, (label, val, tip) in enumerate(items):
        x = pad_l + plot_w * idx / (n - 1)
        y = pad_t + plot_h * (1 - val / vmax)
        pts.append((x, y, label, val, tip))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y, *_ in pts)
    out.append(f'<polyline points="{poly}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    for x, y, label, val, tip in pts:
        dash = ' class="incomplete"' if label in note_keys else ""
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"{dash} data-tip="{escape(tip, quote=True)}"/>')
        out.append(f'<text x="{x:.1f}" y="{pad_t+plot_h+16}" class="tick" text-anchor="middle">{escape(str(label))}</text>')
    out.append(f'<line x1="{pad_l}" y1="{pad_t+plot_h}" x2="{total_w-16}" y2="{pad_t+plot_h}" class="axis"/>')
    return f'<svg viewBox="0 0 {total_w} {plot_h+pad_t+pad_b}" role="img">{"".join(out)}</svg>'


def heatmap(row_labels, col_labels, matrix, tips, total_w=880, cell_h=30, label_w=130):
    """row-share 熱圖。matrix[i][j] 為 0–1 share。"""
    n_cols = len(col_labels)
    cell_w = (total_w - label_w - 10) / n_cols
    out = []
    head_h = 68
    for j, cl in enumerate(col_labels):
        x = label_w + cell_w * j + cell_w / 2
        out.append(f'<text x="{x:.1f}" y="{head_h-8}" class="tick" text-anchor="end" transform="rotate(-35 {x:.1f} {head_h-8})">{escape(cl)}</text>')
    vmax = max(max(r) for r in matrix) or 1
    for i, rl in enumerate(row_labels):
        y = head_h + cell_h * i
        out.append(f'<text x="{label_w-10}" y="{y+cell_h/2}" class="lab" text-anchor="end" dominant-baseline="central">{escape(rl)}</text>')
        for j in range(n_cols):
            share = matrix[i][j]
            k = 0 if vmax == 0 else min(len(RAMP) - 1, int((share / vmax) ** 0.75 * (len(RAMP) - 1) + 0.5))
            fill = RAMP[k]
            x = label_w + cell_w * j
            out.append(f'<rect x="{x+1:.1f}" y="{y+1:.1f}" width="{cell_w-2:.1f}" height="{cell_h-2:.1f}" rx="3" fill="{fill}" data-tip="{escape(tips[i][j], quote=True)}"/>')
            if share >= 0.10:
                ink = "#ffffff" if k >= 7 else "#0b0b0b"
                out.append(f'<text x="{x+cell_w/2:.1f}" y="{y+cell_h/2:.1f}" text-anchor="middle" dominant-baseline="central" style="font-size:10.5px;fill:{ink}">{share*100:.0f}%</text>')
    h = head_h + cell_h * len(row_labels) + 6
    return f'<svg viewBox="0 0 {total_w} {h}" role="img">{"".join(out)}</svg>'


def _nice_step(raw: float) -> float:
    import math
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _compact(v: float) -> str:
    if v >= 10000:
        return f"{v/10000:g} 萬"
    return F(int(v))


def details_table(headers, rows):
    th = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<details><summary>表格檢視</summary><div class="tbl-wrap"><table>'
            f"<thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div></details>")


def legend(pairs):
    sw = "".join(f'<span class="lg"><span class="sw" style="background:{c}"></span>{escape(n)}</span>' for n, c in pairs)
    return f'<div class="legend">{sw}</div>'


def card(title, subtitle, body):
    sub = f'<p class="sub">{subtitle}</p>' if subtitle else ""
    return f'<section class="card"><h3>{escape(title)}</h3>{sub}{body}</section>'


MAP_TMPL = """
<div class="map-box">
 <div class="map-head">
  <div class="legend">
   <span class="lg"><span class="sw" style="background:var(--s2)"></span>重複通報點（曾違規）</span>
   <span class="lg"><span class="sw" style="background:var(--s1)"></span>重複通報點（無違規紀錄）</span>
   <span class="lg"><span class="sw" style="background:var(--s1);opacity:.25"></span>全部通報密度（~2km 網格）</span>
  </div>
  <div class="map-btns"><button id="mzi">＋</button><button id="mzo">－</button><button id="mzr">重置</button></div>
 </div>
 <canvas id="hsmap"></canvas>
 <p class="map-hint">滾輪縮放、拖曳平移；滑過重複通報點顯示明細。編號 1–20 為紀錄數最多的熱點，點下方清單可直接跳至該地點。</p>
 <ol id="hslist"></ol>
</div>
<script>
(function(){
const M = __MAPDATA__;
const cv = document.getElementById('hsmap');
const ctx = cv.getContext('2d');
const K = Math.cos(23.5*Math.PI/180);
let lons=[], lats=[];
M.density.forEach(d=>{lons.push(M.origin[0]+d[0]*M.grid); lats.push(M.origin[1]+d[1]*M.grid);});
const b={x0:Math.min(...lons),x1:Math.max(...lons)+M.grid,y0:Math.min(...lats),y1:Math.max(...lats)+M.grid};
let W=880,H=620,st={s:0,cx:(b.x0+b.x1)/2,cy:(b.y0+b.y1)/2};
function fit(){st.cx=(b.x0+b.x1)/2; st.cy=(b.y0+b.y1)/2;
 st.s=Math.min(W/((b.x1-b.x0)*K), H/(b.y1-b.y0))*0.95;}
function px(lon,lat){return [W/2+(lon-st.cx)*st.s*K, H/2-(lat-st.cy)*st.s];}
function inv(x,y){return [st.cx+(x-W/2)/(st.s*K), st.cy-(y-H/2)/st.s];}
function cssv(n){return getComputedStyle(document.querySelector('.viz-root')).getPropertyValue(n).trim();}
const TOP = M.hotspots.slice(0,20);
function draw(){
 const dpr=window.devicePixelRatio||1;
 const w=cv.clientWidth; H=Math.round(w*0.7); W=w;
 cv.width=W*dpr; cv.height=H*dpr; cv.style.height=H+'px';
 ctx.setTransform(dpr,0,0,dpr,0,0);
 const surface=cssv('--surface'), s1=cssv('--s1'), s2=cssv('--s2'), ink=cssv('--ink');
 ctx.fillStyle=surface; ctx.fillRect(0,0,W,H);
 // 密度底圖
 const cw=Math.max(1,M.grid*st.s*K), ch=Math.max(1,M.grid*st.s);
 ctx.fillStyle=s1;
 M.density.forEach(d=>{
  const lon=M.origin[0]+d[0]*M.grid, lat=M.origin[1]+d[1]*M.grid;
  const p=px(lon,lat+M.grid);
  if(p[0]<-cw||p[0]>W+cw||p[1]<-ch||p[1]>H+ch) return;
  ctx.globalAlpha=Math.min(.5,.06+Math.log(1+d[2])*.055);
  ctx.fillRect(p[0],p[1],cw,ch);
 });
 ctx.globalAlpha=1;
 // 重複通報點
 M.hotspots.forEach(h=>{
  const p=px(h[0],h[1]);
  if(p[0]<-20||p[0]>W+20||p[1]<-20||p[1]>H+20) return;
  const r=1.6+Math.sqrt(h[2])*1.05;
  ctx.beginPath(); ctx.arc(p[0],p[1],r,0,7);
  ctx.fillStyle=h[3]>0?s2:s1; ctx.fill();
  ctx.lineWidth=1.2; ctx.strokeStyle=surface; ctx.stroke();
 });
 // Top20 編號
 ctx.font='600 11px system-ui'; ctx.textAlign='left';
 TOP.forEach((h,i)=>{
  const p=px(h[0],h[1]); if(p[0]<0||p[0]>W||p[1]<0||p[1]>H) return;
  const r=1.6+Math.sqrt(h[2])*1.05;
  ctx.fillStyle=ink; ctx.fillText(String(i+1), p[0]+r+3, p[1]+4);
 });
}
function zoom(f,mx,my){
 const [lon,lat]=inv(mx,my);
 st.s=Math.max(20,Math.min(2e6,st.s*f));
 st.cx=lon-(mx-W/2)/(st.s*K); st.cy=lat+(my-H/2)/st.s;
 draw();
}
cv.addEventListener('wheel',e=>{e.preventDefault();
 const r=cv.getBoundingClientRect();
 zoom(e.deltaY<0?1.3:1/1.3, e.clientX-r.left, e.clientY-r.top);},{passive:false});
let drag=null;
cv.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY]; cv.setPointerCapture(e.pointerId);});
cv.addEventListener('pointerup',()=>drag=null);
cv.addEventListener('pointermove',e=>{
 const r=cv.getBoundingClientRect(), tip=document.getElementById('tip');
 if(drag){st.cx-=(e.clientX-drag[0])/(st.s*K); st.cy+=(e.clientY-drag[1])/st.s;
  drag=[e.clientX,e.clientY]; draw(); return;}
 // hover 最近熱點
 const mx=e.clientX-r.left,my=e.clientY-r.top;
 let best=null,bd=144;
 M.hotspots.forEach(h=>{
  const p=px(h[0],h[1]);
  const d=(p[0]-mx)**2+(p[1]-my)**2;
  if(d<bd){bd=d;best=h;}
 });
 if(best&&tip){
  tip.textContent=`${M.cities[best[6]]}（${best[0]}, ${best[1]}）：${best[2]} 筆（${best[4]}–${best[5]} 年），違規 ${best[3]}/${best[2]}${best[7]?'，主要類型：'+best[7]:''}`;
  tip.style.opacity=1;
  tip.style.left=Math.min(e.clientX+14,window.innerWidth-tip.offsetWidth-8)+'px';
  tip.style.top=Math.min(e.clientY+14,window.innerHeight-tip.offsetHeight-8)+'px';
  cv.style.cursor='pointer';
 } else {if(tip)tip.style.opacity=0; cv.style.cursor='grab';}
});
document.getElementById('mzi').onclick=()=>zoom(1.5,W/2,H/2);
document.getElementById('mzo').onclick=()=>zoom(1/1.5,W/2,H/2);
document.getElementById('mzr').onclick=()=>{fit();draw();};
// Top20 清單
const ol=document.getElementById('hslist');
TOP.forEach((h,i)=>{
 const li=document.createElement('li');
 li.innerHTML=`<b>${M.cities[h[6]]}</b> ${h[2]} 筆（${h[4]}–${h[5]} 年）違規 ${h[3]}/${h[2]}${h[7]?'｜'+h[7]:''}`;
 li.onclick=()=>{st.cx=h[0]; st.cy=h[1]; st.s=250000; draw(); cv.scrollIntoView({behavior:'smooth',block:'center'});};
 ol.appendChild(li);
});
new ResizeObserver(()=>{draw();}).observe(cv);
try{matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>draw());}catch(e){}
new MutationObserver(()=>draw()).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
fit(); draw();
})();
</script>"""


def build_hotspot_section() -> str:
    """地點歷史與重複通報段落（含互動地圖）。統計檔不存在時回傳空字串。"""
    if not (HS_STATS.exists() and HS_MAP.exists()):
        return ""
    hs = json.loads(HS_STATS.read_text(encoding="utf-8"))
    cond = hs["conditional"]
    base = cond["first"]["rate"]

    # 條件違規率：emphasis 形式（重點條橘色、其餘灰）
    items = [
        ("首次通報（無同座標歷史）", cond["first"], "var(--gray)"),
        ("曾有紀錄，皆非違規", cond["prior_noviol"], "var(--gray)"),
        ("曾有紀錄，含違規", cond["prior_viol"], "var(--s2)"),
    ]
    vmax = max(d["rate"] for _, d, _ in items)
    rows, y, bar_h, label_w, plot_w = [], 4, 20, 250, 500
    for label, d, color in items:
        w = plot_w * d["rate"] / (vmax * 1.1)
        tip = f"{label}：{F(d['viol'])}/{F(d['n'])} 筆違規（{fmt_pct(d['rate'])}）"
        rows.append(f'<text x="{label_w-10}" y="{y+bar_h/2}" class="lab" text-anchor="end" dominant-baseline="central">{escape(label)}</text>')
        rows.append(rounded_hbar(label_w, y, w, bar_h, color, tip))
        rows.append(f'<text x="{label_w+w+8}" y="{y+bar_h/2}" class="val" dominant-baseline="central">{fmt_pct(d["rate"])}（n={F(d["n"])}）</text>')
        y += bar_h + 12
    cond_svg = f'<svg viewBox="0 0 880 {y+4}" role="img">{"".join(rows)}</svg>'

    rd = hs["repeat_dist"]
    dist_items = [(f"{k} 筆", v, f"同座標 {k} 筆紀錄的地點：{F(v)} 處") for k, v in rd.items()]
    dist_svg = hbar_chart(dist_items, label_w=110)

    map_html = MAP_TMPL.replace("__MAPDATA__", HS_MAP.read_text(encoding="utf-8"))

    lift = cond["prior_viol"]["rate"] / base
    body = (
        f'<p class="sub">「同一座標過去是否有違規紀錄」是可直接工程化的預測特徵：曾有違規紀錄的地點，'
        f'後續通報的違規率 <b>{fmt_pct(cond["prior_viol"]["rate"])}</b>，是首次通報基準（{fmt_pct(base)}）的 '
        f'<b>{lift:.2f} 倍</b>；反之，曾有紀錄但皆非違規者僅 {fmt_pct(cond["prior_noviol"]["rate"])}。'
        f'評估使用全部年度（網站公開點位皆為查核完畢的最終結果），歷史定義為同座標、年度嚴格較早的紀錄。'
        f'注意：此處用「座標完全相同」的保守匹配（{F(hs["locations_repeat"])} 個地點、'
        f'涉及 {F(cond["prior_noviol"]["n"] + cond["prior_viol"]["n"])} 筆有歷史的紀錄）——'
        f'改用鄰近半徑（如 50–100 公尺）匹配可大幅擴大特徵覆蓋率，是建模時的第一個延伸方向。</p>'
        + cond_svg
        + '<h4 style="font-size:13px;margin:18px 0 6px">同座標紀錄數分布（以地點計）</h4>'
        + dist_svg
        + '<h4 style="font-size:13px;margin:18px 0 6px">重複通報點地圖</h4>'
        + map_html
        + details_table(
            ["縣市", "重複通報地點數"],
            [(c, F(v)) for c, v in hs["hot_city_top"].items()],
        )
    )
    return f'<section class="card"><h3>交叉分析｜地點歷史與重複通報（違規辨識特徵評估）</h3>{body}</section>'


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    s = json.loads(STATS.read_text(encoding="utf-8"))
    total = s["rows_total"]
    viol = s["by_violation"].get("true", 0)
    viol_rate = viol / total

    # ---- KPI ----
    n_auth = len(s["by_authority"])
    n_city = sum(1 for a in s["by_authority"] if a in CITIES)
    agency_rows = sum(v for a, v in s["by_authority"].items() if a not in CITIES)
    peak_year = max(s["by_year"], key=lambda y: s["by_year"][y])
    kpi = f"""
<div class="kpis">
 <div class="tile"><div class="t-label">總筆數（93–115 年）</div><div class="t-value">{F(total)}</div><div class="t-sub">v1 資料集，22 縣市</div></div>
 <div class="tile"><div class="t-label">違規筆數 / 違規率</div><div class="t-value">{F(viol)}</div><div class="t-sub">佔全部 {fmt_pct(viol_rate)}</div></div>
 <div class="tile"><div class="t-label">權責單位種類</div><div class="t-value">{n_auth}</div><div class="t-sub">{n_city} 縣市 + {n_auth-n_city} 個中央/專責機關</div></div>
 <div class="tile"><div class="t-label">高峰年度</div><div class="t-value">{peak_year} 年</div><div class="t-sub">{F(s['by_year'][peak_year])} 筆</div></div>
</div>"""

    # ---- C1 年度分布 ----
    year_items = [(y, c, f"{y} 年：{F(c)} 筆") for y, c in s["by_year"].items()]
    c1 = card("各年度資料筆數", "114、115 年為進行中年度（斜紋標示），筆數尚未定型。93–96 年僅零星資料（合計 45 筆）。",
              column_chart(year_items, note_keys={"114", "115"})
              + details_table(["年度", "筆數"], [(y, F(c)) for y, c in s["by_year"].items()]))

    # ---- C2 權責單位 ----
    auth_items = []
    top_auth = list(s["by_authority"].items())[:18]
    for a, c in top_auth:
        ci = 0 if a in CITIES else 1
        kind = "縣市" if ci == 0 else "中央/專責機關"
        auth_items.append((a, c, ci, f"{a}（{kind}）：{F(c)} 筆"))
    rest = total - sum(c for _, c in top_auth)
    c2 = card("權責單位筆數（前 18 名）",
              f"authority_unit 共 {n_auth} 種值：22 縣市之外還有 {n_auth-n_city} 個中央/專責機關（河川分署、水源特定區、國家公園等），"
              f"共 {F(agency_rows)} 筆（{fmt_pct(agency_rows/total)}）——與 query_city 不同值的筆數 {F(s['authority_ne_city_rows'])} 筆。其餘 {n_auth-18} 個單位合計 {F(rest)} 筆。",
              legend([("縣市", "var(--s1)"), ("中央/專責機關", "var(--s2)")])
              + cat_hbar_chart(auth_items, ["var(--s1)", "var(--s2)"])
              + details_table(["權責單位", "類別", "筆數"],
                              [(a, "縣市" if a in CITIES else "中央/專責機關", F(c)) for a, c in s["by_authority"].items()]))

    # ---- C3 查證結果 ----
    res_items = [(k, v, f"{k}：{F(v)} 筆（{fmt_pct(v/total)}）") for k, v in s["by_result"].items()]
    c3 = card("查證結果分布",
              "7 種查證結果。is_violation 欄位僅「違規」對映 true，其餘 6 種皆為 false——"
              "其中「不屬於其管轄範圍」「無法現場查驗」「無法辨識變異點位置」屬於未確定案件而非確認合規，分析違規率時需注意分母定義。",
              hbar_chart(res_items)
              + details_table(["查證結果", "筆數", "佔比"],
                              [(k, F(v), fmt_pct(v/total)) for k, v in s["by_result"].items()]))

    # ---- C4 變異類型 ----
    type_top = list(s["by_type"].items())[:15]
    type_rest = total - sum(v for _, v in type_top)
    t_items = [(k if len(k) <= 14 else k[:13] + "…", v, f"{k}：{F(v)} 筆（{fmt_pct(v/total)}）") for k, v in type_top]
    c4 = card("變異類型分布（前 15 名）",
              f"共 {len(s['by_type'])} 種類型。最大宗是語意最模糊的「其他」（{fmt_pct(s['by_type'].get('其他',0)/total)}），"
              f"另有 {F(s['by_type'].get('(空白)', 0))} 筆空白（來源未提供）。前 15 名之外的 {len(s['by_type'])-15} 種合計 {F(type_rest)} 筆。",
              hbar_chart(t_items)
              + details_table(["變異類型", "筆數", "佔比"],
                              [(k, F(v), fmt_pct(v/total)) for k, v in s["by_type"].items()]))

    # ---- 交叉 A：權責單位 × 查證結果（違規率）----
    RES_V, RES_NV = "違規", "非違規"
    rows_a = []
    for a, _c in list(s["by_authority"].items())[:15]:
        d = s["authority_result"][a]
        rt = sum(d.values())
        v_ = d.get(RES_V, 0); nv = d.get(RES_NV, 0); other = rt - v_ - nv
        rows_a.append((a, [v_, nv, other], rt))
    rows_a.sort(key=lambda r: r[1][0] / r[2], reverse=True)
    ca = card("交叉分析｜權責單位 × 查證結果（依違規率排序，量前 15 名單位）",
              "違規率的單位差異極大。tooltip 顯示各段筆數與占比；「其他結果」含已知工程、不屬管轄等 5 種。",
              legend([("違規", "var(--s2)"), ("非違規", "var(--s1)"), ("其他結果", "var(--gray)")])
              + stacked100_chart(rows_a, ["違規", "非違規", "其他結果"],
                                 ["var(--s2)", "var(--s1)", "var(--gray)"],
                                 end_label=lambda r: f"違規率 {fmt_pct(r[1][0]/r[2])}")
              + details_table(["權責單位", "違規", "非違規", "其他", "違規率"],
                              [(a, F(v[0]), F(v[1]), F(v[2]), fmt_pct(v[0]/rt)) for a, v, rt in rows_a]))

    # ---- 交叉 B：變異類型 × 查證結果 ----
    rows_b = []
    for t, _c in s["by_type"].items():
        d = s["type_result"][t]
        rt = sum(d.values())
        if rt < 1500 or t == "(空白)":
            continue
        v_ = d.get(RES_V, 0); nv = d.get(RES_NV, 0); other = rt - v_ - nv
        rows_b.append((t if len(t) <= 14 else t[:13] + "…", [v_, nv, other], rt))
    rows_b.sort(key=lambda r: r[1][0] / r[2], reverse=True)
    rows_b = rows_b[:14]
    cb = card("交叉分析｜變異類型 × 查證結果（依違規率排序，樣本 ≥1,500 筆的類型）",
              "「傾倒廢棄物、土」「違規農業使用」等類型近乎必然違規；「作物變化」「自然植被改變」則幾乎不違規——類型本身就是強力的違規預測因子。",
              legend([("違規", "var(--s2)"), ("非違規", "var(--s1)"), ("其他結果", "var(--gray)")])
              + stacked100_chart(rows_b, ["違規", "非違規", "其他結果"],
                                 ["var(--s2)", "var(--s1)", "var(--gray)"],
                                 end_label=lambda r: f"違規率 {fmt_pct(r[1][0]/r[2])}")
              + details_table(["變異類型", "違規", "非違規", "其他", "違規率"],
                              [(t, F(v[0]), F(v[1]), F(v[2]), fmt_pct(v[0]/rt)) for t, v, rt in rows_b]))

    # ---- 交叉 C：權責單位 × 變異類型（row-share 熱圖）----
    hm_auth = [a for a, _ in list(s["by_authority"].items())[:10]]
    hm_types = [t for t, _ in list(s["by_type"].items())[:8] if t != "(空白)"][:8]
    matrix, tips = [], []
    for a in hm_auth:
        d = s["authority_type"][a]
        rt = sum(d.values())
        row, trow = [], []
        for t in hm_types:
            share = d.get(t, 0) / rt
            row.append(share)
            trow.append(f"{a} × {t}：{F(d.get(t,0))} 筆（占該單位 {fmt_pct(share)}）")
        matrix.append(row); tips.append(trow)
    short_types = [t if len(t) <= 12 else t[:11] + "…" for t in hm_types]
    cc = card("交叉分析｜權責單位 × 變異類型（占該單位案件比例，量前 10 名單位 × 前 8 大類型）",
              "各縣市的類型組成明顯不同：有的縣市以「其他」為大宗（通報分類習慣差異），有的以整地、農業使用為主——跨縣市比較前需先正規化分類習慣。",
              heatmap(hm_auth, short_types, matrix, tips)
              + details_table(["權責單位"] + hm_types,
                              [[a] + [fmt_pct(matrix[i][j]) for j in range(len(hm_types))] for i, a in enumerate(hm_auth)]))

    # ---- 交叉 D：年度 × 違規率 ----
    line_items = []
    for y, d in s["year_result"].items():
        rt = sum(d.values())
        if int(y) < 97:
            continue
        r = d.get(RES_V, 0) / rt
        line_items.append((y, r, f"{y} 年：違規 {F(d.get(RES_V,0))}/{F(rt)} 筆（{fmt_pct(r)}）"))
    cd = card("交叉分析｜年度 × 違規率（97 年起；93–96 年樣本 <25 筆不列）",
              "違規率長期走高：由 97–103 年的一到兩成，升至 110 年後的四成上下。可能反映查報效率提升、通報標準改變、或實際違規增加——值得深入的研究題目。114–115 年（空心點）通報仍在累積、年度尚未完整——點位本身皆為查核後結果，但年度組成仍會變動。",
              line_chart(line_items, note_keys={"114", "115"})
              + details_table(["年度", "違規", "總數", "違規率"],
                              [(y, F(d.get(RES_V, 0)), F(sum(d.values())), fmt_pct(d.get(RES_V, 0)/sum(d.values())))
                               for y, d in s["year_result"].items() if int(y) >= 97]))

    # ---- 地點歷史與重複通報（含互動地圖）----
    hs_section = build_hotspot_section()

    # ---- 潛在機會 ----
    dup_loc = s["coords_duplicated_locations"]
    opportunities = f"""
<section class="card">
<h3>完整資料檢視與潛在研究機會</h3>
<div class="opps">
<div class="opp"><b>1. 違規率的時間趨勢是最突出的訊號。</b>97 年約一成，113 年達四成上下（詳見上圖）。拆解「查報技術進步（衛星影像頻率/解析度）」「通報標準改變」與「實際行為改變」三種解釋，本身就是一個論文級題目；可對照 108 年起筆數倍增（17,899 → 26,267 → 31,399）的階梯，與國土利用監測計畫的政策節點互相印證。</div>
<div class="opp"><b>2. 變異類型是強力的違規預測因子。</b>「傾倒廢棄物、土」「違規農業使用」違規率極高，「作物變化」「自然植被改變」趨近於零。可建簡單分類模型，用類型＋縣市＋座標預測查證結果，評估「先驗排序、優先查核」的行政效益。</div>
<div class="opp"><b>3. 縣市間的巨大差異需要正規化解讀。</b>違規率與類型組成在縣市間差距懸殊（交叉圖 A、C）。差異可能來自土地利用結構、查報密度、或分類習慣（「其他」佔比差異極大）。控制類型組成後的「調整後違規率」會比原始值更有比較意義。</div>
<div class="opp"><b>4. 一成多的案件屬於中央/專責機關管轄。</b>{F(agency_rows)} 筆（{fmt_pct(agency_rows/total)}）權責單位是河川分署、水源特定區、國家公園等而非縣市政府——河川區域的違規（盜採砂石、傾倒廢土）是可以獨立成章的子題。</div>
<div class="opp"><b>5. 空間重疊點揭示「重複變異」熱點。</b>{F(dup_loc)} 個座標出現 2 筆以上資料（最多同點 18 筆）——同一地點跨年度反覆被通報，可能是頑固性違規或長期開發案。建議以座標聚類（DBSCAN 等）找出熱區，結合地籍/都市計畫圖深入。</div>
<div class="opp"><b>6. 資料品質面的注意事項。</b>「其他」類型佔三成二、310 筆類型空白、4 筆座標在台灣範圍外（已列 outliers 檔）、「不屬於其管轄範圍」等 5,400 餘筆未確定案件——這些在建模前都需要明確的處理規則；「其他」的縣市差異也暗示通報端分類指引不一致，本身即是行政研究素材。</div>
<div class="opp"><b>7. 可疊加的外部資料。</b>座標為 WGS84，可直接疊國土利用調查圖、地價、雨量/坡度、農地重劃區等開放資料，把「哪裡容易違規」從描述統計升級為空間計量模型。</div>
</div>
</section>"""

    style_and_js = """
<style>
.viz-root{color-scheme:light;
 --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
 --s1:#2a78d6;--s2:#eb6834;--gray:#c3c2b7;
 background:var(--page);color:var(--ink);
 font-family:system-ui,-apple-system,"Segoe UI","Noto Sans TC",sans-serif;
 margin:0;padding:28px 16px;min-height:100vh}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
 --page:#0d0d0d;--surface:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
 --s1:#3987e5;--s2:#d95926;--gray:#52514e}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;
 --page:#0d0d0d;--surface:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
 --s1:#3987e5;--s2:#d95926;--gray:#52504d}
.wrap{max-width:980px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px}
.meta{color:var(--ink2);font-size:13px;margin:0 0 20px}
h3{font-size:15.5px;margin:0 0 6px}
.sub{color:var(--ink2);font-size:12.5px;line-height:1.65;margin:0 0 12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
 padding:18px 20px;margin:0 0 16px}
svg{width:100%;height:auto;display:block}
svg text{font-family:inherit}
.lab{font-size:12px;fill:var(--ink2)}
.val{font-size:11.5px;fill:var(--ink2);font-variant-numeric:tabular-nums}
.tick{font-size:10.5px;fill:var(--muted);font-variant-numeric:tabular-nums}
.grid{stroke:var(--grid);stroke-width:1}
.axis{stroke:var(--axis);stroke-width:1}
.incomplete{opacity:.45}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:0 0 16px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.t-label{font-size:12px;color:var(--ink2)}
.t-value{font-size:28px;font-weight:600;margin:2px 0}
.t-sub{font-size:12px;color:var(--muted)}
.legend{display:flex;gap:16px;margin:0 0 10px;font-size:12px;color:var(--ink2);flex-wrap:wrap}
.lg{display:inline-flex;align-items:center;gap:6px}
.sw{width:12px;height:12px;border-radius:3px;display:inline-block}
details{margin-top:10px;font-size:12.5px}
summary{cursor:pointer;color:var(--ink2)}
.tbl-wrap{overflow-x:auto;margin-top:8px}
table{border-collapse:collapse;font-size:12px;min-width:420px}
th,td{padding:4px 10px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
td{font-variant-numeric:tabular-nums;color:var(--ink2)}
th{color:var(--ink)}
.opps{display:flex;flex-direction:column;gap:10px}
.opp{font-size:13px;line-height:1.7;color:var(--ink2)}
.opp b{color:var(--ink)}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--page);
 padding:6px 10px;border-radius:6px;font-size:12px;max-width:320px;line-height:1.5;
 opacity:0;transition:opacity .08s;z-index:9}
[data-tip]{cursor:default}
[data-tip]:hover{opacity:.85}
.map-box{margin-top:6px}
.map-head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.map-btns button{background:var(--surface);border:1px solid var(--border);color:var(--ink);
 border-radius:6px;padding:3px 12px;font-size:13px;cursor:pointer;margin-left:4px}
.map-btns button:hover{border-color:var(--muted)}
#hsmap{width:100%;border:1px solid var(--border);border-radius:8px;cursor:grab;touch-action:none}
.map-hint{font-size:11.5px;color:var(--muted);margin:6px 0 10px}
#hslist{font-size:12.5px;color:var(--ink2);columns:2;gap:24px;padding-left:20px;margin:0}
#hslist li{cursor:pointer;padding:2px 0;break-inside:avoid}
#hslist li:hover{color:var(--ink)}
footer{color:var(--muted);font-size:11.5px;margin-top:20px;line-height:1.7}
</style>
<script>
document.addEventListener('DOMContentLoaded',()=>{
 const tip=document.createElement('div');tip.id='tip';document.body.appendChild(tip);
 document.addEventListener('mousemove',e=>{
  const t=e.target.closest('[data-tip]');
  if(t){tip.textContent=t.getAttribute('data-tip');tip.style.opacity=1;
   const x=Math.min(e.clientX+14,window.innerWidth-tip.offsetWidth-8);
   const y=Math.min(e.clientY+14,window.innerHeight-tip.offsetHeight-8);
   tip.style.left=x+'px';tip.style.top=y+'px';}
  else tip.style.opacity=0;});
});
</script>"""

    html = f"""<title>國土利用監測變異點資料分析報告（v1）</title>
{style_and_js}
<div class="viz-root"><div class="wrap">
<h1>國土利用監測變異點資料分析報告</h1>
<p class="meta">資料：landchg_variation_points_v1_slim.csv（263,510 筆，民國 93–115 年，2026-08-01 v1 版）｜
來源：內政部國土管理署 國土利用監測整合資訊網（公開資料）</p>
{kpi}
{c1}
{c2}
{c3}
{c4}
{ca}
{cb}
{cc}
{cd}
{hs_section}
{opportunities}
<footer>方法註記：違規率 = 查證結果為「違規」÷ 該分組全部筆數（含未確定案件）。網站公開點位皆為查核完畢的最終結果；114–115 年為進行中年度，通報仍在累積，年度層級的趨勢解讀以 113 年以前較穩定。
統計由 analysis/analyze_v1_slim.py 產生、報告由 analysis/build_report.py 建置，可完整重現。</footer>
</div></div>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"written: {OUT} ({OUT.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

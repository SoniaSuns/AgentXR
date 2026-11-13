#!/usr/bin/env python3
"""Print-at-100% ArUco registration sheet. Board placement must be verified manually."""
import argparse
import base64
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from breadboard import dependencies, profile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',default='breadboards/terminal-63.json')
    parser.add_argument('--out',default='artifacts/board-markers.html')
    args=parser.parse_args();spec=json.loads(Path(args.profile).read_text());holes,_=profile(spec)
    cv,np=dependencies();size=spec['marker_size'];pitch=2.54
    positions=list(spec['markers'].values());x0=min(p[0] for p in positions)-size;y0=min(p[1] for p in positions)-size
    width=max(p[0] for p in positions)-x0+size;height=max(p[1] for p in positions)-y0+size
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width*pitch}mm" height="{height*pitch}mm" viewBox="0 0 {width} {height}">']
    for name,(x,y) in holes.items():
        parts.append(f'<circle cx="{x-x0}" cy="{y-y0}" r=".08" fill="#999"/>')
    for idx,(x,y) in spec['markers'].items():
        marker=cv.aruco.generateImageMarker(cv.aruco.getPredefinedDictionary(cv.aruco.DICT_4X4_50),int(idx),300)
        _,data=cv.imencode('.png',marker);encoded=base64.b64encode(data).decode()
        parts.append(f'<image x="{x-x0-size/2}" y="{y-y0-size/2}" width="{size}" height="{size}" href="data:image/png;base64,{encoded}"/>')
        parts.append(f'<text x="{x-x0}" y="{y-y0+size}" font-size="1.5" text-anchor="middle">ID {idx}</text>')
    parts.append('</svg>')
    html='<meta charset="utf-8"><title>AgentXR 标定板</title><style>@page{size:A4 landscape;margin:15mm}body{font-family:sans-serif}</style><p>按 100% 原尺寸打印；孔距 2.54mm。核对板型，将 a1 对齐网格左上角。标记位置须相对板子固定；本纸不是任意贴四角的模板。拍摄前复核全部编号及孔距。</p>'+''.join(parts)
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(html);print(out)
if __name__=='__main__':main()

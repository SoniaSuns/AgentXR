"""Evidence-bound breadboard geometry. Independent implementation; see docs/BREADBOARD.md.

No VLM output is accepted as a measured connection. Rails are explicit, never inferred
from stripe colours. Missing paths remain unknown; resistors and LEDs are not shorts.
"""
import hashlib
import math
import re
import time

ROWS = 'abcdefghij'


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def profile(spec):
    if not isinstance(spec, dict):
        raise ValueError('需要面包板几何配置')
    n = spec.get('columns')
    ys = spec.get('row_y')
    if type(n) is not int or not 2 <= n <= 100:
        raise ValueError('列数必须为 2–100')
    if not isinstance(ys, list) or len(ys) != 10 or not all(finite(y) and -50 <= y <= 50 for y in ys) or any(b-a < .5 for a,b in zip(ys,ys[1:])):
        raise ValueError('需要十个递增的孔行坐标，单位为孔距')
    holes = {f'{row}{c+1}':(float(c), float(ys[r])) for r,row in enumerate(ROWS) for c in range(n)}
    nodes = {h:f'{"ae" if h[0] in "abcde" else "fj"}:{h[1:]}' for h in holes}
    rails = spec.get('rails', [])
    if not isinstance(rails, list) or len(rails) > 4:
        raise ValueError('最多配置四条电源轨')
    for rail in rails:
        if not isinstance(rail,dict):raise ValueError('电源轨配置须为对象')
        name=rail.get('name','');xs=rail.get('x',[]);y=rail.get('y');groups=rail.get('groups',[])
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,15}',name) or not finite(y) or not -50<=y<=50 or not isinstance(xs,list) or not 1<=len(xs)<=100:
            raise ValueError('电源轨需要唯一名称、逐孔 x 坐标及 y 坐标')
        if not all(finite(x) and -10<=x<=110 for x in xs) or len(groups)!=len(xs):
            raise ValueError('每个电源轨孔须指定分段编号；未知用 null')
        for i,x in enumerate(xs):
            h=f'{name}:{i+1}';g=groups[i]
            if h in holes or (g is not None and (type(g) is not int or not 0<=g<=100)):
                raise ValueError('电源轨重名或分段编号无效')
            holes[h]=(float(x),float(y));nodes[h]=f'rail:{name}:{g}' if g is not None else f'unknown:{h}'
    if len(set(holes.values()))!=len(holes):raise ValueError('孔坐标重叠')
    return holes,nodes


def evidence(spec, endpoints, wires, measurements, expected, digest):
    holes,nodes=profile(spec)
    for items in (endpoints,wires,measurements,expected):
        if not isinstance(items,list) or len(items)>100:raise ValueError('连接表每类最多 100 项')
    pins={};parent={}
    def find(x):
        parent.setdefault(x,x)
        if parent[x]!=x:parent[x]=find(parent[x])
        return parent[x]
    def union(a,b):parent[find(a)]=find(b)
    for p in endpoints:
        if not isinstance(p,dict):raise ValueError('端点格式无效')
        name=p.get('id','');hole=p.get('hole')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.+-]{1,40}',name) or name in pins:
            raise ValueError('端点编号须唯一，如 W1.1 或 LED1.A')
        if hole not in holes or p.get('confirmed') is not True:
            raise ValueError('端点必须由用户明确确认到配置中的孔；候选不能参与连接计算')
        pins[name]={'hole':hole,'node':nodes[hole],'source':'user_confirmed_hole','frame_digest':digest}
    edges=[]
    for w in wires:
        if not isinstance(w,dict):raise ValueError('导线格式无效')
        a,b=w.get('a'),w.get('b')
        if a not in pins or b not in pins or a==b or w.get('confirmed') is not True:
            raise ValueError('导线两端必须已确认；不要将电阻或 LED 当作导线合并')
        union(pins[a]['node'],pins[b]['node']);edges.append({'a':a,'b':b,'source':'user_confirmed_wire'})
    measured={};observations=[]
    for m in measurements:
        if not isinstance(m,dict):raise ValueError('测量格式无效')
        a,b=m.get('a'),m.get('b');value=m.get('value')
        if a not in pins or b not in pins or a==b or value not in ('closed','open','unknown'):
            raise ValueError('通断记录需要有效端点及 closed/open/unknown')
        if m.get('power_off') is not True:raise ValueError('通断测量须先断电并确认')
        key=tuple(sorted((a,b)))
        if key in measured:raise ValueError('同一端点对只保留一次当前测量，需先移除旧值')
        measured[key]=value
        observations.append({'a':a,'b':b,'value':value,'source':'user_reported_continuity','frame_digest':digest})
    # Keep measured connectivity separate from geometry; contradictions must stay visible.
    measured_parent={}
    def mf(x):
        measured_parent.setdefault(x,x)
        if measured_parent[x]!=x:measured_parent[x]=mf(measured_parent[x])
        return measured_parent[x]
    for (a,b),v in measured.items():
        if v=='closed':measured_parent[mf(a)]=mf(b)
    checks=[]
    for pair in expected:
        if not isinstance(pair,dict):raise ValueError('目标格式无效')
        a,b=pair.get('a'),pair.get('b');want=pair.get('connected')
        if a not in pins or b not in pins or a==b or type(want) is not bool:raise ValueError('目标关系须引用已确认端点和 connected 布尔值')
        geometric=find(pins[a]['node'])==find(pins[b]['node'])
        electrical=measured.get(tuple(sorted((a,b))),'unknown')
        closure=mf(a)==mf(b)
        conflict=electrical=='open' and (geometric or closure)
        if conflict:verdict='conflict'
        elif electrical!='unknown' or closure:
            connected=electrical=='closed' or closure
            verdict='measured_match' if connected==want else 'measured_mismatch'
        elif geometric:verdict='geometry_match' if want else 'geometry_mismatch'
        else:verdict='unknown'
        checks.append({'a':a,'b':b,'expected_connected':want,'geometric_same_net':geometric,'measurement':electrical,'verdict':verdict})
    groups={}
    for name,p in pins.items():groups.setdefault(find(p['node']),[]).append(name)
    from circuit import analyze as analyze_circuit
    # Deterministic loop/short/polarity rules and a single-move repair, from the same pins.
    circuit=analyze_circuit(holes,nodes,pins,edges,[{'a':a,'b':b,'value':v} for (a,b),v in measured.items()])
    return {'frame_digest':digest,'endpoints':pins,'wires':edges,'nets':list(groups.values()),
            'measurements':observations,'checks':checks,'circuit':circuit,'limitations':['几何连接依赖人工确认的板型和落孔；不证明实际导通。','未建立路径不等于断路；电阻和 LED 不合并为同一节点。','通断为用户报告；电源轨分段未知时逐孔隔离。']}


def dependencies():
    try:
        import cv2
        import numpy as np
        return cv2,np
    except ImportError as exc:
        raise ValueError('板面标定需要 OpenCV 和 numpy：安装 requirements-vision.txt 后重启服务') from exc


def rectify(data, request):
    cv,np=dependencies()
    if not isinstance(request,dict):raise ValueError('板面配置无效')
    spec=request.get('profile');holes,_=profile(spec)
    if request.get('layout_confirmed') is not True:raise ValueError('先确认板型、编号方向与中缝/电源轨分段')
    from image_metadata import jpeg_size
    dimensions=jpeg_size(data)
    if not dimensions or dimensions[0]*dimensions[1]>20_000_000:raise ValueError('JPEG 尺寸无效或过大')
    picture=cv.imdecode(np.frombuffer(data,np.uint8),cv.IMREAD_COLOR)
    if picture is None:raise ValueError('无法解码标定照片')
    h,w=picture.shape[:2]
    if w*h>20_000_000:raise ValueError('照片过大')
    digest=hashlib.sha256(data).hexdigest()
    if request.get('frame_digest') not in (None,digest):raise ValueError('标定属于另一帧，请重新冻结并标定')
    if request.get('mode','manual') not in ('manual','aruco'):raise ValueError('标定方式无效')
    anchors=request.get('anchors',[]);src=[];dst=[]
    if request.get('mode','manual')=='aruco':
        if request.get('marker_layout_confirmed') is not True:raise ValueError('先确认标记相对孔格的实际位置，不能只贴在任意四角')
        marker_map=spec.get('markers',{});size=spec.get('marker_size')
        if not finite(size) or not .5<=size<=10 or not isinstance(marker_map,dict) or len(marker_map)!=4:raise ValueError('需要四个标记的板面位置与边长（孔距单位）')
        detector=cv.aruco.ArucoDetector(cv.aruco.getPredefinedDictionary(cv.aruco.DICT_4X4_50),cv.aruco.DetectorParameters())
        corners,ids,_=detector.detectMarkers(picture)
        seen=set()
        for quad,idx in zip(corners,[] if ids is None else ids.flatten()):
            key=str(int(idx))
            if key not in marker_map:continue
            if key in seen:raise ValueError('标记 ID 重复，拒绝标定')
            seen.add(key);center=marker_map[key]
            if not isinstance(center,list) or len(center)!=2 or not all(finite(x) and -120<=x<=120 for x in center):raise ValueError('标记位置无效')
            x,y=center;s=size/2
            src.extend([[x-s,y-s],[x+s,y-s],[x+s,y+s],[x-s,y+s]]);dst.extend(quad.reshape(4,2).tolist())
        if seen!=set(marker_map):raise ValueError('四个 ArUco 标记未全部可见，不能沿用旧帧标定')
        H,_=cv.findHomography(np.float64(src),np.float64(dst),0)
    else:
        expected=[f'a1',f'a{spec["columns"]}',f'j{spec["columns"]}','j1','e1',f'f{spec["columns"]}']
        if not isinstance(anchors,list) or len(anchors)!=6 or not all(isinstance(a,dict) for a in anchors) or [a.get('hole') for a in anchors]!=expected:raise ValueError('按顺序标定四个角孔，再点 e1 和最末列 f 孔复核')
        for a in anchors:
            xy=a.get('xy')
            if not isinstance(xy,list) or len(xy)!=2 or not all(finite(v) and 0<=v<=1 for v in xy):raise ValueError('标定点越界')
            src.append(holes[a['hole']]);dst.append([xy[0]*w,xy[1]*h])
        quad=np.float32(dst[:4])
        if not cv.isContourConvex(quad) or cv.contourArea(quad,oriented=True)<=0:raise ValueError('角点次序错误、镜像或退化')
        H=cv.getPerspectiveTransform(np.float32(src[:4]),quad)
    if H is None or not np.isfinite(H).all() or abs(np.linalg.det(H))<1e-10:raise ValueError('标定变换退化')
    def project(points):return cv.perspectiveTransform(np.float64(points).reshape(-1,1,2),H).reshape(-1,2)
    names=list(holes);xy=project(list(holes.values()))
    # Minimum projected x/y pitch, not resized output pitch; upsampling adds no evidence.
    unit=[]
    for point in holes.values():unit.extend([point,(point[0]+1,point[1]),(point[0],point[1]+1)])
    projected=project(unit).reshape(-1,3,2)
    pitches=np.minimum(np.linalg.norm(projected[:,1]-projected[:,0],axis=1),np.linalg.norm(projected[:,2]-projected[:,0],axis=1))
    pitch=float(pitches.min())
    error=float(np.linalg.norm(project(src)-np.float64(dst),axis=1).max())
    reasons=[]
    if pitch<20:reasons.append(f'最小原图孔距 {pitch:.1f}px，小于 20px；请提高分辨率或靠近，放大图片不算新增细节')
    if error>max(2,pitch*.25):reasons.append(f'独立复核点/标记重投影误差 {error:.1f}px 过大')
    if (xy[:,0]<0).any() or (xy[:,0]>=w).any() or (xy[:,1]<0).any() or (xy[:,1]>=h).any():reasons.append('配置中的孔格未完整入镜')
    # Validate actual hole texture across the board, refusing blank/shifted fits.
    gray=cv.cvtColor(picture,cv.COLOR_BGR2GRAY);supported=0;sampled=0
    for (x,y),p in zip(xy,pitches):
        x,y=int(round(x)),int(round(y));r=max(2,int(p*.22));outer=max(r+1,int(p*.42))
        if x-outer<0 or y-outer<0 or x+outer>=w or y+outer>=h:continue
        patch=gray[y-outer:y+outer+1,x-outer:x+outer+1];center=gray[y-r:y+r+1,x-r:x+r+1]
        ring=np.concatenate((patch[:2].ravel(),patch[-2:].ravel(),patch[:, :2].ravel(),patch[:,-2:].ravel()))
        sampled+=1;supported+=int(float(np.median(ring))-float(np.percentile(center,10))>18)
    support=supported/max(1,sampled)
    if support<.55:reasons.append(f'只有 {support:.0%} 的预测孔位有暗孔纹理支持；检查板型、起始孔和对齐')
    warped,warp_grid,size=labeled_warp(picture,holes,H,reasons,cv,np)
    ok,encoded=cv.imencode('.jpg',warped,[cv.IMWRITE_JPEG_QUALITY,92])
    if not ok:raise ValueError('矫正图编码失败')
    grid=[{'hole':name,'xy':[float(p[0]/w),float(p[1]/h)],'pitch_px':float(pp)} for name,p,pp in zip(names,xy,pitches)]
    return {'ok':not reasons,'reasons':reasons,'frame_digest':digest,'width':w,'height':h,'min_pitch_px':pitch,
            'reprojection_error_px':error,'hole_support':support,'grid':grid,'warp_grid':warp_grid,'warp_size':size,
            'candidates':color_candidates(picture,cv,np),'image':encoded.tobytes()}


WARP_SCALE=32   # canonical px per hole pitch: large enough for a model to read every label


def labeled_warp(picture, holes, H, reasons, cv, np):
    """Top-down view with every column numbered and every row lettered (a 'set of marks').

    The model is asked for hole names, never for pixel coordinates: labels sit outside the
    hole field so they do not hide what is plugged in. Returns (image, {hole: [x, y]}, (w, h))."""
    pts=np.array(list(holes.values()));scale=WARP_SCALE
    minimum=pts.min(axis=0)-np.array([2.5,2.2]);maximum=pts.max(axis=0)+np.array([2.5,2.2])
    size=tuple(int(v) for v in np.ceil((maximum-minimum)*scale))
    T=np.array([[scale,0,-minimum[0]*scale],[0,scale,-minimum[1]*scale],[0,0,1]],float)
    warped=cv.warpPerspective(picture,T@np.linalg.inv(H),size,borderValue=(245,245,245))
    to=lambda point:tuple(int(round(v)) for v in (np.array(point)-minimum)*scale)
    ink=(0,150,0) if not reasons else (0,120,255);text=(160,0,0)
    columns=sorted({int(n[1:]) for n in holes if n[0] in ROWS})
    rows=[r for r in ROWS if r+'1' in holes]
    top=min(holes[r+'1'][1] for r in rows);bottom=max(holes[r+'1'][1] for r in rows)
    # Faint guide line every 5 columns, column numbers above and below (two staggered lines so
    # two-digit numbers never overlap at this pitch), row letters at both ends.
    for c in columns:
        x=holes[f'{rows[0]}{c}'][0]
        if c%5==0:
            cv.line(warped,to((x,top-.6)),to((x,bottom+.6)),(215,215,215),1,cv.LINE_AA)
        for y,sign in ((top-1.0,-1),(bottom+1.0,1)):
            yy=y+sign*(.65 if c%2==0 else 0)
            px,py=to((x,yy));label=str(c);(tw,_),_=cv.getTextSize(label,cv.FONT_HERSHEY_SIMPLEX,.42,1)
            cv.putText(warped,label,(px-tw//2,py+5),cv.FONT_HERSHEY_SIMPLEX,.42,text,1,cv.LINE_AA)
    for r in rows:
        x,y=holes[r+'1'];xr=holes[f'{r}{columns[-1]}'][0]
        for xx,dx in ((x-1.4,-6),(xr+1.4,-6)):
            px,py=to((xx,y));cv.putText(warped,r,(px+dx,py+6),cv.FONT_HERSHEY_SIMPLEX,.5,text,1,cv.LINE_AA)
    for name,point in holes.items():
        if ':' in name:   # rail holes: name at first/last and every fifth hole
            rail,idx=name.split(':');idx=int(idx)
            if idx==1 or idx%5==0:
                px,py=to(point);cv.putText(warped,name,(px-10,py-7),cv.FONT_HERSHEY_SIMPLEX,.3,text,1,cv.LINE_AA)
    warp_grid={}
    for name,point in holes.items():
        px,py=to(point);cv.circle(warped,(px,py),max(3,scale//9),ink,1,cv.LINE_AA);warp_grid[name]=[px,py]
    return warped,warp_grid,size


def snap(grid, xy, width, height):
    if not isinstance(xy,list) or len(xy)!=2 or not all(finite(v) and 0<=v<=1 for v in xy):raise ValueError('端点坐标无效')
    distances=sorted((math.hypot((g['xy'][0]-xy[0])*width,(g['xy'][1]-xy[1])*height),g['hole'],g['pitch_px']) for g in grid)
    d,hole,pitch=distances[0];margin=distances[1][0]-d
    reason='离孔过远' if d>.35*pitch else '位于两孔之间' if margin<.25*pitch else None
    return {'hole':None if reason else hole,'nearest':hole,'distance_px':d,'reason':reason}


def process(data, request):
    result=rectify(data,request)
    pins=[]
    endpoints=request.get('endpoints',[])
    if not isinstance(endpoints,list) or len(endpoints)>100:raise ValueError('端点最多 100 个')
    for pin in endpoints:
        if not isinstance(pin,dict):raise ValueError('端点格式无效')
        match=snap(result['grid'],pin.get('xy'),result['width'],result['height'])
        if not result['ok'] or match['hole'] is None:raise ValueError('孔格不可信或端点模糊，不能生成连接表')
        pins.append({'id':pin.get('id'),'hole':match['hole'],'confirmed':pin.get('confirmed')})
    result['connections']=evidence(request['profile'],pins,request.get('wires',[]),request.get('measurements',[]),request.get('expected',[]),result['frame_digest'])
    return result


def color_candidates(picture, cv, np):
    """Coarse insulation regions, never asserted wire identities or insertion points.

    A contour's principal-axis extremes are only annotation suggestions. Occlusion,
    curved paths, same-colour crossings and black connectors require human review.
    """
    hsv=cv.cvtColor(picture,cv.COLOR_BGR2HSV);height,width=picture.shape[:2];out=[]
    for color,lo,hi in [('yellow',18,38),('blue',90,130),('green',39,88),('red',0,10)]:
        mask=cv.inRange(hsv,np.array([lo,90,50]),np.array([hi,255,255]))
        if color=='red':mask|=cv.inRange(hsv,np.array([170,90,50]),np.array([179,255,255]))
        count,labels,stats,_=cv.connectedComponentsWithStats(mask)
        # Prefer large isolated regions; do not join separated same-colour components.
        for idx in sorted(range(1,count),key=lambda i:stats[i,cv.CC_STAT_AREA],reverse=True)[:8]:
            if stats[idx,cv.CC_STAT_AREA]<40:continue
            y,x=np.where(labels==idx);points=np.column_stack([x,y]).astype(float)
            covariance=np.cov(points.T);values,vectors=np.linalg.eigh(covariance)
            if values[-1]<9 or values[-1]/max(1,values[0])<3:continue
            along=np.sum(points*vectors[:,-1],axis=1);ends=points[[along.argmin(),along.argmax()]]
            out.append({'id':f'candidate-{len(out)+1}','color':color,'endpoints':[[float(x/width),float(y/height)] for x,y in ends],
                        'status':'unconfirmed_insulation_extremes','warning':'绝缘层端部候选，不代表引脚落孔；不可据此合并节点'})
    return out[:24]

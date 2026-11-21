"""分账纯逻辑：不依赖数据库，便于离线核对。
划窗、实抄取舍、余量分摊、挂账与倒挂判定都在这里，service 只负责读写。"""
import hashlib,json
from datetime import timedelta

EPS=0.0005  # 0.5Wh 容差，表底按0.001kWh入账
BOUNDARY_HOURS=2  # 抄表同一轮次允许的时刻偏差

def boundary_reading(readings,when,tol_hours=BOUNDARY_HOURS):
    """取边界时刻附近最近的一条表底；readings需含id/reading_kwh/taken_at。"""
    best=None
    for r in readings:
        delta=abs((r.taken_at-when).total_seconds())/3600
        if delta<=tol_hours and (best is None or delta<best[0]):best=(delta,r)
    return best[1] if best else None

def array_actual(readings,start_at,end_at):
    """分表在窗口两端都有表底才算抄齐，返回实抄kWh；否则None。"""
    s=boundary_reading(readings,start_at);e=boundary_reading(readings,end_at)
    if not s or not e or e.id==s.id or e.taken_at<=s.taken_at:return None
    return round(e.reading_kwh-s.reading_kwh,3)

def plan_allocation(master_kwh,actuals,predictions):
    """实抄先入账，余量按预测分配，解释不了的挂账。
    actuals/predictions: {block_id:kwh}；返回(entries,inverted)，entries=[(kind,block_id,kwh)]。"""
    if master_kwh<-EPS:return [],True
    if any(v<-EPS for v in actuals.values()):return [],True  # 分表倒挂
    entries=[('actual',bid,round(kwh,3)) for bid,kwh in actuals.items()]
    remainder=round(master_kwh-sum(actuals.values()),3)
    if predictions and remainder>=-EPS:
        remainder=max(0.0,remainder)
        weights={bid:max(0.0,p) for bid,p in predictions.items()}
        total=sum(weights.values())
        if total<=EPS:weights={bid:1.0 for bid in predictions};total=float(len(predictions))
        shares={bid:round(remainder*w/total,3) for bid,w in weights.items()}
        drift=round(remainder-sum(shares.values()),3)  # 舍入差并回最大的一份，账必须平
        if shares:
            biggest=max(shares,key=lambda b:shares[b])
            shares[biggest]=round(shares[biggest]+drift,3)
        entries+= [('estimate',bid,v) for bid,v in shares.items() if v>EPS]
        residual=round(master_kwh-sum(e[2] for e in entries),3)
    else:residual=remainder  # 没有漏抄阵列或余量为负：差额解释不了
    if abs(residual)>EPS:entries.append(('suspense',None,residual))
    return entries,False

def window_token(master_kwh,actuals,predictions):
    """入账依据指纹：依据不变则重试/重入不重复入账。"""
    payload={'m':round(master_kwh,3),'a':{str(k):round(v,3) for k,v in sorted(actuals.items())},'p':{str(k):round(v,3) for k,v in sorted(predictions.items())}}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]

def affected_by_array_reading(windows,taken_at,tol_hours=BOUNDARY_HOURS):
    """晚到的分表记录影响边界落在其容差内及之后的所有窗口。"""
    low=taken_at-timedelta(hours=tol_hours)
    return [w for w in windows if w.end_at>=low]

def affected_by_array_correction(windows,taken_at,tol_hours=BOUNDARY_HOURS):
    """分表表底更正只影响以其为边界抄齐依据的窗口。"""
    tol=timedelta(hours=tol_hours)
    return [w for w in windows if abs(w.start_at-taken_at)<=tol or abs(w.end_at-taken_at)<=tol]

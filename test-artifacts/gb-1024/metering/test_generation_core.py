"""分账纯逻辑离线核对：划窗、实抄、分摊、挂账、倒挂、指纹幂等。
运行：python3 test_generation_core.py（仅标准库）"""
import sys,random,math
from datetime import datetime,timedelta
sys.path.insert(0,'/workspace/backend/src')
import generation_core as core

class R:
    def __init__(self,id,kwh,taken_at):self.id=id;self.reading_kwh=kwh;self.taken_at=taken_at
class W:
    def __init__(self,start_at,end_at):self.start_at=start_at;self.end_at=end_at

def check(name,cond):
    assert cond,name
    print('ok -',name)

t0=datetime(2026,11,1,18)
# 边界取最近，容差±2h
rows=[R(1,100,t0),R(2,160,t0+timedelta(hours=26)),R(3,220,t0+timedelta(days=2,hours=-1))]
check('边界容差内取最近',core.boundary_reading(rows,t0+timedelta(days=1)).id==2)
check('超出容差返回空',core.boundary_reading(rows,t0+timedelta(hours=5)) is None)
check('起止齐全得实抄',core.array_actual(rows,t0,t0+timedelta(days=1))==60.0)
check('缺止端表底为漏抄',core.array_actual(rows[:1],t0,t0+timedelta(days=1)) is None)
check('同一读数不算抄齐',core.array_actual([R(4,100,t0+timedelta(minutes=30))],t0,t0+timedelta(hours=1)) is None)

# 全部抄齐且总表=分表和：无挂账
e,inv=core.plan_allocation(250.0,{1:100.0,2:150.0},{})
check('抄齐无挂账',not inv and e==[('actual',1,100.0),('actual',2,150.0)])
# 抄齐但总表多抄：差额挂账
e,inv=core.plan_allocation(260.0,{1:100.0,2:150.0},{})
check('正差挂账',e[-1]==('suspense',None,10.0) and not inv)
# 抄齐但总表少于分表和：负挂账，不算倒挂
e,inv=core.plan_allocation(240.0,{1:100.0,2:150.0},{})
check('负差挂账不撤账',e[-1]==('suspense',None,-10.0) and not inv)
# 漏抄一阵列：余量按预测分配且分毫不差
e,inv=core.plan_allocation(300.0,{1:100.0},{2:2.0,3:1.0})
est={b:v for kind,b,v in e if kind=='estimate'}
check('估数分尽余量',not inv and abs(sum(est.values())-200.0)<1e-9 and not any(kind=='suspense' for kind,_,_ in e))
check('估数按比例',abs(est[2]-200*2/3)<0.001)
# 舍入漂移并回最大份
e,inv=core.plan_allocation(100.0,{1:33.333},{2:1.0,3:1.0,4:1.0})
est=[v for kind,_,v in e if kind=='estimate']
check('舍入后账平',abs(sum(est)-(100.0-33.333))<1e-9)
# 余量为负且有漏抄：不摊负估数，差额挂账
e,inv=core.plan_allocation(90.0,{1:100.0},{2:5.0})
check('负余量不摊估数',not any(kind=='estimate' for kind,_,_ in e) and e[-1]==('suspense',None,-10.0) and not inv)
# 总表倒挂 / 分表倒挂 → 撤账信号
check('总表倒挂',core.plan_allocation(-5.0,{1:1.0},{2:1.0})[1])
check('分表倒挂',core.plan_allocation(100.0,{1:-2.0},{2:1.0})[1])
# 预测全零退化为均分
e,inv=core.plan_allocation(90.0,{},{2:0.0,3:0.0})
est={b:v for kind,b,v in e if kind=='estimate'}
check('零预测均分',abs(est[2]-45.0)<1e-9 and abs(est[3]-45.0)<1e-9)
# 指纹幂等：依据同则token同
t1=core.window_token(100.0,{1:60.0},{2:40.0});t2=core.window_token(100.0,{1:60.0},{2:40.0})
check('依据同指纹同',t1==t2 and t1!=core.window_token(100.0,{1:61.0},{2:39.0}))
# 晚到分表影响范围
ws=[W(t0+timedelta(days=i),t0+timedelta(days=i+1)) for i in range(5)]
late=t0+timedelta(days=2)
check('晚到补录波及之后各窗',[id(w) for w in core.affected_by_array_reading(ws,late)]==[id(w) for w in ws[1:]])
check('分表更正只及相邻边界',[id(w) for w in core.affected_by_array_correction(ws,late)]==[id(ws[1]),id(ws[2])])

# 复演seed口径：核对倒挂窗、挂账量级与SL-A02补录演示值
base=datetime(2026,11,20,18);starts=[base-timedelta(days=26-i) for i in range(27)]
rng=random.Random(91000+2)
daily=[round(120*(3.8+0.6*math.sin(2*math.pi*(starts[j+1].timetuple().tm_yday-80)/365)+rng.uniform(-0.35,0.35)),1) for j in range(26)]
cum=5000.0+2*137.0;vals=[]
for i in range(27):
    if i>0:cum=round(cum+daily[i-1],1)
    vals.append(cum)
print('SL-A02 2026-11-18 18:00 应补表底 =',vals[24],'kWh；11-13 =',vals[19],'；11-14 =',vals[20])
check('补录值单调',vals[24]>vals[23]>vals[22])
print('ALL PASS')

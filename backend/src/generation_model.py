"""分阵列发电量预测：只用抄齐的时段训练，按日期留出报误差。
样本不足时退化为按装机×时长分摊，证据里如实标注，不伪装成模型输出。"""
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
FEATURES=['capacity_kw','slope','area_m2','duration_hours','month_sin','month_cos']
MIN_SAMPLES=10

def _row(block,start,end):
    hours=(end-start).total_seconds()/3600
    angle=2*np.pi*(start.month-1)/12
    return [block.capacity_kw,block.slope,block.area_m2,hours,float(np.sin(angle)),float(np.cos(angle))]

def train_model(samples):
    """samples=[(block,start,end,kwh)]，全部来自抄齐窗口；按窗口结束日期留出约20%报误差。"""
    if len(samples)<MIN_SAMPLES:return None,{'mode':'capacity_fallback','samples':len(samples),'reason':f'抄齐样本{len(samples)}条，不足{MIN_SAMPLES}条，按装机×时长分摊'}
    x=[_row(b,s,e) for b,s,e,_ in samples];y=[kwh for *_,kwh in samples]
    dates=sorted({e.date() for _,_,e,_ in samples})
    hold=set(dates[-max(1,round(len(dates)*0.2)):]) if len(dates)>1 else set()
    train_i=[i for i,(_,_,e,_) in enumerate(samples) if e.date() not in hold]
    hold_i=[i for i,(_,_,e,_) in enumerate(samples) if e.date() in hold]
    if not train_i:return None,{'mode':'capacity_fallback','samples':len(samples),'reason':'留出后无训练样本，按装机×时长分摊'}
    model=RandomForestRegressor(n_estimators=100,max_depth=8,min_samples_leaf=2,random_state=42,n_jobs=1)
    model.fit([x[i] for i in train_i],[y[i] for i in train_i])
    evidence={'mode':'random_forest','samples':len(y),'train_rows':len(train_i),'holdout_rows':len(hold_i),'holdout_dates':sorted(str(d) for d in hold),'train_mae_kwh':float(mean_absolute_error([y[i] for i in train_i],model.predict([x[i] for i in train_i]))),'holdout_mae_kwh':float(mean_absolute_error([y[i] for i in hold_i],model.predict([x[i] for i in hold_i]))) if hold_i else None,'feature_importance':dict(zip(FEATURES,[round(float(v),4) for v in model.feature_importances_])),'random_state':42,'method':'RandomForestRegressor / 100 trees / max_depth 8'}
    return model,evidence

def predict(model,block,start,end):
    return max(0.0,float(model.predict([_row(block,start,end)])[0]))

def capacity_weights(blocks,start,end):
    """模型不可用时的兜底权重：装机×时长，只作分摊比例。"""
    hours=(end-start).total_seconds()/3600
    return {b.id:round(b.capacity_kw*hours,3) for b in blocks}

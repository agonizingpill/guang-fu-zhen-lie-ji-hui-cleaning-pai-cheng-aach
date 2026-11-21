"""发电量分账：总表相邻抄数划窗，实抄先入账，余量按预测分配，解释不了的挂账。
表底更正时两侧窗口一起重算，任一倒挂就一起撤账待核；晚到的分表记录按窗口冲回估数再补记。"""
from sqlalchemy import select,func,or_
from fastapi import HTTPException
from .models import ArrayBlock,Audit
from .generation_models import MeterGroup,MeterGroupMember,MeterReading,GenerationWindow,AllocationBatch,GenerationEntry,ReadingCorrection
from .generation_core import EPS,array_actual,plan_allocation,window_token,affected_by_array_reading,affected_by_array_correction
from .generation_model import train_model,predict,capacity_weights
from .field_service import require

def log(db,actor,msg):db.add(Audit(actor=actor,action=msg))

def group_blocks(db,group_id):
    return db.scalars(select(ArrayBlock).join(MeterGroupMember,MeterGroupMember.block_id==ArrayBlock.id).where(MeterGroupMember.group_id==group_id).order_by(ArrayBlock.id)).all()

def block_readings(db,block_id,ctx):
    if block_id not in ctx['readings']:
        ctx['readings'][block_id]=db.scalars(select(MeterReading).where(MeterReading.block_id==block_id).order_by(MeterReading.taken_at,MeterReading.id)).all()
    return ctx['readings'][block_id]

def collect_samples(db,group_id,blocks,before=None,ctx=None):
    """训练样本=本组结束于before之前、且全部阵列抄齐的窗口；待核窗口不用。"""
    query=select(GenerationWindow).where(GenerationWindow.group_id==group_id,GenerationWindow.state!='pending_review')
    if before is not None:query=query.where(GenerationWindow.end_at<=before)
    past=db.scalars(query.order_by(GenerationWindow.end_at)).all()
    samples=[];ids=[]
    for w in past:
        per={};ok=True
        for b in blocks:
            rows=block_readings(db,b.id,ctx) if ctx is not None else db.scalars(select(MeterReading).where(MeterReading.block_id==b.id).order_by(MeterReading.taken_at)).all()
            kwh=array_actual(rows,w.start_at,w.end_at)
            if kwh is None or kwh<-EPS:ok=False;break
            per[b.id]=kwh
        if ok:
            ids.append(w.id)
            for b in blocks:samples.append((b,w.start_at,w.end_at,per[b.id]))
    return samples,ids

def predict_missing(db,window,missing,ctx):
    """漏抄阵列的余量分摊权重：优先随机森林预测，样本不足按装机×时长兜底。"""
    blocks=group_blocks(db,window.group_id)
    samples,ids=collect_samples(db,window.group_id,blocks,before=window.start_at,ctx=ctx)
    key=(window.group_id,tuple(ids))
    if key not in ctx['models']:ctx['models'][key]=train_model(samples)
    model,evidence=ctx['models'][key]
    evidence=dict(evidence);evidence['sample_windows']=len(ids)
    if model is None:return capacity_weights(missing,window.start_at,window.end_at),evidence
    return {b.id:round(predict(model,b,window.start_at,window.end_at),3) for b in missing},evidence

def window_facts(db,window,ctx):
    """窗口入账依据：总表电量、各阵列实抄、漏抄阵列的预测权重。"""
    blocks=group_blocks(db,window.group_id)
    start=db.get(MeterReading,window.start_reading_id);end=db.get(MeterReading,window.end_reading_id)
    master=round(end.reading_kwh-start.reading_kwh,3)
    actuals={};missing=[]
    for b in blocks:
        kwh=array_actual(block_readings(db,b.id,ctx),window.start_at,window.end_at)
        if kwh is None:missing.append(b)
        else:actuals[b.id]=kwh
    predictions={};evidence=None
    if missing and master>-EPS:predictions,evidence=predict_missing(db,window,missing,ctx)
    return master,actuals,predictions,evidence

def recompute_windows(db,windows,actor,reason):
    """一组窗口一起重算：先冲回在账分录，任一倒挂则整组撤账待核，否则按最新依据入账。
    依据(token)不变的窗口直接跳过，重试不会重复入账。"""
    if not windows:return []
    ctx={'readings':{},'models':{}}
    plans=[(w,)+window_facts(db,w,ctx) for w in windows]
    any_inverted=any(master<-EPS or any(v<-EPS for v in actuals.values()) for _,master,actuals,_,_ in plans)
    results=[]
    for w,master,actuals,predictions,evidence in plans:
        token=window_token(master,actuals,predictions)
        if any_inverted:
            if w.state=='pending_review' and w.token==token:results.append((w,'unchanged'));continue
        elif w.state=='allocated' and w.token==token:results.append((w,'unchanged'));continue
        batch=AllocationBatch(window_id=w.id,reason=reason,actor=actor,evidence=evidence)
        db.add(batch);db.flush()
        posted=db.scalars(select(GenerationEntry).where(GenerationEntry.window_id==w.id,GenerationEntry.state=='posted')).all()
        for e in posted:e.state='reversed';e.reversed_batch_id=batch.id  # 冲回旧账，保留痕迹
        w.master_kwh=master;w.token=token
        if any_inverted:
            w.state='pending_review';results.append((w,'suspended'))
        else:
            entries,_=plan_allocation(master,actuals,predictions)
            for kind,bid,kwh in entries:db.add(GenerationEntry(window_id=w.id,batch_id=batch.id,block_id=bid,kind=kind,kwh=kwh))
            w.state='allocated';results.append((w,'allocated'))
    return results

def record_master(db,body,actor):
    group=require(db,MeterGroup,body.group_id)
    last=db.scalars(select(MeterReading).where(MeterReading.group_id==group.id).order_by(MeterReading.taken_at.desc(),MeterReading.id.desc())).first()
    if last and body.taken_at<=last.taken_at:raise HTTPException(409,'总表抄数必须晚于最新一条表底；修改历史表底请走更正')
    reading=MeterReading(group_id=group.id,reading_kwh=body.reading_kwh,taken_at=body.taken_at,note=body.note,created_by=actor)
    db.add(reading);db.flush()
    window=None
    if last:
        window=GenerationWindow(group_id=group.id,start_reading_id=last.id,end_reading_id=reading.id,start_at=last.taken_at,end_at=reading.taken_at,master_kwh=round(reading.reading_kwh-last.reading_kwh,3))
        db.add(window);db.flush()
        recompute_windows(db,[window],actor,'initial')
    log(db,actor,f'总表抄数 {group.code} {body.reading_kwh}kWh @{body.taken_at}'+(f'，关窗WINDOW-{window.id}({window.state})' if window else ''))
    db.commit();return reading,window

def record_array(db,body,actor):
    block=require(db,ArrayBlock,body.block_id)
    member=db.scalar(select(MeterGroupMember).where(MeterGroupMember.block_id==block.id))
    if not member:raise HTTPException(409,'该阵列未挂总表组，请先建立分账组')
    if db.scalar(select(MeterReading).where(MeterReading.block_id==block.id,MeterReading.taken_at==body.taken_at)):raise HTTPException(409,'该时刻已有分表表底，重复提交不会重复入账；改数请走更正')
    reading=MeterReading(block_id=block.id,reading_kwh=body.reading_kwh,taken_at=body.taken_at,note=body.note,created_by=actor)
    db.add(reading);db.flush()
    open_windows=db.scalars(select(GenerationWindow).where(GenerationWindow.group_id==member.group_id,GenerationWindow.state=='allocated').order_by(GenerationWindow.end_at)).all()
    affected=affected_by_array_reading(open_windows,body.taken_at)
    results=recompute_windows(db,affected,actor,'late_reading')
    changed=[w.id for w,r in results if r!='unchanged']
    log(db,actor,f'分表抄数 {block.code} {body.reading_kwh}kWh @{body.taken_at}'+(f'，晚到补记冲回重算{len(changed)}窗' if changed else ''))
    db.commit();return reading,changed

def correct_reading(db,key,body,actor):
    reading=require(db,MeterReading,key,True)
    if abs(reading.reading_kwh-body.reading_kwh)<EPS:raise HTTPException(409,'更正值与现值一致，无需更正')
    db.add(ReadingCorrection(reading_id=reading.id,old_kwh=reading.reading_kwh,new_kwh=body.reading_kwh,reason=body.reason,actor=actor))
    old=reading.reading_kwh;reading.reading_kwh=body.reading_kwh
    if reading.group_id:  # 总表表底：两侧窗口一起重算
        windows=db.scalars(select(GenerationWindow).where(or_(GenerationWindow.start_reading_id==reading.id,GenerationWindow.end_reading_id==reading.id)).order_by(GenerationWindow.end_at)).all()
    else:
        member=db.scalar(select(MeterGroupMember).where(MeterGroupMember.block_id==reading.block_id))
        windows=affected_by_array_correction(db.scalars(select(GenerationWindow).where(GenerationWindow.group_id==member.group_id).order_by(GenerationWindow.end_at)).all(),reading.taken_at) if member else []
    results=recompute_windows(db,windows,actor,'correction')
    suspended=[w.id for w,r in results if r=='suspended']
    log(db,actor,f'更正表底#{reading.id} {old}→{body.reading_kwh}kWh（{body.reason}），两侧{len(windows)}窗重算'+(f'，倒挂撤账待核{suspended}' if suspended else ''))
    db.commit();return reading,results

def review_window(db,key,actor):
    window=require(db,GenerationWindow,key,True)
    results=recompute_windows(db,[window],actor,'review')
    log(db,actor,f'复核重算 WINDOW-{window.id}，结果{results[0][1]}')
    db.commit();return window,results[0][1]

def create_group(db,body,actor):
    if db.scalar(select(MeterGroup).where(MeterGroup.code==body.code)):raise HTTPException(409,'分账组编号已存在')
    group=MeterGroup(code=body.code,name=body.name);db.add(group);db.flush()
    for bid in dict.fromkeys(body.block_ids):
        block=require(db,ArrayBlock,bid)
        if db.scalar(select(MeterGroupMember).where(MeterGroupMember.block_id==bid)):raise HTTPException(409,f'阵列 {block.code} 已挂在其他总表组')
        db.add(MeterGroupMember(group_id=group.id,block_id=bid))
    log(db,actor,f'建立分账组 {body.code}，成员{len(body.block_ids)}个阵列')
    db.commit();return group

_GROUP_MODEL_CACHE={}
def group_json(db,g):
    blocks=group_blocks(db,g.id)
    samples,ids=collect_samples(db,g.id,blocks,ctx={'readings':{},'models':{}})
    stamp=(db.scalar(select(func.max(MeterReading.id))) or 0,db.scalar(select(func.count()).select_from(ReadingCorrection)))
    key=(g.id,tuple(ids),stamp)
    if len(_GROUP_MODEL_CACHE)>64:_GROUP_MODEL_CACHE.clear()
    if key not in _GROUP_MODEL_CACHE:_GROUP_MODEL_CACHE[key]=train_model(samples)[1]
    evidence=dict(_GROUP_MODEL_CACHE[key]);evidence['sample_windows']=len(ids)
    last=db.scalars(select(MeterReading).where(MeterReading.group_id==g.id).order_by(MeterReading.taken_at.desc(),MeterReading.id.desc())).first()
    return {'id':g.id,'code':g.code,'name':g.name,'members':[{'block_id':b.id,'code':b.code,'name':b.name,'capacity_kw':b.capacity_kw} for b in blocks],'model':evidence,'window_count':db.scalar(select(func.count()).select_from(GenerationWindow).where(GenerationWindow.group_id==g.id)),'pending_count':db.scalar(select(func.count()).select_from(GenerationWindow).where(GenerationWindow.group_id==g.id,GenerationWindow.state=='pending_review')),'last_reading_kwh':last.reading_kwh if last else None,'last_reading_at':last.taken_at if last else None}

def window_json(db,w):
    entries=db.scalars(select(GenerationEntry).where(GenerationEntry.window_id==w.id).order_by(GenerationEntry.id)).all()
    posted=[e for e in entries if e.state=='posted']
    booked=round(sum(e.kwh for e in posted),3)
    return {'id':w.id,'group_id':w.group_id,'start_at':w.start_at,'end_at':w.end_at,'master_kwh':w.master_kwh,'state':w.state,'booked_kwh':booked,'diff_kwh':round(w.master_kwh-booked,3),'reversed_count':len(entries)-len(posted),'entries':[{'id':e.id,'block_id':e.block_id,'kind':e.kind,'kwh':e.kwh} for e in posted]}

def array_ledger(db,block_id):
    block=require(db,ArrayBlock,block_id)
    member=db.scalar(select(MeterGroupMember).where(MeterGroupMember.block_id==block_id))
    if not member:raise HTTPException(409,'该阵列未挂总表组，无分账台账')
    group=db.get(MeterGroup,member.group_id)
    rows=[]
    windows=db.scalars(select(GenerationWindow).where(GenerationWindow.group_id==group.id).order_by(GenerationWindow.end_at.desc(),GenerationWindow.id.desc())).all()
    for w in windows:
        posted=db.scalars(select(GenerationEntry).where(GenerationEntry.window_id==w.id,GenerationEntry.state=='posted')).all()
        mine={e.kind:e.kwh for e in posted if e.block_id==block_id}
        booked=round(sum(e.kwh for e in posted),3)
        rows.append({'window_id':w.id,'start_at':w.start_at,'end_at':w.end_at,'state':w.state,'master_kwh':w.master_kwh,'actual_kwh':mine.get('actual'),'estimate_kwh':mine.get('estimate'),'suspense_kwh':round(sum(e.kwh for e in posted if e.kind=='suspense'),3),'booked_kwh':booked,'diff_kwh':round(w.master_kwh-booked,3)})
    return {'block':block,'group':group,'windows':rows}

from fastapi import APIRouter,Depends
from sqlalchemy import select
from .database import session
from .auth import current,editor,admin
from .generation_models import MeterGroup,MeterReading,GenerationWindow
from .generation_schemas import GroupInput,MasterReadingInput,ArrayReadingInput,CorrectInput
from . import generation_service as svc
router=APIRouter(prefix='/generation')
@router.get('/groups')
def groups(u=Depends(current),db=Depends(session)):return [svc.group_json(db,g) for g in db.scalars(select(MeterGroup).order_by(MeterGroup.id)).all()]
@router.post('/groups')
def create_group(b:GroupInput,u=Depends(admin),db=Depends(session)):return svc.create_group(db,b,u.email)
@router.get('/readings')
def readings(u=Depends(current),db=Depends(session)):return db.scalars(select(MeterReading).order_by(MeterReading.taken_at.desc(),MeterReading.id.desc()).limit(200)).all()
@router.post('/readings/master')
def master(b:MasterReadingInput,u=Depends(editor),db=Depends(session)):
    reading,window=svc.record_master(db,b,u.email)
    return {'reading':reading,'window_id':window.id if window else None,'window_state':window.state if window else None}
@router.post('/readings/array')
def array(b:ArrayReadingInput,u=Depends(editor),db=Depends(session)):
    reading,changed=svc.record_array(db,b,u.email)
    return {'reading':reading,'recomputed_windows':changed}
@router.post('/readings/{key}/correct')
def correct(key:int,b:CorrectInput,u=Depends(admin),db=Depends(session)):
    reading,results=svc.correct_reading(db,key,b,u.email)
    return {'reading':reading,'windows':[{'window_id':w.id,'result':r} for w,r in results]}
@router.get('/windows')
def windows(group_id:int|None=None,u=Depends(current),db=Depends(session)):
    query=select(GenerationWindow).order_by(GenerationWindow.end_at.desc(),GenerationWindow.id.desc()).limit(100)
    if group_id:query=query.where(GenerationWindow.group_id==group_id)
    return [svc.window_json(db,w) for w in db.scalars(query).all()]
@router.post('/windows/{key}/recompute')
def recompute(key:int,u=Depends(admin),db=Depends(session)):
    window,result=svc.review_window(db,key,u.email)
    return {'window_id':window.id,'state':window.state,'result':result}
@router.get('/arrays/{block_id}/ledger')
def ledger(block_id:int,u=Depends(current),db=Depends(session)):return svc.array_ledger(db,block_id)

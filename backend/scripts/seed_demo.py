from datetime import datetime,timedelta
from sqlalchemy import select,func
from src.database import Base,engine,Session
from src.models import User,ArrayBlock,SoilingSurvey,WashMission
from src.revisit_models import WaterDelivery,FollowupSurvey
from src.auth import hash_password
from src.operations_models import WashCrew

def seed():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ['admin','planner','viewer']:
            email=role+'@qingling.test'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,password=hash_password('Solar2026!'),role=role))
        for code,minutes in [('CREW-SOUTH',480),('CREW-RIDGE',360)]:
            if not db.scalar(select(WashCrew).where(WashCrew.code==code)):db.add(WashCrew(code=code,max_daily_minutes=minutes))
        blocks=[('SL-A01','南坡一号','南坡',150,23,780,1500),('SL-A02','南坡二号','南坡',120,25,630,1200),('SL-B01','脊线一号','脊线',100,18,520,900),('SL-C01','西坡一号','西坡',80,30,420,600)]
        for code,name,sector,cap,slope,area,water in blocks:
            if not db.scalar(select(ArrayBlock).where(ArrayBlock.code==code)):db.add(ArrayBlock(code=code,name=name,sector=sector,capacity_kw=cap,slope=slope,area_m2=area,water_liters=water))
        db.flush();rows=db.scalars(select(ArrayBlock).order_by(ArrayBlock.id)).all()
        measurements=[('SV-1117-A01',rows[0],800,35,18,3.2,88),('SV-1117-A02',rows[1],820,33,12,2.1,76),('SV-CLOUD-01',rows[2],240,23,8,1.2,18)]
        for code,block,irr,temp,rain,dust,power in measurements:
            if not db.scalar(select(SoilingSurvey).where(SoilingSurvey.code==code)):db.add(SoilingSurvey(code=code,block_id=block.id,capacity_kw=block.capacity_kw,slope=block.slope,irradiance=irr,module_temp=temp,rain_days=rain,dust=dust,observed_kw=power,state='recorded'))
        db.flush()
        block=db.scalar(select(ArrayBlock).where(ArrayBlock.code=='SL-C01'))
        if not db.scalar(select(WaterDelivery).where(WaterDelivery.delivery_code=='WD-DEMO-01')):
            db.add(WaterDelivery(block_id=block.id,delivery_code='WD-DEMO-01',liters=500,source='西坡蓄水池 / 水车02'))
        historical=db.scalar(select(SoilingSurvey).where(SoilingSurvey.code=='SV-DEMO-CLEAN'))
        if not historical:
            historical=SoilingSurvey(code='SV-DEMO-CLEAN',block_id=block.id,capacity_kw=block.capacity_kw,slope=block.slope,irradiance=800,module_temp=35,rain_days=15,dust=2,observed_kw=50,clean_kw=62,predicted_loss=12,recommended_water=100,state='closed')
            db.add(historical);db.flush()
        mission=db.scalar(select(WashMission).where(WashMission.survey_id==historical.id))
        if not mission:
            mission=WashMission(survey_id=historical.id,water_liters=100,available_liters=700,status='accepted',post_kw=62,post_irradiance=800,normalized_kw=62,verified_gain=12,dispatched_at=datetime.utcnow()-timedelta(days=7),accepted_at=datetime.utcnow()-timedelta(days=7))
            db.add(mission);db.flush()
        if not db.scalar(select(FollowupSurvey).where(FollowupSurvey.mission_id==mission.id,FollowupSurvey.days_after==2)):
            db.add(FollowupSurvey(mission_id=mission.id,days_after=2,irradiance=800,observed_kw=61,normalized_kw=61,normalized_loss=100/62,state='stable',baseline_snapshot={'post_kw':62,'post_irradiance':800,'accepted_at':mission.accepted_at.isoformat(),'block_id':block.id,'block_code':block.code,'survey_id':historical.id,'survey_code':historical.code,'threshold_pct':5}))
        seed_metering(db)
        db.commit();print({key:db.scalar(select(func.count()).select_from(model)) for key,model in [('users',User),('blocks',ArrayBlock),('surveys',SoilingSurvey)]})

def seed_metering(db):
    """分账演示：两组共用总表，26个日窗；SL-A02/SL-C01漏抄走估数，脊西组一条倒挂表底待更正。"""
    import random,math
    from datetime import datetime,timedelta
    from src.generation_models import MeterGroup,MeterGroupMember,MeterReading,GenerationWindow
    from src.generation_service import recompute_windows
    blocks={b.code:b for b in db.scalars(select(ArrayBlock)).all()}
    plan={'MG-SOUTH':('南坡总表',['SL-A01','SL-A02']),'MG-WEST':('脊西总表',['SL-B01','SL-C01'])}
    miss={'SL-A02':{'2026-11-13','2026-11-14','2026-11-18'},'SL-C01':{'2026-11-08'}}
    dip={'MG-WEST':23}  # 11-17总表表底少抄45kWh，形成倒挂窗与异常挂账，供更正演示
    base=datetime(2026,11,20,18)
    starts=[base-timedelta(days=26-i) for i in range(27)]
    for code,(name,codes) in plan.items():
        if db.scalar(select(MeterGroup).where(MeterGroup.code==code)):continue
        group=MeterGroup(code=code,name=name);db.add(group);db.flush()
        members=[blocks[c] for c in codes]
        for b in members:db.add(MeterGroupMember(group_id=group.id,block_id=b.id))
        rng=random.Random(20261120+group.id)
        daily={}
        for b in members:
            brng=random.Random(91000+b.id)
            daily[b.id]=[round(b.capacity_kw*(3.8+0.6*math.sin(2*math.pi*(starts[j+1].timetuple().tm_yday-80)/365)+brng.uniform(-0.35,0.35)),1) for j in range(26)]
        mdaily=[round(sum(daily[b.id][j] for b in members)*1.003+rng.uniform(-1.5,1.5),1) for j in range(26)]
        for b in members:
            cum=5000.0+b.id*137.0
            for i,t in enumerate(starts):
                if i>0:cum=round(cum+daily[b.id][i-1],1)
                if b.code in miss and t.date().isoformat() in miss[b.code]:continue
                db.add(MeterReading(block_id=b.id,reading_kwh=cum,taken_at=t,note='seed',created_by='seed@qingling.test'))
        mcum=20000.0+group.id*733.0
        master_rows=[]
        for i,t in enumerate(starts):
            if i>0:mcum=round(mcum+mdaily[i-1],1)
            master_rows.append(MeterReading(group_id=group.id,reading_kwh=mcum,taken_at=t,note='seed',created_by='seed@qingling.test'))
        if code in dip:master_rows[dip[code]].reading_kwh=round(master_rows[dip[code]-1].reading_kwh-45.0,1)
        for r in master_rows:db.add(r)
        db.flush()
        prev=None
        for r in master_rows:
            if prev:
                window=GenerationWindow(group_id=group.id,start_reading_id=prev.id,end_reading_id=r.id,start_at=prev.taken_at,end_at=r.taken_at,master_kwh=round(r.reading_kwh-prev.reading_kwh,3))
                db.add(window);db.flush()
                recompute_windows(db,[window],'seed@qingling.test','initial')
            prev=r
if __name__=='__main__':seed()

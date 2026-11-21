from datetime import datetime
from sqlalchemy import String,Float,ForeignKey,JSON,CheckConstraint,DateTime,UniqueConstraint,func
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base

class MeterGroup(Base):
    __tablename__='meter_groups'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    name:Mapped[str]=mapped_column(String(80))

class MeterGroupMember(Base):
    __tablename__='meter_group_members'
    __table_args__=(UniqueConstraint('block_id'),)  # 一个阵列只挂一块总表，分摊才不含糊
    id:Mapped[int]=mapped_column(primary_key=True)
    group_id:Mapped[int]=mapped_column(ForeignKey('meter_groups.id'))
    block_id:Mapped[int]=mapped_column(ForeignKey('array_blocks.id'))

class MeterReading(Base):
    __tablename__='meter_readings'
    __table_args__=(UniqueConstraint('group_id','taken_at'),UniqueConstraint('block_id','taken_at'),CheckConstraint('reading_kwh>=0'),CheckConstraint('(group_id IS NULL)!=(block_id IS NULL)'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    group_id:Mapped[int|None]=mapped_column(ForeignKey('meter_groups.id'),nullable=True)  # 总表表底
    block_id:Mapped[int|None]=mapped_column(ForeignKey('array_blocks.id'),nullable=True)  # 分表表底
    reading_kwh:Mapped[float]=mapped_column(Float)
    taken_at:Mapped[datetime]=mapped_column(DateTime)
    note:Mapped[str]=mapped_column(String(200),default='')
    created_by:Mapped[str]=mapped_column(String(120),default='')
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())

class GenerationWindow(Base):
    __tablename__='generation_windows'
    __table_args__=(UniqueConstraint('group_id','start_reading_id'),UniqueConstraint('group_id','end_reading_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    group_id:Mapped[int]=mapped_column(ForeignKey('meter_groups.id'))
    start_reading_id:Mapped[int]=mapped_column(ForeignKey('meter_readings.id'))
    end_reading_id:Mapped[int]=mapped_column(ForeignKey('meter_readings.id'))
    start_at:Mapped[datetime]=mapped_column(DateTime)
    end_at:Mapped[datetime]=mapped_column(DateTime)
    master_kwh:Mapped[float]=mapped_column(Float)
    state:Mapped[str]=mapped_column(String(24),default='allocated')  # allocated / pending_review
    token:Mapped[str]=mapped_column(String(64),default='')  # 入账依据指纹，重试不重复入账

class AllocationBatch(Base):
    __tablename__='allocation_batches'
    id:Mapped[int]=mapped_column(primary_key=True)
    window_id:Mapped[int]=mapped_column(ForeignKey('generation_windows.id'))
    reason:Mapped[str]=mapped_column(String(24))  # initial / correction / late_reading / review
    actor:Mapped[str]=mapped_column(String(120),default='')
    evidence:Mapped[dict|None]=mapped_column(JSON,nullable=True)  # 本次分摊所用模型证据
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())

class GenerationEntry(Base):
    __tablename__='generation_entries'
    __table_args__=(CheckConstraint("kind='suspense' OR kwh>=0"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    window_id:Mapped[int]=mapped_column(ForeignKey('generation_windows.id'))
    batch_id:Mapped[int]=mapped_column(ForeignKey('allocation_batches.id'))
    block_id:Mapped[int|None]=mapped_column(ForeignKey('array_blocks.id'),nullable=True)  # 挂账不归属阵列
    kind:Mapped[str]=mapped_column(String(10))  # actual实抄 / estimate估数 / suspense挂账
    kwh:Mapped[float]=mapped_column(Float)
    state:Mapped[str]=mapped_column(String(10),default='posted')  # posted / reversed
    reversed_batch_id:Mapped[int|None]=mapped_column(ForeignKey('allocation_batches.id'),nullable=True)

class ReadingCorrection(Base):
    __tablename__='reading_corrections'
    id:Mapped[int]=mapped_column(primary_key=True)
    reading_id:Mapped[int]=mapped_column(ForeignKey('meter_readings.id'))
    old_kwh:Mapped[float]=mapped_column(Float)
    new_kwh:Mapped[float]=mapped_column(Float)
    reason:Mapped[str]=mapped_column(String(300))
    actor:Mapped[str]=mapped_column(String(120))
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())

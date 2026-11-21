from datetime import datetime
from pydantic import Field,field_validator
from .revisit_schemas import Inputs
class GroupInput(Inputs):
    code:str=Field(min_length=2,max_length=40)
    name:str=Field(min_length=2,max_length=80)
    block_ids:list[int]=Field(min_length=1,max_length=50)
class MasterReadingInput(Inputs):
    group_id:int
    taken_at:datetime
    reading_kwh:float=Field(ge=0,le=100000000)
    note:str=Field(default='',max_length=200)
class ArrayReadingInput(Inputs):
    block_id:int
    taken_at:datetime
    reading_kwh:float=Field(ge=0,le=100000000)
    note:str=Field(default='',max_length=200)
class CorrectInput(Inputs):
    reading_kwh:float=Field(ge=0,le=100000000)
    reason:str=Field(min_length=5,max_length=300)
    @field_validator('reason')
    @classmethod
    def reason(cls,v):
        if len(v.strip())<5:raise ValueError('更正依据至少5字')
        return v.strip()

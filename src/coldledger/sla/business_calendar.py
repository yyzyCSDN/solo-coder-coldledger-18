from dataclasses import dataclass
from datetime import datetime,date,time,timedelta
from zoneinfo import ZoneInfo
@dataclass(frozen=True)
class Calendar: zone:str;holidays:frozenset;open_time:time;close_time:time

def add_minutes(cal,start,minutes):
    if minutes<0:raise ValueError('negative duration')
    z=ZoneInfo(cal.zone);cur=start.astimezone(z);left=minutes
    while left:
        if cur.weekday()>=5 or cur.date() in cal.holidays:cur=datetime.combine(cur.date()+timedelta(days=1),cal.open_time,z);continue
        op=datetime.combine(cur.date(),cal.open_time,z);cl=datetime.combine(cur.date(),cal.close_time,z)
        if cur<op:cur=op
        if cur>=cl:cur=datetime.combine(cur.date()+timedelta(days=1),cal.open_time,z);continue
        take=min(left,int((cl-cur).total_seconds()//60));cur+=timedelta(minutes=take);left-=take
        if left and cur>=cl:cur=datetime.combine(cur.date()+timedelta(days=1),cal.open_time,z)
    return cur

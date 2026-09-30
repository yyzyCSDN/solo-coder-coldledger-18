from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
UTC=timezone.utc
def aware(v):
    if v.tzinfo is None or v.utcoffset() is None: raise ValueError('timezone-aware datetime required')
    return v
def utc(v): return aware(v).astimezone(UTC)
def seconds(a,b):
    a,b=utc(a),utc(b)
    if b<a: raise ValueError('end before start')
    return (b-a).total_seconds()
def wall_to_utc(v,zone,fold=0):
    if v.tzinfo is not None: raise ValueError('wall time must be naive')
    if fold not in (0,1): raise ValueError('invalid fold')
    return v.replace(tzinfo=ZoneInfo(zone),fold=fold).astimezone(UTC)
def bucket(v,minutes):
    if minutes<=0: raise ValueError('minutes must be positive')
    x=utc(v); step=minutes*60; epoch=int(x.timestamp()); return datetime.fromtimestamp(epoch-epoch%step,tz=UTC)
def local_date(v,zone): return utc(v).astimezone(ZoneInfo(zone)).date()
def add_local_days(v,zone,days):
    local=utc(v).astimezone(ZoneInfo(zone)); target=local.date()+timedelta(days=days)
    return datetime.combine(target,local.timetz().replace(tzinfo=None),ZoneInfo(zone)).astimezone(UTC)

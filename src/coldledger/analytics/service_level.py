from dataclasses import dataclass
from statistics import mean
@dataclass(frozen=True)
class Outcome:
    lane:str; on_time:bool; complete:bool; excursion:bool; delay_minutes:int; claim_value:float
def summarize(rows):
    groups={}
    for r in rows: groups.setdefault(r.lane,[]).append(r)
    out={}
    for lane,items in groups.items():
        n=len(items)
        out[lane]={'count':n,'on_time_rate':sum(x.on_time for x in items)/n,'complete_rate':sum(x.complete for x in items)/n,'excursion_rate':sum(x.excursion for x in items)/n,'avg_delay':mean(max(0,x.delay_minutes) for x in items),'claim_value':sum(x.claim_value for x in items)}
    return out
def weighted_score(stats):
    return 100*stats['on_time_rate']+80*stats['complete_rate']-150*stats['excursion_rate']-0.05*stats['avg_delay']-0.0001*stats['claim_value']
def ranking(rows):
    stats=summarize(rows);return sorted(stats,key=lambda lane:(-weighted_score(stats[lane]),lane))

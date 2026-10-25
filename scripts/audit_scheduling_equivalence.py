import sys, statistics, csv
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research_code"))
from p4lab.model import make_scenario, policy_view, SyntheticProcessor
from p4lab.policies import plan, estimated_gain
from p4lab.experiments import evaluate

def lawler_moore(obs, c=0.5):
    # Classical 1||sum w_j U_j pseudo-polynomial DP (Lawler & Moore 1969): EDD order + knapsack on time.
    jobs = sorted([p for p in obs if p.permitted and estimated_gain(p,c)>0], key=lambda p:(p.capture_at,p.uid))
    best = {0:(0.0,())}
    for p in jobs:
        new = dict(best)
        for t,(v,ids) in best.items():
            f = t+p.remedy_minutes
            if f < p.capture_at:
                cand=(v+estimated_gain(p,c), ids+(p.uid,))
                if f not in new or cand[0] > new[f][0]+1e-9: new[f]=cand
        best=new
    return max(best.values(), key=lambda x:x[0])[1]

def exp_value(obs, ids, c=0.5):
    L={o.uid:o for o in obs}; return sum(estimated_gain(L[i],c) for i in ids)

cfgs=[("main","normal",0.0,0.5),("tight","tight",0.0,0.5),("loose","loose",0.0,0.5),("high_collateral","normal",0.0,1.0)]
for name,urg,sh,c in cfgs:
    mism=0; same_obj=0; N=1200; nocap=[]; dp=[]; edf=[]; lm=[]; capbind=0
    for i in range(N):
        s=71231+i; pay=make_scenario(s,8,urg,sh); obs=policy_view(pay)
        a=plan(obs,"dp",c); b=lawler_moore(obs,c)
        if abs(exp_value(obs,a,c)-exp_value(obs,b,c))>1e-6: mism+=1
        # unlimited-capacity bound: cancel every positive-gain permitted item that is individually feasible
        allv=tuple(o.uid for o in obs if o.permitted and estimated_gain(o,c)>0 and o.remedy_minutes<o.capture_at)
        if set(allv)!=set(a): capbind+=1
        proc=SyntheticProcessor(pay)
        for u in allv: proc.cancel(u,0,"remediation_operator",u)
        tr=proc.score_truth(10**6); nocap.append(tr["suspect_captured_value"]+c*tr["legitimate_cancelled_value"])
        dp.append(evaluate(pay,"dp",c)["loss"]); edf.append(evaluate(pay,"edf",c)["loss"])
    print(f"{name:16} DP vs Lawler-Moore objective mismatches: {mism}/{N} | seeds where capacity binds for DP: {capbind}/{N} | mean loss DP={statistics.mean(dp):.1f} EDF={statistics.mean(edf):.1f} unlimited-capacity={statistics.mean(nocap):.1f}")

# seed overlap pilot vs main
def seeds(p): return {r["seed"] for r in csv.DictReader(open(p))}
ps=seeds(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","research_code","results")+"/pilot/trials.csv"); ms=seeds(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","research_code","results","main","trials.csv"))
print("pilot seeds",len(ps),"main seeds",len(ms),"overlap",len(ps&ms))

# DP runtime scaling
import time
for n in (8,10,12):
    t=time.time()
    for i in range(20): plan(policy_view(make_scenario(1000+i,n)),"dp")
    t1=time.time()-t; t=time.time()
    for i in range(20): lawler_moore(policy_view(make_scenario(1000+i,n)))
    print(f"n={n}: subset-DP {t1/20*1000:.1f} ms/plan, Lawler-Moore {(time.time()-t)/20*1000:.2f} ms/plan")

import sys; import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research_code"))
from p4lab.model import Payment, SyntheticProcessor, make_scenario
from p4lab.experiments import evaluate
# 1 key poisoning: unauthorized actor uses key first
s=SyntheticProcessor((Payment("A",100,10,1,.9,True),))
print("poison:", s.cancel("A",1,"original_agent","K"), "->", s.cancel("A",1,"remediation_operator","K"), "state", s.independent_state("A",11))
# 2 key reuse across different payment
s=SyntheticProcessor((Payment("A",100,10,1,.9,True),Payment("B",100,10,1,.9,True)))
print("cross-uid reuse:", s.cancel("A",1,"remediation_operator","K"), s.cancel("B",1,"remediation_operator","K"), "B state", s.independent_state("B",11))
# 3 caller-controlled clock
s=SyntheticProcessor((Payment("A",100,3,1,.9,True),))
print("caller backdates finish_at=0 after capture:", s.cancel("A",0,"remediation_operator","K2"))
# 4 authority_violations always zero?
import statistics
v=[evaluate(make_scenario(71231+i),m)["authority_violations"] for i in range(1200) for m in ("edf","value_greedy","dp")]
print("authority_violations nonzero:", sum(x>0 for x in v))
# 5 calibration: realized suspect rate vs risk in main
ps=[p for i in range(3000) for p in make_scenario(71231+i)]
print("mean risk", round(statistics.mean(p.risk_estimate for p in ps),3), "realized", round(statistics.mean(p.suspected_problem for p in ps),3))

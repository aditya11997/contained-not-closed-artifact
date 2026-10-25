import unittest
from itertools import permutations
from p4lab.model import Payment, SyntheticProcessor, make_scenario, policy_view
from p4lab.policies import estimated_gain, plan
from p4lab.experiments import evaluate

class LabTests(unittest.TestCase):
    def test_oracle_truth_not_exposed(self):
        data=policy_view(make_scenario(5))
        self.assertFalse(hasattr(data[0],"suspected_problem"))

    def test_cancel_only_authority(self):
        p=(Payment("P00",100,10,1,0.8,True),)
        sink=SyntheticProcessor(p)
        self.assertEqual(sink.cancel("P00",1,"original_agent","key1"),"DENIED")
        self.assertEqual(sink.independent_state("P00",11),"CAPTURED")

    def test_cancel_idempotency(self):
        p=(Payment("P00",100,10,1,0.8,True),)
        sink=SyntheticProcessor(p)
        self.assertEqual(sink.cancel("P00",1,"remediation_operator","same"),"CANCELLED")
        self.assertEqual(sink.cancel("P00",1,"remediation_operator","same"),"CANCELLED")
        self.assertEqual(len(sink.events),1)

    def test_finality_window(self):
        p=(Payment("P00",100,3,1,0.8,True),)
        sink=SyntheticProcessor(p)
        self.assertEqual(sink.cancel("P00",3,"remediation_operator","key2"),"TOO_LATE_CAPTURED")

    def test_refuse_unapproved(self):
        p=(Payment("P00",100,10,1,0.9,True,False),)
        self.assertEqual(plan(policy_view(p),"dp"),())

    def test_dp_matches_exhaustive_optimum_for_small_instances(self):
        # Independent brute-force reference; verifies ordering under declared model.
        for seed in range(15):
            obs=policy_view(make_scenario(seed,count=5))
            elig=tuple(x for x in obs if x.permitted and estimated_gain(x,0.5)>0)
            best=0.0
            for n in range(1,len(elig)+1):
                for seq in permutations(elig,n):
                    t=0; total=0.0; feasible=True
                    for x in seq:
                        t+=x.remedy_minutes
                        if t>=x.capture_at:
                            feasible=False;break
                        total+=estimated_gain(x,0.5)
                    if feasible:best=max(best,total)
            picked=plan(obs,"dp")
            lookup={x.uid:x for x in obs}
            obtained=sum(estimated_gain(lookup[s],0.5) for s in picked)
            self.assertAlmostEqual(obtained,best,places=6)

    def test_repeatable_and_no_authority_violation(self):
        p=make_scenario(77)
        a=evaluate(p,"dp");b=evaluate(p,"dp")
        self.assertEqual(a,b)
        self.assertEqual(a["authority_violations"],0)

if __name__=="__main__":unittest.main()

class AssuranceTests(unittest.TestCase):
    def test_ack_lost_and_query_unavailable_stays_unknown(self):
        from p4lab.assurance import cancel_and_reconcile
        s=SyntheticProcessor((Payment("X",500,10,2,.9,True),))
        result=cancel_and_reconcile(s,"X",2,"x1",lose_ack=True,query_available=False)
        self.assertEqual(result["disposition"],"UNRESOLVED_UNKNOWN")

    def test_replay_does_not_duplicate_cancel(self):
        from p4lab.assurance import cancel_and_reconcile
        s=SyntheticProcessor((Payment("X",500,10,2,.9,True),))
        a=cancel_and_reconcile(s,"X",2,"x1",lose_ack=True,query_available=False)
        b=cancel_and_reconcile(s,"X",2,"x1",lose_ack=False,query_available=True)
        self.assertEqual(b["disposition"],"CLOSED_CANCELLED")
        self.assertEqual(len(s.events),1)

    def test_capture_is_not_misclassified_as_remediated(self):
        from p4lab.assurance import cancel_and_reconcile
        s=SyntheticProcessor((Payment("X",500,3,2,.9,True),))
        b=cancel_and_reconcile(s,"X",3,"x1")
        self.assertEqual(b["disposition"],"CLOSED_EXPOSURE")

import json,unittest
from pathlib import Path
from dataclasses import replace
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.contracts.validation import parse_method_input
from contractedge.contracts.models import RunContext
from contractedge.retrieval.candidates import CandidateSet
from contractedge.orchestration.runner import _bind_diagnosis_envelope


class DiagnosisIndependentErrors(unittest.TestCase):
    def test_source_anchor_error_does_not_hide_already_present_path_error(self):
        root=Path('outputs/deepseek-final-regression-v2-20261020/cases/RW-CAND-130')
        front=json.load(open(root/'rounds/0/front.trace.json'))
        raw=json.loads(json.load(open(root/'model_calls.json'))[0]['response_content'])
        public=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public')
        method,_=method_input_from_task(public,'RW-CAND-130');method=parse_method_input(method)
        observations=json.load(open(root/'broken-public/observation.json'))['observations']
        method=replace(method,public_evidence=method.public_evidence+tuple(observations))
        context=RunContext(**front['run_context']);candidates=CandidateSet(**front['candidate_set'])
        with self.assertRaises(ValueError) as caught:
            _bind_diagnosis_envelope(raw,method,context,front['requirement_spec'],candidates)
        message=str(caught.exception)
        self.assertIn('OBLIGATION_SCOPE_QUOTE_MISMATCH',message)
        self.assertIn('PRODUCTION_PATH_INCOMPLETE',message)

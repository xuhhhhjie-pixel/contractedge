"""Offline capture of a changed front request using saved PUBLIC responses.

No network provider or evaluation artifact is read. Capturing a request proves
delivery only, not a valid diagnosis/plan, an executed patch or repair success.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-case', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--saved-responses', type=int, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.saved_responses < 0:
        parser.error('Use a new output directory and a nonnegative response count')
    case = args.public_case.resolve()
    calls_path = case / 'model_calls.json'
    calls = json.loads(calls_path.read_text())
    if args.saved_responses >= len(calls):
        parser.error('The saved trace must include the captured comparison request')
    stage = json.loads((case / 'journal/stages/diagnosis/round-0/revision-000000.json').read_text())
    inputs = {}
    for ref in stage['input_refs']:
        if ref['kind'] not in {'METHOD_INPUT', 'FRONT_CONFIG', 'PUBLIC_OBSERVATIONS', 'EXECUTION_FEEDBACK'}:
            continue
        path = (case / ref['relative_path']).resolve()
        assert path.is_relative_to(case)
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref['sha256']
        inputs[ref['kind']] = json.loads(raw)
    source = args.workspace / inputs['METHOD_INPUT']['source_root']
    before = compute_snapshot(source).snapshot_id
    assert before == inputs['METHOD_INPUT']['initial_snapshot_id']
    captured = []

    class ReplayOnly:
        count = 0

        def complete_json(self, request):
            index = self.count
            self.count += 1
            if index == args.saved_responses:
                captured.append(asdict(request))
                raise RuntimeError('OFFLINE_CAPTURE_STOP; no request dispatched')
            assert not captured and index < args.saved_responses
            assert request.prompt_version == calls[index]['prompt_version']
            return ModelResponse(calls[index]['response_content'],
                                 {'provider': 'fixture', 'model': request.model,
                                  # Offline recovery must follow the saved
                                  # truncation/usage branch, without real calls.
                                  'finish_reason': calls[index].get('response_metadata', {}).get('finish_reason', 'fixture'),
                                  'usage': calls[index].get('response_metadata', {}).get('usage', {})})

    provider = ReplayOnly()
    trace = run_front_half(inputs['METHOD_INPUT'], args.workspace, provider,
        inputs['FRONT_CONFIG'], utf8_byte_token_bound,
        execution_feedback=inputs['EXECUTION_FEEDBACK'],
        public_observations=tuple(inputs['PUBLIC_OBSERVATIONS']))
    assert compute_snapshot(source).snapshot_id == before
    status = 'REQUEST_CAPTURED_ONLY' if len(captured) == 1 else 'CAPTURE_BOUNDARY_NOT_REACHED'
    args.output.mkdir(parents=True)
    values = [('replay.trace.json', asdict(trace)),
        ('summary.json', dict(status=status, original_calls_sha256=hashlib.sha256(calls_path.read_bytes()).hexdigest(),
          snapshot_id=before, saved_responses=args.saved_responses, model_calls=0, grader_calls=0,
          source_unchanged=True, formal_ready=False,
          scope='Offline trace/request audit only; no new diagnosis, plan or repair effectiveness conclusion'))]
    if captured:
        values.append(('request.json', captured[0]))
    for name, value in values:
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': status, 'output': str(args.output), 'model_calls': 0}))
    if len(captured) != 1:
        raise RuntimeError(f'Capture boundary not reached: {trace.status}, {trace.errors}')


if __name__ == '__main__':
    main()

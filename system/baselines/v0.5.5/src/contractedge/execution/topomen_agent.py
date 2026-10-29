"""Native TopoMen DefaultAgent, with a ContractEdge model port and file-only environment.

The plan format belongs to ContractEdge. The actual TopoMen Agent controls the
query/action/exit lifecycle; its Docker deployment scheduler is a separate API.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Mapping

from contractedge.contracts.validation import canonical_digest
from contractedge.execution.topomen import (
    PlanRejected, apply_edits, build_execution_request, validate_execution_response,
)


_IMPORT_LOCK = threading.RLock()
_LOADED_IDENTITY = None


def source_identity(root: str | Path) -> dict:
    root = Path(root).resolve(strict=True)
    package = root / 'src/topomen'
    if not (package / 'collaboration/base_agent.py').is_file():
        raise RuntimeError('Native TopoMen source is missing its actual Agent implementation')
    files = []
    for path in sorted(package.rglob('*.py')):
        if path.is_symlink() or not path.resolve().is_relative_to(package.resolve()):
            raise RuntimeError('TopoMen source identity cannot include linked external modules')
        files.append({'path': str(path.relative_to(root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'source_root': str(root), 'source_sha256': canonical_digest(files), 'python_modules': len(files)}


def _native_agent(config: Mapping):
    global _LOADED_IDENTITY
    if not config.get('topomen_source_root') or not config.get('topomen_source_sha256'):
        raise RuntimeError('Native TopoMen requires an explicit audited source identity and path')
    identity = source_identity(config['topomen_source_root'])
    if identity['source_sha256'] != config['topomen_source_sha256']:
        raise RuntimeError('Native TopoMen source identity differs from the audited configuration')
    with _IMPORT_LOCK:
        if _LOADED_IDENTITY is not None and _LOADED_IDENTITY != identity:
            raise RuntimeError('A different native TopoMen source identity is already loaded; use a fresh process')
        if _LOADED_IDENTITY is None and 'topomen' in sys.modules:
            raise RuntimeError('TopoMen was loaded outside this audited adapter; use a fresh process')
        source_path = str(Path(identity['source_root']) / 'src')
        inserted = source_path not in sys.path
        previous = {key: os.environ.get(key) for key in ['TOPOMEN_GLOBAL_CONFIG_DIR', 'TOPOMEN_SILENT_STARTUP']}
        previous_cache_prefix = sys.pycache_prefix
        try:
            # __init__ reads a machine-local .env on import. Give it an owned,
            # empty location instead; it must not change the experiment profile.
            with tempfile.TemporaryDirectory(prefix='ce-topomen-config-') as directory:
                os.environ['TOPOMEN_GLOBAL_CONFIG_DIR'] = directory
                os.environ['TOPOMEN_SILENT_STARTUP'] = '1'
                # Timestamp/size-valid .pyc can contain code from an older source
                # tree. Import through an empty owned cache to execute audited text.
                sys.pycache_prefix = str(Path(directory) / 'pycache')
                if inserted:
                    sys.path.insert(0, source_path)
                module = importlib.import_module('topomen.collaboration.base_agent')
                package = importlib.import_module('topomen')
                submitted = importlib.import_module('topomen.core.exceptions').Submitted
                expected = Path(source_path) / 'topomen/collaboration/base_agent.py'
                if Path(module.__file__).resolve() != expected.resolve():
                    raise RuntimeError('Loaded TopoMen Agent is not from the audited source path')
                if source_identity(identity['source_root']) != identity:
                    raise RuntimeError('Native TopoMen source identity changed during import')
                _LOADED_IDENTITY = identity
                provenance = {**identity, 'package_version': package.__version__,
                    'agent_class': module.DefaultAgent.__module__ + '.' + module.DefaultAgent.__name__,
                    'agent_module_path': str(expected), 'global_config_isolated': True,
                    'bytecode_cache_isolated': True, 'source_rechecked_after_import': True,
                    'budget_owner': 'contractedge.shared_budget',
                    'docker_deployment_scheduler_invoked': False}
                return module.DefaultAgent, submitted, provenance
        except ImportError as exc:
            raise RuntimeError('Native TopoMen runtime dependencies are unavailable; install the topomen extra') from exc
        finally:
            sys.pycache_prefix = previous_cache_prefix
            if inserted and source_path in sys.path:
                sys.path.remove(source_path)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class _ModelPort:
    def __init__(self, provider, request):
        self.provider, self.request, self.response = provider, request, None

    def query(self, messages, **kwargs):
        request = replace(self.request, messages=tuple({'role': m['role'], 'content': m['content']} for m in messages))
        self.response = self.provider.complete_json(request)
        body = validate_execution_response(self.response, request)
        return {'role': 'assistant', 'content': self.response.content,
                'extra': {'cost': 0.0, 'actions': [{'kind': 'apply_source_edits', 'body': body}]}}

    def format_message(self, **kwargs):
        return kwargs

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [{'role': 'user', 'content': json.dumps(outputs, ensure_ascii=False)}]

    def get_template_vars(self, **kwargs):
        return {'model_name': self.request.model}

    def serialize(self):
        return {'info': {'config': {'model_adapter': 'contractedge.shared_budget_port.v1',
                                  'model_name': self.request.model}}}


class _FileEnvironment:
    def __init__(self, plan, source, policy, submitted):
        self.plan, self.source, self.policy, self.submitted = plan, source, policy, submitted
        self.result = None

    def execute(self, action, cwd=''):
        if (cwd or not isinstance(action, Mapping) or set(action) != {'kind', 'body'}
                or action['kind'] != 'apply_source_edits' or self.result is not None):
            raise PlanRejected('Native TopoMen environment only accepts one scoped source-edit transaction')
        self.result = apply_edits(self.plan, self.source, self.policy, action['body'])
        raise self.submitted({'role': 'exit', 'content': 'Accepted source edits applied.',
            'extra': {'exit_status': 'Submitted', 'submission': 'Accepted source edits applied.'}})

    def get_template_vars(self, **kwargs):
        return {}

    def serialize(self):
        return {'info': {'config': {'environment_type': 'contractedge.scoped_source_environment.v1',
            'plan_digest': canonical_digest(self.plan), 'shell_available': False}}}


class TopoMenAgentExecutor:
    def __init__(self, provider, token_counter, config: Mapping, *, on_trace=None):
        self.provider, self.token_counter, self.config = provider, token_counter, dict(config)
        self.on_trace, self.last_trace = on_trace, None

    def execute(self, plan: Mapping, source: Path, policy: Mapping, *, requirement: str,
                declared_artifacts=(), observations=(), feedback=None, previous_attempt=None) -> dict:
        self.last_trace = None
        request = build_execution_request(plan, source, policy, self.token_counter, self.config,
            requirement=requirement, declared_artifacts=declared_artifacts, observations=observations,
            feedback=feedback, previous_attempt=previous_attempt)
        agent_class, submitted, provenance = _native_agent(self.config)
        model = _ModelPort(self.provider, request)
        environment = _FileEnvironment(plan, source, policy, submitted)
        agent = agent_class(model=model, env=environment,
            system_template='{{ execution_system }}', instance_template='{{ execution_input }}',
            step_limit=1, cost_limit=0)
        try:
            info = agent.run(requirement, execution_system=request.messages[0]['content'],
                             execution_input=request.messages[1]['content'])
        finally:
            self.last_trace = {**agent.serialize(), 'backend': provenance}
            if self.on_trace:
                self.on_trace(self.last_trace)
        if info.get('exit_status') != 'Submitted' or environment.result is None:
            raise PlanRejected('Native TopoMen did not complete the accepted edit transaction')
        result = {**environment.result, 'consumer_id': 'contractedge.native_topomen_agent_adapter.v1',
                  'source_transaction_consumer_id': environment.result['consumer_id'],
                  'backend': provenance, 'topomen_trace': self.last_trace,
                  'model_response': {'content': model.response.content, 'metadata': dict(model.response.metadata)}}
        return result

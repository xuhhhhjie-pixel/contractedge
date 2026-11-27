"""Owned method-container lifecycle and explicit public-tool dispatch."""
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from minisweagent.agents.default import TerminatingException
from minisweagent.environments.docker import DockerEnvironment

from contractedge.graph_repair.state import append_jsonl, write_json


class ToolStop(TerminatingException):
    """The admitted tool timed out or could not complete safely."""


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError('Run deadline exhausted')
    return seconds


class CommonEnvironment(DockerEnvironment):
    """Use the upstream Live shell executor, with controller-owned lifecycle."""

    def __init__(self, *, image, source, inputs, runner, workspace, out, shell_env, profile,
                 action_deadline, final_deadline):
        self.name = 'ce-common-' + uuid.uuid4().hex
        self.source, self.inputs = source, inputs
        self.runner, self.workspace, self.out = runner, workspace, out
        self.action_deadline, self.final_deadline = action_deadline, final_deadline
        self.public_seconds = profile['tools']['public_seconds']
        self.accepting = False
        self.public_closure_confirmed = True
        self.tool_count = 0
        self.mounts = {
            '/testbed': (source, True),
            '/testbed/.git': (source / '.git', False),
            '/inputs': (inputs, False),
        }
        resources = profile['resources']
        args = ['--pull=never', '--network=none', '--read-only', '--cap-drop=ALL', '--no-healthcheck',
                '--security-opt=no-new-privileges:true', '--ipc=private',
                '--tmpfs=/tmp:rw,nosuid,nodev,size=256m,mode=1777',
                f"--memory={resources['memory_bytes']}", f"--cpus={resources['cpus']}",
                f"--pids-limit={resources['pids']}"]
        for target, (path, writable) in self.mounts.items():
            if ',' in str(path):
                raise ValueError('Method mount paths may not contain commas')
            suffix = '' if writable else ',readonly'
            args.extend(['--mount', f'type=bind,src={path},dst={target}{suffix}'])
        super().__init__(
            image=image, cwd='/testbed', run_args=args, executable='docker', forward_env=[],
            timeout=profile['tools']['shell_seconds'],
            env={**shell_env, 'HOME': '/tmp', 'GIT_CONFIG_COUNT': '1',
                 'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': '/testbed'},
        )

    def _start_container(self):
        # Starting is explicit so the controller owns cleanup even on partial startup.
        pass

    def __del__(self):
        # The controller closes this container synchronously and records the result.
        pass

    def docker(self, *args, deadline, check=True):
        command = [self.config.executable, *args]
        result = subprocess.run(command, timeout=min(20, remaining(deadline)), check=False,
                                capture_output=True, text=True)
        append_jsonl(self.out / 'lifecycle.jsonl', {
            'at_monotonic': time.monotonic(), 'command': command, 'exit_code': result.returncode,
            'stderr': result.stderr,
        })
        if check:
            result.check_returncode()
        return result.stdout

    def start(self):
        end = self.action_deadline
        self.container_id = self.docker(
            'create', '--name', self.name, '--label', 'contractedge.common=' + self.name,
            '-w', '/testbed', '--entrypoint=/bin/sleep', *self.config.run_args,
            self.config.image, 'infinity', deadline=end,
        ).strip()
        if not re.fullmatch('[0-9a-f]{64}', self.container_id):
            self.container_id = None
            raise ValueError('The daemon did not return a container identity')
        inspected = json.loads(self.docker('inspect', self.container_id, deadline=end))[0]
        host = inspected['HostConfig']
        mounts = inspected['Mounts']
        actual = {m['Destination']: (Path(m['Source']), m['RW']) for m in mounts if m['Type'] == 'bind'}
        if (len(mounts) != len(self.mounts) or actual != self.mounts or inspected['Image'] != self.config.image
                or host['NetworkMode'] != 'none' or host['Privileged'] or not host['ReadonlyRootfs']):
            raise ValueError('The method container does not match its frozen sandbox')
        write_json(self.out / 'container.json', {'id': self.container_id, 'image': inspected['Image'],
                                                'mounts': mounts, 'network': host['NetworkMode'],
                                                'readonly_root': host['ReadonlyRootfs']})
        self.docker('start', self.container_id, deadline=end)
        platform_info = json.loads(self.docker(
            'exec', self.container_id, 'python3', '-c',
            'import json, platform; print(json.dumps(platform.uname()._asdict()))', deadline=end,
        ))
        write_json(self.out / 'container_platform.json', platform_info)
        self.accepting = True
        return platform_info

    def stop(self):
        self.accepting = False
        deadline = self.final_deadline
        # Name and label are chosen in this run. The lookup also covers an
        # acknowledged creation whose client did not receive the result.
        selector = ['--filter', 'label=contractedge.common=' + self.name]
        before = self.docker('ps', '-aq', '--no-trunc', *selector, deadline=deadline).split()
        if len(before) > 1 or any(not re.fullmatch('[0-9a-f]{64}', cid) for cid in before):
            raise ValueError('Ambiguous method-container ownership')
        if before:
            if self.container_id is not None and before != [self.container_id]:
                raise ValueError('The owned container identity changed')
            self.docker('rm', '-f', before[0], deadline=deadline)
        after = self.docker('ps', '-aq', '--no-trunc', *selector, deadline=deadline).split()
        if after or not self.public_closure_confirmed:
            raise ValueError('Writer closure not confirmed')
        self.container_id = None
        record = {'status': 'CONFIRMED', 'dispatch_closed': True, 'removed': before, 'remaining': after,
                  'confirmed_at_monotonic': time.monotonic()}
        write_json(self.out / 'writer_closure.json', record)
        return record

    def public_check(self, output, deadline):
        # The published runner builds on a disposable copy. Pausing makes the
        # input to that copy consistent even if an earlier shell left a writer.
        self.docker('pause', self.container_id, deadline=deadline)
        self.public_closure_confirmed = False
        self.runner.timeout_seconds = remaining(deadline)
        result = self.runner.observe(self.workspace, output)
        self.public_closure_confirmed = result['runtime_cleanup']['status'] == 'OWNED_CONTAINERS_CLOSED'
        if not self.public_closure_confirmed:
            raise ToolStop('Public runtime writer closure is unconfirmed')
        if result['launcher']['timed_out'] or time.monotonic() >= deadline:
            raise ToolStop('Public check reached its deadline')
        streams = sorted((output / 'raw').rglob('*.stdout')) + sorted((output / 'raw').rglob('*.stderr'))
        text = ''.join(path.read_text(errors='replace') for path in streams)
        if not text:
            text = result['status'] + '\n'
        self.docker('unpause', self.container_id, deadline=deadline)
        return {'output': text, 'returncode': result['launcher']['returncode']}

    def execute(self, command, cwd='', *, timeout=None):
        if not self.accepting:
            raise ToolStop('Tool dispatch is closed')
        self.tool_count += 1
        started = time.monotonic()
        record = {'sequence': self.tool_count, 'command': command, 'start_monotonic': started}
        try:
            public = command.strip() == 'ce-public'
            limit = self.public_seconds if public else self.config.timeout
            if timeout is not None:
                limit = min(limit, timeout)
            deadline = min(self.action_deadline, started + limit)
            record['deadline_monotonic'] = deadline
            if public:
                output = self.out / 'public-checks' / str(self.tool_count)
                record['public_observation'] = str(output / 'observation.json')
                result = self.public_check(output, deadline)
            else:
                result = super().execute(command, cwd, timeout=remaining(deadline))
            record.update(result)
            return result
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or b''
            record['output'] = partial.decode(errors='replace') if isinstance(partial, bytes) else partial
            record['status'] = 'TOOL_TIMEOUT'
            self.accepting = False
            raise ToolStop('Tool timed out and further dispatch was closed') from exc
        except (OSError, ValueError, subprocess.CalledProcessError, ToolStop) as exc:
            self.accepting = False
            record['error'] = str(exc)
            raise ToolStop('Tool infrastructure or deadline failure') from exc
        finally:
            append_jsonl(self.out / 'tools.jsonl', {**record, 'end_monotonic': time.monotonic()})

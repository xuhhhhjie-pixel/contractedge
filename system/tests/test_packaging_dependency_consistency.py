"""The documented development lock must satisfy the advertised package dependencies."""
from pathlib import Path
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def test_development_lock_satisfies_core_and_native_consumer_requirements():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    pins = {}
    for line in (root / 'requirements-dev.lock').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        requirement = Requirement(line)
        versions = [s.version for s in requirement.specifier if s.operator == '==']
        assert len(versions) == 1, f'Development dependency must have one exact pin: {line}'
        pins[canonicalize_name(requirement.name)] = versions[0]
    for text in project['dependencies'] + project['optional-dependencies']['topomen']:
        requirement = Requirement(text)
        selected = pins[canonicalize_name(requirement.name)]
        assert requirement.specifier.contains(selected), f'{text} rejects development pin {selected}'

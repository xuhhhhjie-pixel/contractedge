"""Shared public edit-scope policy; read evidence does not grant edit rights."""
import fnmatch


def target_permission_error(relative, policy):
    if policy is None:
        return None
    allowed = policy.get('allowed_paths', [])
    if allowed and not any(fnmatch.fnmatchcase(relative, pattern) for pattern in allowed):
        return 'TARGET_OUTSIDE_EDITABLE_SCOPE'
    if any(fnmatch.fnmatchcase(relative, pattern) for pattern in policy.get('forbidden_paths', [])):
        return 'TARGET_FORBIDDEN'
    return None

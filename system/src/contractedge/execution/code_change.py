"""Conservative recognition of cosmetic retries, not general semantic proof."""
import ast
import hashlib
import re


def _fingerprint(path,text):
    if path.endswith('.py'):
        if re.search(r'(?m)^\s*#.*coding[:=]',text):return None
        try:return ('PYTHON_AST',ast.dump(ast.parse(text,type_comments=True),include_attributes=False))
        except (SyntaxError,ValueError):return None
    if path.endswith('.go'):
        # Go directives/cgo can give comments and separation runtime meaning.
        if (re.search(r'(?m)^\s*//(?:go:|line\b|export\b|\s*\+build\b)',text)
                or re.search(r'/\*\s*line\s',text) or '"C"' in text):return None
        # Ordinary comments are whitespace, but a block comment's newline can
        # insert a Go semicolon. Preserve that boundary and every literal token;
        # directives/cgo remain deliberately unclassified above.
        def token(match):
            value=match[0]
            if value.startswith('//'):return ''
            if value.startswith('/*'):return '\n' if '\n' in value else ' '
            return '\x00'+hashlib.sha256(value.encode()).hexdigest()+'\x00'
        masked=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|`[^`]*`|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
            token,text)
        return ('GO_COMMENTS_AND_BLANK_LINES',re.sub(r'(?m)^[ \t\r]*\n','',masked))
    return None


def semantics_unchanged(original,changed):
    """All files must have a supported matching fingerprint; unknown allows edit."""
    for path,after in changed.items():
        before=original[path]
        if before is None:return False
        old,new=_fingerprint(path,before.decode()),_fingerprint(path,after)
        if old is None or new is None or old!=new:return False
    return bool(changed)


def behavior_change_required(feedback,snapshot_id):
    if not feedback or feedback.get('to_snapshot')!=snapshot_id or feedback.get('semantic_outcome')!='PUBLIC_REVIEW_FAIL':return False
    public={o['id'] for o in feedback.get('public_observations',[])
            if o.get('snapshot_id')==snapshot_id and o.get('evidence_kind')=='PUBLIC_BEHAVIOR'}
    return any(c.get('status')=='FAIL' and any(e.get('observation_id') in public for e in c.get('evidence',[]))
               for c in feedback.get('public_check_results',[]))

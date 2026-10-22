import tempfile,unittest
from pathlib import Path
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units


class HelmDocumentContext(unittest.TestCase):
    def test_separate_template_resources_are_visible_beyond_file_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            text='{{- if .Values.enabled }}\nkind: Job\nmetadata:\n  name: create\n'+('  # padding\n'*30)+'---\nkind: Job\nmetadata:\n  name: patch\nspec:\n  template:\n    spec:\n      restartPolicy: Never\n{{- end }}\n'
            (p/'jobs.yaml').write_text(text)
            units=extract_units(compute_snapshot(p),{'seed_paths':['jobs.yaml']},query='Create and patch Jobs must propagate global scheduling',max_lines=16,max_units=12)
            matches=[u for u in units if 'name: patch' in u['text']]
            self.assertTrue(matches,'The second Helm resource cannot be diagnosed from a truncated first-resource prefix')
            self.assertTrue(any(u['locator']['start_line']>16 for u in matches))

    def test_relevant_resource_documents_survive_competing_declaration_fragments(self):
        from contractedge.retrieval.candidates import build_candidate_set
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            text='{{- if .Values.enabled }}\nkind: Role\nmetadata:\n  name: permission\n'+('  # padding\n'*14)+'---\nkind: Job\nmetadata:\n  name: create\nspec:\n  template:\n    spec:\n      restartPolicy: Never\n---\nkind: Job\nmetadata:\n  name: patch\nspec:\n  template:\n    spec:\n      restartPolicy: Never\n{{- end }}\n'
            (p/'jobs.yaml').write_text(text);snap=compute_snapshot(p)
            requirement='When create and patch Jobs are enabled, global nodeSelector, tolerations and affinity must appear in both Job pod specs.'
            units=extract_units(snap,{'seed_paths':['jobs.yaml']},query=requirement,max_lines=16,max_units=20)
            for u in units:u['anchor_priority']=2
            for i in range(8):
                units.append({'id':f'INPUT{i}','path':f'input-{i}.yaml','kind':'DECLARED_INPUT','text':'nodeSelector tolerations affinity',
                    'anchor_priority':1,'origin_kind':'DECLARED_ARTIFACT','snapshot_id':snap.snapshot_id,'locator':{'start_line':1,'end_line':1},'content_digest':str(i)})
            selected=build_candidate_set(requirement,None,units,[],top_n=4,max_context_tokens=12000,token_counter=len)
            sources=[u['text'] for u in selected.candidates if u['path']=='jobs.yaml']
            self.assertTrue(any('name: patch' in t for t in sources),'The target sibling must survive the same bounded context budget')
            self.assertTrue(any('name: create' in t for t in sources))

    def test_explicit_resource_file_cannot_lose_second_document_in_pool_interleave(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);paths=[]
            for i in range(12):
                name=f'doc{i}.txt';(p/name).write_text('nodeSelector tolerations affinity\n');paths.append(name)
            (p/'jobs.yaml').write_text('{{- if .Values.enabled }}\nkind: Job\nmetadata:\n  name: create\n---\nkind: Job\nmetadata:\n  name: patch\n{{- end }}\n')
            units=extract_units(compute_snapshot(p),{'seed_paths':paths+['jobs.yaml']},query='Both Jobs need nodeSelector tolerations affinity',
                max_units=8,focused_paths=['jobs.yaml'])
            self.assertTrue(any('name: patch' in u['text'] for u in units),'Explicit target resources must enter the bounded source pool before unrelated documents')

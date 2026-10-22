import tempfile
import unittest
from pathlib import Path
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_gap_extension_units
from contractedge.retrieval.candidates import build_candidate_set, extend_once


class DirectoryGapCoverageTests(unittest.TestCase):
    def test_actual_directory_plus_same_gap_query_reaches_production_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'pkg/resource/metric_alarm').mkdir(parents=True)
            (root/'generator.yaml').write_text('Tags: generated\n')
            (root/'pkg/resource/metric_alarm/tags.go').write_text('package metric_alarm\nfunc updateTags() { Tags() }\n')
            snap=compute_snapshot(root)
            units,audit=extract_gap_extension_units(snap,['generator.yaml'],[],[
                {'id':'G1','locator_hint':{'path':'pkg/resource/metric_alarm','search_term':'Tags'}}])
            self.assertTrue(any(u['path']=='pkg/resource/metric_alarm/tags.go' for u in units))
            u=next(u for u in units if u['path'].endswith('tags.go'))
            self.assertEqual(u['scope_extension'],'EVIDENCE_GAP_DIRECTORY_PATH')
            self.assertEqual(u['evidence_gap_refs'],['G1'])
            self.assertIn('not a verified',u['association_status'])
            self.assertLessEqual(audit['inspected_files'],2000)

    def test_directory_path_alone_or_cross_gap_query_cannot_expand_read_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'impl').mkdir();(root/'impl/worker.go').write_text('package impl\nfunc Tags() {}\n')
            snap=compute_snapshot(root)
            for gaps in [[{'id':'G1','locator_hint':{'path':'impl'}}],
                         [{'id':'G1','locator_hint':{'path':'impl'}},{'id':'G2','locator_hint':{'symbol':'Tags'}}],
                         [{'id':'G1','locator_hint':{'path':'../impl','symbol':'Tags'}}]]:
                with self.subTest(gaps=gaps):
                    units,_=extract_gap_extension_units(snap,[],[],gaps)
                    self.assertEqual(units,[])

    def test_directory_boundary_and_existing_file_limits_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for directory in ['impl','impl-other']:
                (root/directory).mkdir()
                for i in range(4): (root/directory/f'{i}.go').write_text('package impl\nfunc Tags() {}\n')
            units,audit=extract_gap_extension_units(compute_snapshot(root),[],[],[
                {'id':'G','locator_hint':{'path':'impl','symbol':'Tags'}}],max_files=2,max_units=1)
            self.assertTrue(units)
            self.assertTrue(all(u['path'].startswith('impl/') for u in units))
            self.assertLessEqual(len(units),1);self.assertLessEqual(audit['inspected_files'],2)

    def test_new_gap_coverage_beats_redundant_windows_without_a_file_quota(self):
        def unit(i,gap,path):
            return {'id':str(i),'path':path,'text':'reload Tags '*100 if gap=='G1' else 'reload',
                'snapshot_id':'S','locator':{'start_line':i*100+1,'end_line':i*100+40},
                'evidence_gap_refs':[gap],'scope_extension':'EVIDENCE_GAP_EXACT_PATH','anchor_priority':4}
        initial=build_candidate_set('reload',None,[unit(0,'G1','api.go')],[],top_n=2,max_context_tokens=10000,token_counter=len)
        result=extend_once(initial,[{'id':'G1','locator_hint':{'symbol':'Tags'}},
                                   {'id':'G2','locator_hint':{'symbol':'reload'}}],
                           [unit(1,'G1','api.go'),unit(2,'G2','dispatch.go')],max_context_tokens=10000,token_counter=len)
        self.assertIn('dispatch.go',[u['path'] for u in result.candidates])
        self.assertTrue(any(result.ranking_features[i].get('new_gap_ids') for i in result.selected_ids))

    def test_first_file_cannot_exhaust_gap_pool_before_later_requested_file(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'api.go').write_text('package api\n'+'\n'.join('func Query%d() { reload() }'%i for i in range(40)))
            (root/'dispatch.go').write_text('package api\nfunc Run() { reload() }\n')
            units,_=extract_gap_extension_units(compute_snapshot(root),['api.go','dispatch.go'],[],[
                {'id':'G1','locator_hint':{'path':'api.go','search_term':'reload'}},
                {'id':'G2','locator_hint':{'path':'dispatch.go','search_term':'reload'}}],max_units=4)
            self.assertIn('dispatch.go',[u['path'] for u in units])

    def test_same_word_in_other_file_does_not_claim_precise_gap_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'api.go').write_text('package api\nfunc Get() { reload() }\n')
            (root/'dispatch.go').write_text('package api\nfunc Run() { reload() }\n')
            units,_=extract_gap_extension_units(compute_snapshot(root),['api.go','dispatch.go'],[],[
                {'id':'G1','locator_hint':{'path':'api.go','search_term':'reload'}},
                {'id':'G2','locator_hint':{'path':'dispatch.go','search_term':'reload'}}])
            for u in units:
                self.assertEqual(u['evidence_gap_refs'],['G1'] if u['path']=='api.go' else ['G2'])

    def test_same_file_different_symbols_do_not_merge_independent_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'a.go').write_text('package a\nfunc Alpha() {}\nfunc Beta() {}\n')
            units,_=extract_gap_extension_units(compute_snapshot(root),['a.go'],[],[
                {'id':'G1','locator_hint':{'path':'a.go','symbol':'Alpha'}},
                {'id':'G2','locator_hint':{'symbol':'Beta'}}])
            beta=next(u for u in units if u.get('symbol')=='Beta')
            self.assertEqual(beta['evidence_gap_refs'],['G2'])

    def test_located_long_declaration_is_not_only_its_eighty_line_prefix(self):
        import hashlib
        from contractedge.source.units import extract_units
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            text='package p\nfunc newResourceDelta() {\n'+'  keep()\n'*170+'  compare(Spec.Tags)\n}\n'
            (root/'delta.go').write_text(text)
            snap=compute_snapshot(root)
            original=extract_units(snap,{'seed_paths':['delta.go']},max_lines=80,max_extension_files=0)
            units,_=extract_gap_extension_units(snap,['delta.go'],original,[
                {'id':'G','locator_hint':{'path':'delta.go','symbol':'newResourceDelta','search_term':'Spec.Tags'}}],max_lines=80)
            u=next(u for u in units if u.get('symbol')=='newResourceDelta')
            self.assertIn('compare(Spec.Tags)',u['text'])
            self.assertFalse(u['truncated'])
            self.assertLessEqual(u['locator']['end_line']-u['locator']['start_line']+1,512)
            self.assertNotIn(u['id'],{v['id'] for v in original})
            self.assertEqual(hashlib.sha256(u['text'].encode()).hexdigest(),u['content_digest'])
            self.assertEqual(u['expanded_from_unit_id'],next(v['id'] for v in original if v.get('symbol')=='newResourceDelta'))

    def test_complete_located_function_beats_its_header_only_fallback(self):
        base={'path':'delta.go','snapshot_id':'S','evidence_gap_refs':['G1','G2'],
              'scope_extension':'EVIDENCE_GAP_EXACT_PATH','anchor_priority':4}
        header={**base,'id':'header','text':'import tags\n// delta resources',
            'kind':'SOURCE_TEXT','locator':{'start_line':1,'end_line':30}}
        body={**base,'id':'body','evidence_gap_refs':['G2'],'text':'func newResourceDelta() { delta() }',
            'kind':'FUNCTION_OR_TYPE','symbol':'newResourceDelta','declared_symbols':['newResourceDelta'],
            'references':['delta'],'expanded_from_unit_id':'prefix','truncated':False,
            'locator':{'start_line':31,'end_line':250}}
        initial=build_candidate_set('Tags delta',None,[header],[],top_n=1,max_context_tokens=5000,token_counter=len)
        result=extend_once(initial,[{'id':'G1','locator_hint':{'path':'delta.go','search_term':'Tags'}},
                                  {'id':'G2','locator_hint':{'path':'delta.go','search_term':'delta'}}],
                           [body],max_context_tokens=5000,token_counter=len)
        self.assertEqual(result.selected_ids,('body',))

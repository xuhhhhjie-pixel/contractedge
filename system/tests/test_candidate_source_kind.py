"""Candidate registration requires source material, regardless of record ID."""
import pytest

from contractedge.diagnosis.evidence import source_context_offer
from tests.test_bounded_retrieval_continuation import owner, search_record, source


def test_static_search_cannot_register_a_source_candidate_id():
    fact = search_record()
    # The structured digest deliberately remains valid: record IDs are not
    # query semantics, but candidate linkage still requires a source kind.
    fact['id'] = 'SRC-fake'
    with pytest.raises(ValueError, match='canonical source record'):
        source_context_offer(owner(), [fact], producer='extension', candidate_ids=['fake'])


@pytest.mark.parametrize('kind', ['SOURCE_SPAN', 'DECLARED_ARTIFACT'])
def test_real_source_candidate_kinds_remain_accepted(kind):
    item = source(1)
    item['kind'] = kind
    result = source_context_offer(owner(), [item], producer='extension', candidate_ids=['1'])
    assert result['candidate_ids'] == ['1']

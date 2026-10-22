import unittest

from contractedge.changeset.topomen import translate_for_topomen
from tests.test_contracts import valid_plan


class TopoMenPortTests(unittest.TestCase):
    def test_translation_is_versioned_and_explicitly_unverified(self):
        plan = translate_for_topomen(valid_plan(), "contractedge.topomen_plan.v1")
        self.assertEqual(plan["schema_version"], "contractedge.topomen_plan.v1")
        self.assertEqual(plan["consumer_acceptance"], "UNVERIFIED")
        self.assertEqual(plan["operations"][0]["operation"], "MODIFY")

    def test_unknown_consumer_contract_is_rejected(self):
        with self.assertRaises(ValueError):
            translate_for_topomen(valid_plan(), "topomen.unknown")


if __name__ == "__main__":
    unittest.main()

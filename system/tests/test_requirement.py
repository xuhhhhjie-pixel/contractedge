import unittest

from contractedge.requirement.normalize import normalize_requirement


class RequirementNormalizationTests(unittest.TestCase):
    def test_preserves_exact_text_and_uses_one_explicit_outcome(self):
        text = "When enabled, do not omit serviceAccountName.\nKeep the selected name."
        result = normalize_requirement({"requirement_text": text})
        self.assertEqual(result["raw_text"], text)
        self.assertEqual(result["outcomes"][0]["statement"], text)
        self.assertEqual(result["outcomes"][0]["support"], "EXPLICIT")

    def test_keeps_condition_and_negation_inside_the_original_outcome(self):
        text = "Unless disabled, never remove the configured mount."
        result = normalize_requirement({"requirement_text": text})
        self.assertIn("Unless disabled", result["outcomes"][0]["statement"])
        self.assertIn("never remove", result["outcomes"][0]["statement"])
        self.assertEqual(result["outcomes"][0]["condition"], "Unless disabled")

    def test_empty_requirement_is_under_specified(self):
        result = normalize_requirement({"requirement_text": "   "})
        self.assertEqual(result["status"], "UNDER_SPECIFIED")
        self.assertEqual(result["outcomes"], [])


if __name__ == "__main__":
    unittest.main()

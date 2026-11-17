import unittest
from content_agent import classify, economic_impact_score, slugify, template_generate


class ContentAgentTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("AI & the Future!"), "ai-the-future")

    def test_classifier(self):
        self.assertEqual(classify("Stock market update"), "Money")

    def test_template_is_a_full_news_story_without_quiz(self):
        content = template_generate("Test topic", "Learning")
        self.assertGreaterEqual(len(content["story"]), 8)
        self.assertIn("economy_impact", content)
        self.assertIn("bottom_line", content)
        self.assertNotIn("quiz", content)

    def test_economic_impact_ranking(self):
        self.assertGreater(
            economic_impact_score("Federal Reserve interest rates", "Money"),
            economic_impact_score("Celebrity concert", "Entertainment"),
        )


if __name__ == "__main__":
    unittest.main()

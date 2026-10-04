#!/usr/bin/env python3
"""Regression tests for repository secrets and runtime artifacts."""

from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[2]


class GitignoreTests(unittest.TestCase):
    def test_sensitive_agent_and_runtime_paths_are_ignored(self):
        rules = (REPO / ".gitignore").read_text(encoding="utf-8")
        for rule in ("/agent/agent.json", "/agent/data/", "/agent/logs/", "*.claim-token"):
            with self.subTest(rule=rule):
                self.assertIn(rule, rules)

    def test_environment_files_are_ignored(self):
        rules = (REPO / ".gitignore").read_text(encoding="utf-8")
        self.assertTrue(".env" in rules or "*.env" in rules)


if __name__ == "__main__":
    unittest.main()

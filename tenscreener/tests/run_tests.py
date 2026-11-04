#!/usr/bin/env python3
"""tenscreener 测试入口:python3 tests/run_tests.py [模式]"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    sys.exit(0 if runner.run(suite).wasSuccessful() else 1)

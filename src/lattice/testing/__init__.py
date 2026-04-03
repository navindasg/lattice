"""lattice.testing — test file discovery, classification, and coverage subsystem.

Public API:
    TestDiscovery   — discovers pytest and jest test files in a project
    TestClassifier  — classifies test files by type (unit, integration, e2e)
    CoverageBuilder — computes transitive edge coverage and gap reports
"""
from lattice.testing.classifier import TestClassifier
from lattice.testing.coverage import CoverageBuilder
from lattice.testing.discovery import TestDiscovery

__all__ = ["TestDiscovery", "TestClassifier", "CoverageBuilder"]

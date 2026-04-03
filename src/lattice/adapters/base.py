"""LanguageAdapter abstract base class (SA-01).

Defines the contract every language AST parser must implement.
All concrete adapters return FileAnalysis instances so downstream
consumers have a stable, typed interface regardless of language.

Usage:
    class PythonAdapter(LanguageAdapter):
        @property
        def supported_extensions(self) -> frozenset[str]:
            return frozenset({".py"})

        def analyze(self, path: Path) -> FileAnalysis:
            ...
"""
from abc import ABC, abstractmethod
from pathlib import Path

from lattice.models.analysis import FileAnalysis


class LanguageAdapter(ABC):
    """Abstract contract for all AST parsers.

    Subclasses must implement both abstract members:
    - supported_extensions: property returning frozenset[str]
    - analyze(path): method returning FileAnalysis
    """

    @property
    @abstractmethod
    def supported_extensions(self) -> frozenset[str]:
        """File extensions this adapter handles, e.g. frozenset({'.py'})."""
        ...

    @abstractmethod
    def analyze(self, path: Path) -> FileAnalysis:
        """Parse a single file and return structured analysis.

        Args:
            path: Absolute or relative path to the source file.

        Returns:
            FileAnalysis with imports, exports, signatures, and classes extracted.
        """
        ...

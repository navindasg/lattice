"""LLM abstraction layer — config and model factory."""
from lattice.llm.config import LatticeSettings, ModelTierConfig
from lattice.llm.factory import get_model

__all__ = ["LatticeSettings", "ModelTierConfig", "get_model"]

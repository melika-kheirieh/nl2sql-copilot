"""
Registry mapping simple string keys to concrete component classes.
Used by pipeline_factory to perform lightweight dependency injection.
"""

from typing import Dict, Type
from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from nl2sql.safety import Safety
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair

# later you can add llm-aware generator variants, etc.
PLANNERS: Dict[str, Type[Planner]] = {"default": Planner}
DETECTORS: Dict[str, Type[AmbiguityDetector]] = {"default": AmbiguityDetector}
GENERATORS: Dict[str, Type[Generator]] = {"rules": Generator}
SAFETIES: Dict[str, Type[Safety]] = {"default": Safety}
EXECUTORS: Dict[str, Type[Executor]] = {"default": Executor}
VERIFIERS: Dict[str, Type[Verifier]] = {"basic": Verifier}
REPAIRS: Dict[str, Type[Repair]] = {"default": Repair}

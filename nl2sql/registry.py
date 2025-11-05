"""
Registry mapping simple string keys to concrete component classes.
Used by pipeline_factory to perform lightweight dependency injection.
"""

from nl2sql.ambiguity_detector import AmbiguityDetector
from nl2sql.planner import Planner
from nl2sql.generator import Generator
from nl2sql.safety import Safety
from nl2sql.executor import Executor
from nl2sql.verifier import Verifier
from nl2sql.repair import Repair

# later you can add llm-aware generator variants, etc.
PLANNERS = {"default": Planner}
GENERATORS = {"rules": Generator}
EXECUTORS = {"default": Executor}
REPAIRS = {"default": Repair}
DETECTORS = {"default": AmbiguityDetector}
SAFETIES = {"default": Safety}
VERIFIERS = {"basic": Verifier}

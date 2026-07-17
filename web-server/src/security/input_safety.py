"""Deterministic policy gate for user input."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from src.config.models import InputSafetyRule


class SafetyDecision(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class SafetyAssessment:
    decision: SafetyDecision
    rule_id: str | None = None
    reason: str | None = None


class InputSafetyGate:
    """Assess a message against configured prompt-injection rules."""

    _EVALUATION_ERROR_RULE_ID = "input_safety_evaluation_error"
    _EVALUATION_ERROR_REASON = "Input safety policy evaluation failed"

    def __init__(self, rules: Sequence[InputSafetyRule]) -> None:
        try:
            self._rules = tuple(
                (rule, tuple(re.compile(pattern) for pattern in rule.patterns))
                for rule in rules
            )
            self._configuration_is_valid = True
        except (re.error, OverflowError):
            self._rules = ()
            self._configuration_is_valid = False

    def assess(self, message: str) -> SafetyAssessment:
        if not self._configuration_is_valid:
            return self._evaluation_failure()

        try:
            normalized = unicodedata.normalize("NFKC", message).casefold()
            for rule, patterns in self._rules:
                if any(pattern.search(normalized) for pattern in patterns):
                    return SafetyAssessment(
                        decision=SafetyDecision.BLOCK,
                        rule_id=rule.id,
                        reason=rule.reason,
                    )
            return SafetyAssessment(decision=SafetyDecision.ALLOW)
        except Exception:
            return self._evaluation_failure()

    def _evaluation_failure(self) -> SafetyAssessment:
        return SafetyAssessment(
            decision=SafetyDecision.BLOCK,
            rule_id=self._EVALUATION_ERROR_RULE_ID,
            reason=self._EVALUATION_ERROR_REASON,
        )

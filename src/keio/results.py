from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Issue:
    level: str
    message: str


@dataclass(slots=True)
class OperationSummary:
    issues: list[Issue] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    fatal: bool = False

    def add_issue(self, level: str, message: str) -> None:
        self.issues.append(Issue(level=level, message=message))

    def increment(self, key: str, amount: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + amount

    @property
    def exit_code(self) -> int:
        if self.fatal:
            return 1
        if any(issue.level in {"warning", "skip"} for issue in self.issues):
            return 2
        return 0

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    def lines(self) -> list[str]:
        lines = []
        for key in sorted(self.counters):
            lines.append(f"{key}: {self.counters[key]}")
        for issue in self.issues:
            lines.append(f"{issue.level}: {issue.message}")
        return lines

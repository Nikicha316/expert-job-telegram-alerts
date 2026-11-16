"""Shared job record: only information that the source actually provided."""
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Job:
    source: str
    job_id: str
    title: str
    url: str
    location: str | None = None
    pay: str | None = None
    eligible_locations: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.job_id}"

    def updated(self, **changes):
        return replace(self, **changes)

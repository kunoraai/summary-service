from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class ServiceMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.jobs_created = Counter(
            "summary_jobs_created_total",
            "Summary jobs accepted",
            registry=self.registry,
        )
        self.jobs_completed = Counter(
            "summary_jobs_completed_total",
            "Summary jobs completed",
            ["status"],
            registry=self.registry,
        )
        self.queue_rejected = Counter(
            "summary_queue_rejected_total",
            "Jobs rejected because the queue is full",
            registry=self.registry,
        )
        self.leases_recovered = Counter(
            "summary_leases_recovered_total",
            "Expired worker leases recovered",
            registry=self.registry,
        )
        self.jobs_cleaned = Counter(
            "summary_jobs_cleaned_total",
            "Jobs expired or deleted",
            ["action"],
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "summary_queue_depth",
            "Queued and running jobs",
            registry=self.registry,
        )
        self.jobs_by_status = Gauge(
            "summary_jobs",
            "Jobs by status",
            ["status"],
            registry=self.registry,
        )
        self.llm_latency = Histogram(
            "summary_llm_duration_seconds",
            "LLM call duration",
            registry=self.registry,
        )
        self.llm_attempts = Histogram(
            "summary_llm_attempts",
            "LLM attempts per job",
            buckets=(1, 2, 3),
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)

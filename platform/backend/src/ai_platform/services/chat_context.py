from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from ai_platform.repositories import conversations as repo
from ai_platform.settings import settings

SUMMARY_PROMPT = "总结历史对话中已确认的事实、用户约束、决定及待处理事项。只总结提供的历史资料。"


def cost(message):
    return 8 + len(message["content"].encode("utf-8"))


def message(role, text):
    return {"role": role, "content": text}


class ChatError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class HistoryCache:
    def __init__(self):
        self.entries = {}
        self.lock = Lock()

    def read(self, conn, job, after):
        key = job["workspace_id"], job["conversation_id"]
        stamp = after, job["input_sequence"]
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0] == stamp and job["created_at"] - entry[2] < timedelta(minutes=30):
                return entry[1]
        rows = repo.completed_history(conn, *key, *stamp)
        with self.lock:
            self.entries[key] = stamp, rows, job["created_at"]
        return rows

    def committed(self, job, text):
        key = job["workspace_id"], job["conversation_id"]
        with self.lock:
            entry = self.entries.get(key)
            if entry and entry[0][1] == job["input_sequence"]:
                after, seq = entry[0]
                self.entries[key] = (
                    (after, seq + 2),
                    [
                        *entry[1],
                        {"sequence": seq, "role": "user", "text": job["input_text"]},
                        {"sequence": seq + 1, "role": "assistant", "text": text},
                    ],
                    datetime.now(UTC),
                )

    def discard(self, key):
        with self.lock:
            self.entries.pop(key, None)

    def collect(self, conn):
        with self.lock:
            keys = list(self.entries)
        for key in keys:
            if not repo.recently_used(conn, *key):
                self.discard(key)


cache = HistoryCache()


@dataclass
class Context:
    system: dict
    current: dict
    history: list
    summary: dict | None
    budget: int
    window: int
    output: int
    count: int
    threshold: int

    @classmethod
    def load(cls, conn, job):
        config, model = job["config"], job["snapshot"]
        window = min(
            config["context_budget_tokens"], model["context_limit_tokens"], settings().context_limit_tokens
        )
        output = min(config["max_output_tokens"], model["max_output_tokens"])
        system, current = message("system", config["system_prompt"]), message("user", job["input_text"])
        budget = window - output - cost(system) - cost(current) - 16
        if budget < 0:
            raise ChatError("CONTEXT_LIMIT_EXCEEDED")
        summary = repo.latest_summary(conn, job["workspace_id"], job["conversation_id"])
        history = cache.read(conn, job, summary["covered_through_seq"] if summary else 0)
        return cls(
            system,
            current,
            history,
            summary,
            budget,
            window,
            output,
            config["history_max_messages"],
            config["context_compression_threshold_percent"],
        )

    def historical(self):
        return [message(row["role"], row["text"]) for row in self.history]

    def prior(self):
        return [message("assistant", self.summary["summary_text"])] if self.summary else []

    def summary_request(self, remaining):
        raw = self.historical()
        size = sum(map(cost, self.prior() + raw))
        if not raw or (size * 100 < self.budget * self.threshold and len(raw) <= self.count):
            return None
        count = len(raw)
        # Keep the latest two complete turns where the configured caps permit.
        keep = min(4, self.count)
        prefix = max(2, count - keep)
        output = min(512, self.budget - 8, self.output)
        if output < 1 or remaining < timedelta(seconds=120):
            return None
        base = [message("system", SUMMARY_PROMPT), *self.prior()]
        available = self.window - output - 16 - sum(map(cost, base))
        used = 0
        for index in range(0, prefix, 2):
            pair = raw[index : index + 2]
            if sum(map(cost, pair)) > available:
                break
            available -= sum(map(cost, pair))
            used += 2
        if not used:
            return None
        return base + raw[:used], output, self.history[used - 1]["sequence"]

    def reply(self, summarized=False):
        raw, prior = self.historical(), self.prior()
        size = sum(map(cost, raw))
        drop = 0
        while drop < len(raw) and (len(raw) - drop > self.count or size > self.budget):
            size -= sum(map(cost, raw[drop : drop + 2]))
            drop += 2
        if size + sum(map(cost, prior)) > self.budget:
            prior = []
        clipped = drop or (self.summary and not prior)
        mode = "truncated" if clipped else ("summarized" if summarized or prior else "none")
        return (
            [self.system, *prior, *raw[drop:], self.current],
            mode,
            {
                "summary_id": str(self.summary["id"]) if prior else None,
                "first_sequence": self.history[drop]["sequence"] if drop < len(raw) else None,
                "last_sequence": self.history[-1]["sequence"] if drop < len(raw) else None,
                "context_compression": mode,
            },
        )

from threading import RLock

import psycopg
from langchain.agents import AgentState, create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from psycopg.rows import dict_row
from pydantic import PrivateAttr

from ai_platform.repositories import checkpoints as repo
from ai_platform.services.chat_context import ChatError
from ai_platform.services.chat_execution import LeaseLost, guard, reply
from ai_platform.settings import settings


def setup_checkpoints(saver):
    saver.conn.execute("CREATE SCHEMA IF NOT EXISTS graph")
    saver.conn.execute("SET search_path TO graph")
    saver.setup()
    repo.create_receipts_table(saver.conn)


class ChatState(AgentState):
    applied_input_seq: int


class ControlledModel(BaseChatModel):
    _job: dict = PrivateAttr()

    @property
    def _llm_type(self):
        return "platform-chat"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = reply(self._job)
        response = AIMessage(
            content=result["text"],
            id=str(self._job["output_message_id"]),
            response_metadata={k: v for k, v in result.items() if k != "text"},
        )
        return ChatResult(generations=[ChatGeneration(message=response)])


class FencedSaver(PostgresSaver):
    def __init__(self, conn, job):
        super().__init__(conn)
        self.lock = RLock()
        self.job = job

    def check(self, config):
        guard(self.conn, self.job)
        if config["configurable"]["thread_id"] != str(self.job["thread_id"]):
            raise LeaseLost()

    def receipt(self, checkpoint_id, task_id):
        repo.insert_receipt(self.conn, self.job["thread_id"], checkpoint_id, self.job["run_id"],
                            self.job["fencing_token"], task_id)

    def put(self, config, checkpoint, metadata, new_versions):
        with self.lock, self.conn.transaction():
            self.check(config)
            result = super().put(
                config,
                checkpoint,
                {**metadata, "run_id": str(self.job["run_id"]), "fencing_token": self.job["fencing_token"]},
                new_versions,
            )
            repo.set_run_checkpoint(self.conn, self.job["run_id"], self.job["thread_id"], checkpoint["id"])
            self.receipt(checkpoint["id"], "")
            return result

    def put_writes(self, config, writes, task_id, task_path=""):
        with self.lock, self.conn.transaction():
            self.check(config)
            super().put_writes(config, writes, task_id, task_path)
            self.receipt(config["configurable"]["checkpoint_id"], task_id)


def execute_graph(job):
    dsn = settings().checkpoint_database_url or settings().database_url
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET search_path TO graph")
        saver = FencedSaver(conn, job)
        model = ControlledModel()
        model._job = job
        graph = create_agent(model, tools=[], state_schema=ChatState, checkpointer=saver)
        config = {"configurable": {"thread_id": str(job["thread_id"])}}
        with conn.transaction():
            guard(conn, job)
            confirmed = repo.confirmed_checkpoint(conn, job["thread_id"], job["run_id"])
            if not confirmed and repo.thread_has_checkpoints(conn, job["thread_id"]):
                raise ChatError("CHECKPOINT_UNAVAILABLE")
        values = {}
        state = None
        if confirmed:
            config["configurable"]["checkpoint_id"] = confirmed["checkpoint_id"]
            state = graph.get_state(config)
            values = state.values
            if not values and not state.next:
                raise ChatError("CHECKPOINT_UNAVAILABLE")
        inputs = None
        if values.get("applied_input_seq", 0) < job["input_sequence"]:
            inputs = {
                "applied_input_seq": job["input_sequence"],
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    HumanMessage(id=str(job["input_message_id"]), content=job["input_text"]),
                ],
            }
        if inputs or (state and state.next):
            values = graph.invoke(inputs, config)
        for value in reversed(values.get("messages", [])):
            if value.id == str(job["output_message_id"]) and isinstance(value, AIMessage):
                return value.content
        raise ChatError("RESULT_UNKNOWN")

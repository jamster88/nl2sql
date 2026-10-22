"""The conversation over REST (arch8 Phase 3): the request names one, the job
echoes it, the answer says where it stands, and the server keeps the turns.

The turns themselves never cross the wire: a request names a conversation and
nothing more, and the server hands the agent the turns its own finished runs
made (R1's seam). What a client sees is `Answer.conversation` -- the turn, the
turns the question was read with, the words typed and the complete question
answered -- and three ways a conversation is shaped from outside: a 409 while
its last question runs, a DELETE that forgets it, and a `no` that forgets the
turn the wrong answer made.
"""

from __future__ import annotations

import threading

import pytest
from nl2sql_agent.api.conversations import ConversationStore
from nl2sql_agent.api.translate import answer_from_state
from nl2sql_agent.config import Settings
from nl2sql_agent.memory import MemoryNote
from nl2sql_agent.state import to_jsonable

from .conftest import ANSWERED, FakeAgent, ask, make_runner
from .test_feedback import FakeSink

READ_AS = "How many stores are there in each state?"


def remembering(question: str, principal, on_progress, turns=()) -> dict:
    """The pipeline as Phase 2 built it, in miniature: a question handed
    turns is read as a complete question, and the note says so."""
    remembering.turns.append(tuple(turns))
    state = dict(ANSWERED, question=question, asked=question, memory=MemoryNote())
    if turns:
        state.update(question=READ_AS, memory=MemoryNote(shown=len(turns), resolved=True, read_as=READ_AS))
    return state


@pytest.fixture
def conversing(make_client):
    """A server keeping conversations five turns deep, over the miniature."""
    remembering.turns = []
    return make_client(remembering, conversations=ConversationStore(depth=5))


# ---------------------------------------------------------------------------
# The translation
# ---------------------------------------------------------------------------


def test_outside_a_conversation_an_answer_has_no_conversation_block():
    state = dict(ANSWERED, asked="how many stores?", memory=MemoryNote())
    assert answer_from_state(state).conversation is None
    assert answer_from_state(None).conversation is None


@pytest.mark.parametrize("through", [dict, to_jsonable], ids=["the note itself", "a note through to_jsonable"])
def test_inside_one_the_block_carries_the_turn_and_the_reading(through):
    state = dict(ANSWERED, question=READ_AS, asked="And in each state?",
                 memory=MemoryNote(shown=2, resolved=True, read_as=READ_AS))
    state = through(state)
    block = answer_from_state(state, conversation="c-1", turn=3).conversation
    assert block.model_dump() == {"id": "c-1", "turn": 3, "remembered": 2, "asked": "And in each state?",
                                  "read_as": READ_AS, "resolved": True, "reason": ""}


def test_a_restatement_that_did_not_hold_says_why_and_reads_as_nothing():
    state = dict(ANSWERED, asked="and FY2024?", memory=MemoryNote(shown=1, reason="C2 literals: 'FY2024' missing"))
    block = answer_from_state(state, conversation="c-1", turn=2).conversation
    assert (block.read_as, block.resolved, block.reason) == ("", False, "C2 literals: 'FY2024' missing")


def test_a_run_that_left_no_state_reads_as_the_words_typed():
    """A question whose run raised has no state to say what was typed."""
    block = answer_from_state({}, conversation="c-1", turn=1, asked="and by store?").conversation
    assert (block.asked, block.remembered, block.resolved) == ("and by store?", 0, False)


# ---------------------------------------------------------------------------
# Asking in a conversation
# ---------------------------------------------------------------------------


def test_a_conversation_is_named_on_the_request_and_echoed_on_the_job(conversing):
    job = ask(conversing, "How many stores are there?", conversation="c-1")
    assert job["conversation"] == "c-1"
    assert job["answer"]["conversation"] == {"id": "c-1", "turn": 1, "remembered": 0,
                                             "asked": "How many stores are there?", "read_as": "",
                                             "resolved": False, "reason": ""}


def test_the_next_question_is_handed_the_turn_before_it_and_says_how_it_was_read(conversing):
    ask(conversing, "How many stores are there?", conversation="c-1")
    job = ask(conversing, "And in each state?", conversation="c-1")

    [turn] = remembering.turns[1]
    assert (turn.index, turn.question, turn.sql) == (1, "How many stores are there?", ANSWERED["sql"])
    assert turn.job_id and turn.rows == [["42"]]
    assert job["question"] == "And in each state?", "the job keeps the words typed"
    block = job["answer"]["conversation"]
    assert (block["turn"], block["remembered"], block["asked"], block["read_as"], block["resolved"]) == (
        2, 1, "And in each state?", READ_AS, True)


def test_a_question_asked_alone_beside_a_conversation_is_asked_alone(conversing):
    ask(conversing, "How many stores are there?", conversation="c-1")
    job = ask(conversing, "How many stores are there?")
    assert job["conversation"] is None and job["answer"]["conversation"] is None
    assert remembering.turns[1] == ()


def test_the_turn_number_is_right_after_the_window_has_slid(make_client):
    remembering.turns = []
    client = make_client(remembering, conversations=ConversationStore(depth=2))
    for question in ("q1", "q2", "q3"):
        ask(client, question, conversation="c-1")
    job = ask(client, "q4", conversation="c-1")
    assert job["answer"]["conversation"]["turn"] == 4
    assert [turn.index for turn in remembering.turns[3]] == [2, 3]


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "x" * 65, "ok\n"])
def test_an_id_outside_the_pattern_is_a_422(conversing, bad):
    answer = conversing.post("/v1/questions", json={"question": "q", "conversation": bad})
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "invalid_request"


def test_meta_publishes_the_ids_bound(conversing):
    assert conversing.get("/v1/meta").json()["limits"]["max_conversation_id_length"] == 64


# ---------------------------------------------------------------------------
# One question at a time: the 409
# ---------------------------------------------------------------------------


def test_a_question_while_the_last_runs_is_a_409_with_a_time_to_wait(make_client):
    gate = threading.Event()
    client = make_client(make_runner(gate=gate), conversations=ConversationStore(depth=5))
    first = client.post("/v1/questions", json={"question": "How many stores?", "conversation": "c-1"})
    assert first.status_code == 202

    refused = client.post("/v1/questions", json={"question": "And by state?", "conversation": "c-1"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conversation_busy"
    assert refused.headers["Retry-After"] == "60"
    assert client.post("/v1/questions", json={"question": "q", "conversation": "c-2"}).status_code == 202, (
        "another conversation is not held up")

    gate.set()
    client.get(f"/v1/questions/{first.json()['id']}", params={"wait": 10})
    assert ask(client, "And by state?", conversation="c-1")["answer"]["conversation"]["turn"] == 2


# ---------------------------------------------------------------------------
# Forgetting a conversation
# ---------------------------------------------------------------------------


def test_a_conversation_is_forgotten_on_request_and_begins_again_at_turn_one(conversing):
    ask(conversing, "How many stores are there?", conversation="c-1")
    assert conversing.delete("/v1/conversations/c-1").status_code == 204
    job = ask(conversing, "And in each state?", conversation="c-1")
    assert job["answer"]["conversation"]["turn"] == 1 and remembering.turns[1] == ()


def test_forgetting_a_conversation_there_is_not_is_a_404(conversing):
    answer = conversing.delete("/v1/conversations/never")
    assert answer.status_code == 404
    error = answer.json()["error"]
    assert error["code"] == "conversation_not_found"
    assert error["message"] == ("no conversation never. An idle one is forgotten after "
                                "API_CONVERSATION_TTL_SECONDS (3600 s).")


# ---------------------------------------------------------------------------
# A verdict that forgets a turn (R10)
# ---------------------------------------------------------------------------


@pytest.fixture
def judged(make_client):
    remembering.turns = []
    sink = FakeSink()
    return make_client(remembering, conversations=ConversationStore(depth=5), feedback=sink), sink


@pytest.mark.parametrize(("verdict", "kept"), [("no", False), ("incomplete", True), ("yes", True)])
def test_a_wrong_answer_is_forgotten_and_a_right_or_incomplete_one_kept(judged, verdict, kept):
    client, sink = judged
    first = ask(client, "How many stores are there?", conversation="c-1")
    receipt = client.post(f"/v1/questions/{first['id']}/feedback", json={"verdict": verdict})
    assert receipt.status_code == 201 and receipt.json()["verdict"] == verdict, "the receipt is unchanged"

    job = ask(client, "And in each state?", conversation="c-1")
    assert len(remembering.turns[1]) == (1 if kept else 0)
    # The number counts answered turns, forgotten ones too.
    assert job["answer"]["conversation"]["turn"] == 2


def test_a_verdict_the_staging_database_refused_forgets_nothing(judged):
    from nl2sql_agent.api.feedback import FeedbackUnavailable

    client, sink = judged
    first = ask(client, "How many stores are there?", conversation="c-1")
    sink.fail = FeedbackUnavailable("cannot record feedback: OperationalError")
    assert client.post(f"/v1/questions/{first['id']}/feedback", json={"verdict": "no"}).status_code == 503
    ask(client, "And in each state?", conversation="c-1")
    assert len(remembering.turns[1]) == 1


def test_withdrawing_a_no_does_not_bring_the_turn_back(judged):
    """The person asks again; the server does not guess."""
    client, sink = judged
    first = ask(client, "How many stores are there?", conversation="c-1")
    client.post(f"/v1/questions/{first['id']}/feedback", json={"verdict": "no"})
    assert client.delete(f"/v1/questions/{first['id']}/feedback").status_code == 204
    ask(client, "And in each state?", conversation="c-1")
    assert remembering.turns[1] == ()


# ---------------------------------------------------------------------------
# With memory off (CONVERSATION_MEMORY=0, arch8 23.8)
# ---------------------------------------------------------------------------


class RecordingAgent(FakeAgent):
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.turns: list[tuple] = []

    def run(self, question: str, *, principal=None, on_progress=None, turns=()) -> dict:
        self.turns.append(tuple(turns))
        return super().run(question, principal=principal, on_progress=on_progress, turns=turns)


def test_the_server_builds_its_store_from_the_settings(make_client, api_settings):
    from dataclasses import replace

    api = replace(api_settings, max_conversations=7, conversation_ttl_seconds=90.0)
    client = make_client(agent_factory=RecordingAgent, api=api, settings=Settings(conversation_memory=3))
    store = client.app.state.conversations
    assert isinstance(store, ConversationStore) and store is client.app.state.jobs.conversations
    assert (store.depth, store.ttl_seconds, store._cap) == (3, 90.0, 7)


def test_with_memory_off_no_store_is_built_and_the_id_is_echoed_and_nothing_remembered(make_client):
    agent = RecordingAgent()
    client = make_client(agent_factory=lambda: agent, settings=Settings(conversation_memory=0))
    assert client.app.state.conversations is None

    first = ask(client, "How many stores are there?", conversation="c-1")
    second = ask(client, "And in each state?", conversation="c-1")
    assert (first["conversation"], second["conversation"]) == ("c-1", "c-1")
    assert second["answer"]["conversation"] is None
    assert agent.turns == [(), ()]

    answer = client.delete("/v1/conversations/c-1")
    assert answer.status_code == 404
    assert answer.json()["error"] == {"code": "conversation_not_found",
                                      "message": "this server keeps no conversation (CONVERSATION_MEMORY=0)"}


def test_with_memory_on_the_agent_is_handed_the_turns(make_client):
    agent = RecordingAgent()
    client = make_client(agent_factory=lambda: agent)
    ask(client, "How many stores are there?", conversation="c-1")
    ask(client, "And in each state?", conversation="c-1")
    assert [len(turns) for turns in agent.turns] == [0, 1]


# ---------------------------------------------------------------------------
# The acceptance: two questions through a real ensemble, in one conversation
# ---------------------------------------------------------------------------


def test_two_questions_through_the_ensemble_in_one_conversation(make_client):
    """Phase 3's acceptance (arch8 section 10): the second question is read
    as a complete one, and every run's generator is shown the first's SQL."""
    from nl2sql_agent.ensemble import EnsembleAgent
    from nl2sql_agent.paraphrase import Rewording, Rewordings
    from nl2sql_agent.supervisor import ConversationScreening, Screening

    from tests.agent.conftest import FakeDatabase
    from tests.agent.test_graph import TABLES, make_agent, scripted

    from .test_ensemble_wire import SQL, WRITTEN

    by_state = "SELECT state, count(*) AS n FROM dim_store GROUP BY state"
    llm = scripted([SQL] * 4 + [by_state] * 4)
    llm.rewordings = Rewordings(rewordings=[Rewording(text=text, changed=f"varied {i}")
                                            for i, text in enumerate(WRITTEN, 1)])
    agent = make_agent(FakeDatabase(tables=TABLES), llm)
    client = make_client(agent_factory=lambda: EnsembleAgent(agent.settings, agent=agent))

    first = ask(client, "How many stores are there?", conversation="c-1")
    assert first["status"] == "succeeded" and first["answer"]["conversation"]["turn"] == 1
    asked_first = len(llm.plain_invocations)

    # The Supervisor reads the follow-up with the turn, restating it; then
    # the restatement is screened as 7.0 screens a question, and so is every
    # rewording of it.
    llm.screening = [
        ConversationScreening(verdict="proceed", intent="aggregate", entities=["state"], measure="store count",
                              period="none", standalone=READ_AS),
        Screening(verdict="proceed", intent="aggregate", entities=["state"], measure="store count", period="none"),
    ]
    second = ask(client, "And in each state?", conversation="c-1")

    answer = second["answer"]
    assert second["status"] == "succeeded" and answer["sql"] == by_state
    assert answer["conversation"] == {"id": "c-1", "turn": 2, "remembered": 1, "asked": "And in each state?",
                                      "read_as": READ_AS, "resolved": True, "reason": ""}
    assert answer["answer"].startswith(f"Read as: *{READ_AS}*")
    runs = llm.plain_invocations[asked_first:]
    assert len(runs) == len(answer["ensemble"]["candidates"]) == 4
    for messages in runs:
        contents = [message.content for message in messages]
        shown = contents.index("Asked earlier in this conversation (turn 1).\n\n"
                               "Question: How many stores are there?\n\nSQL:")
        assert contents[shown + 1] == SQL, "the first question's SQL, as the exemplar after it"

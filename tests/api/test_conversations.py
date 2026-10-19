"""The conversation store: the turns a question over REST is read with.

arch8 section 23.5 and R4, R10 and R11 of its risks by phase. A store with a
lock and a lifetime fails in the ordinary ways -- a conversation left busy, one
forgotten under a question, one that grows -- and in one of its own: the turn
a question is read with must be the right one, numbered right, and never one
the person said was wrong.
"""

from __future__ import annotations

import logging

import pytest
from nl2sql_agent.api.conversations import (
    CONVERSATION_ID_PATTERN,
    MAX_CONVERSATION_ID_LENGTH,
    ConversationBusy,
    ConversationStore,
)
from nl2sql_agent.api.jobs import RETRY_AFTER_SECONDS


class Clock:
    """The store's notion of now, moved by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def answered(question: str = "How many stores are there?", sql: str = "SELECT count(*) AS n FROM dim_store") -> dict:
    """A finished run that answered, in the shape both agents return."""
    return {
        "asked": question,
        "question": question,
        "verdict": "proceed",
        "sql": sql,
        "result": {"columns": ["n"], "rows": [[12]], "truncated": False},
        "narrative": "There are 12 stores.",
        "error": None,
    }


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock):
    def _make(**kwargs) -> ConversationStore:
        return ConversationStore(**{"depth": 5, "clock": clock, **kwargs})

    return _make


def ask(store: ConversationStore, job: str, *, owner: str | None = None, conversation: str = "c1",
        state=...) -> tuple[tuple, int]:
    """One question, from begin to finish: the turns it was handed and its
    number. It answers unless told what it ended with -- None for a run
    that raised."""
    turns, turn = store.begin(owner, conversation, job)
    store.finish(job, answered(f"question {job}") if state is ... else state)
    return turns, turn


# ---------------------------------------------------------------------------
# Whose conversation it is (R11)
# ---------------------------------------------------------------------------


def test_the_first_question_with_an_id_starts_the_conversation_at_turn_one(store):
    conversations = store()
    assert conversations.begin("ann", "c1", "j1") == ((), 1)
    assert conversations.count() == 1


def test_a_conversation_is_its_owners_and_another_owners_same_id_is_another(store):
    conversations = store()
    ask(conversations, "j1", owner="ann")
    assert conversations.begin("bob", "c1", "j2") == ((), 1), "nothing of ann's is shown to bob"
    turns, turn = conversations.begin("ann", "c1", "j3")
    assert ([t.job_id for t in turns], turn) == (["j1"], 2)
    assert conversations.count() == 2


def test_without_sign_in_every_caller_is_the_same_caller_and_the_id_keeps_them_apart(store):
    conversations = store()
    ask(conversations, "j1", conversation="page")
    assert conversations.begin(None, "window", "j2") == ((), 1)
    assert conversations.begin(None, "page", "j3")[1] == 2


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "x" * (MAX_CONVERSATION_ID_LENGTH + 1), "ok\n"])
def test_an_id_outside_the_pattern_is_refused(store, bad):
    """The request refuses it first (a 422); the store holds the same line."""
    with pytest.raises(ValueError, match="1 to 64"):
        store().begin(None, bad, "j1")


def test_the_pattern_the_request_holds_is_the_stores():
    assert CONVERSATION_ID_PATTERN == "^[A-Za-z0-9._-]{1,64}$"


# ---------------------------------------------------------------------------
# One question at a time
# ---------------------------------------------------------------------------


def test_a_second_question_while_the_first_is_in_flight_is_refused_with_a_time_to_wait(store):
    conversations = store()
    conversations.begin(None, "c1", "j1")
    with pytest.raises(ConversationBusy, match="c1 is still answering") as busy:
        conversations.begin(None, "c1", "j2")
    assert busy.value.retry_after == RETRY_AFTER_SECONDS
    conversations.finish("j1", None)
    assert conversations.begin(None, "c1", "j2")[1] == 1, "free again once the first has ended"


def test_finishing_with_an_answer_appends_a_turn_numbered_after_the_answered_ones(store):
    conversations = store()
    ask(conversations, "j1")
    ask(conversations, "j2")
    turns, turn = conversations.begin(None, "c1", "j3")
    assert [(t.index, t.job_id, t.question) for t in turns] == [(1, "j1", "question j1"), (2, "j2", "question j2")]
    assert turn == 3


@pytest.mark.parametrize(
    "ended",
    [None, {}, {**answered(), "verdict": "out_of_domain"}, {**answered(), "error": "gave up after 7 attempts"}],
    ids=["raised", "empty", "refused", "gave up"],
)
def test_a_question_that_answered_nothing_adds_no_turn_and_frees_the_conversation(store, ended):
    conversations = store()
    ask(conversations, "j1")
    ask(conversations, "j2", state=ended)
    turns, turn = conversations.begin(None, "c1", "j3")
    assert ([t.job_id for t in turns], turn) == (["j1"], 2), "the next turn's number counts answered ones"


def test_a_state_no_turn_can_be_made_from_frees_the_conversation_and_says_so(store, caplog):
    conversations = store()
    conversations.begin(None, "c1", "j1")
    with caplog.at_level(logging.WARNING, logger="nl2sql_agent.api.conversations"):
        conversations.finish("j1", {**answered(), "result": 42})
    assert conversations.begin(None, "c1", "j2") == ((), 1)
    assert "job j1 ended with a state no turn could be made from" in caplog.records[0].getMessage()


def test_finishing_a_job_no_conversation_is_busy_with_is_ignored(store):
    """The job store calls `finish` on every way a job ends, twice on some:
    a job asked alone, or one already finished, changes nothing."""
    conversations = store()
    ask(conversations, "j1")
    conversations.finish("j1", answered())
    conversations.finish("never-begun", answered())
    turns, turn = conversations.begin(None, "c1", "j2")
    assert ([t.job_id for t in turns], turn) == (["j1"], 2)


# ---------------------------------------------------------------------------
# Bounded (R4)
# ---------------------------------------------------------------------------


def test_the_window_keeps_the_last_turns_and_the_number_keeps_counting_past_it(store):
    conversations = store(depth=2)
    for job in ("j1", "j2", "j3", "j4"):
        ask(conversations, job)
    turns, turn = conversations.begin(None, "c1", "j5")
    assert [(t.index, t.job_id) for t in turns] == [(3, "j3"), (4, "j4")]
    assert turn == 5, "the turn number is right after the window has slid"


def test_an_idle_conversation_is_forgotten_after_the_ttl_and_begins_again_at_turn_one(store, clock):
    conversations = store(ttl_seconds=60)
    ask(conversations, "j1")
    clock.now += 61
    assert conversations.begin(None, "c1", "j2") == ((), 1)


def test_a_conversation_touched_inside_the_ttl_is_kept(store, clock):
    conversations = store(ttl_seconds=60)
    ask(conversations, "j1")
    clock.now += 59
    assert conversations.begin(None, "c1", "j2")[1] == 2


def test_a_busy_conversation_outlives_its_ttl(store, clock):
    """A question longer than the TTL is a slow question, not an idle
    conversation: its turn would otherwise be appended to nothing."""
    conversations = store(ttl_seconds=60)
    ask(conversations, "j1")
    conversations.begin(None, "c1", "j2")
    clock.now += 600
    conversations.begin(None, "other", "j3")  # prunes
    conversations.finish("j2", answered("question j2"))
    turns, turn = conversations.begin(None, "c1", "j4")
    assert ([t.job_id for t in turns], turn) == (["j1", "j2"], 3)


def test_the_cap_forgets_the_least_recently_touched_first(store, clock):
    conversations = store(max_conversations=2)
    ask(conversations, "j1", conversation="old")
    clock.now += 1
    ask(conversations, "j2", conversation="newer")
    clock.now += 1
    ask(conversations, "j3", conversation="old")  # touched: now the newest
    clock.now += 1
    ask(conversations, "j4", conversation="third")
    assert conversations.count() == 2
    assert conversations.begin(None, "newer", "j5") == ((), 1), "forgotten to make room"
    assert conversations.count() == 2


def test_a_busy_conversation_survives_the_cap(store):
    conversations = store(max_conversations=2)
    conversations.begin(None, "busy", "j1")
    ask(conversations, "j2", conversation="idle")
    ask(conversations, "j3", conversation="new")
    conversations.finish("j1", answered("question j1"))
    turns, turn = conversations.begin(None, "busy", "j4")
    assert ([t.job_id for t in turns], turn) == (["j1"], 2)


def test_with_every_conversation_busy_the_next_is_admitted_past_the_cap_and_trimmed_later(store):
    """The busy ones are the questions in flight, which the job store bounds."""
    conversations = store(max_conversations=1)
    conversations.begin(None, "a", "j1")
    conversations.begin(None, "b", "j2")
    assert conversations.count() == 2
    conversations.finish("j1", None)
    conversations.finish("j2", None)
    conversations.begin(None, "c", "j3")
    assert conversations.count() == 1


def test_the_bounds_are_at_least_one(store, clock):
    conversations = store(max_conversations=0, ttl_seconds=0)
    ask(conversations, "j1", conversation="a")
    clock.now += 0.5
    assert conversations.begin(None, "a", "j2")[1] == 2, "a TTL of at least a second"
    conversations.finish("j2", None)
    conversations.begin(None, "b", "j3")
    assert conversations.count() == 1, "a cap of at least one"
    assert (conversations.depth, conversations.ttl_seconds) == (5, 1.0)


def test_a_store_that_keeps_no_turn_is_not_built():
    with pytest.raises(ValueError, match="CONVERSATION_MEMORY=0"):
        ConversationStore(depth=0)


# ---------------------------------------------------------------------------
# Forgetting
# ---------------------------------------------------------------------------


def test_a_conversation_is_forgotten_by_its_owner_and_not_by_a_stranger(store):
    conversations = store()
    ask(conversations, "j1", owner="ann")
    assert conversations.forget("bob", "c1") is False
    assert conversations.forget("ann", "c1") is True
    assert conversations.forget("ann", "c1") is False
    assert conversations.begin("ann", "c1", "j2") == ((), 1)


def test_a_question_running_when_its_conversation_is_forgotten_adds_no_turn(store):
    conversations = store()
    conversations.begin(None, "c1", "j1")
    conversations.forget(None, "c1")
    conversations.begin(None, "c1", "j2")  # the same id, begun afresh
    conversations.finish("j1", answered("question j1"))
    conversations.finish("j2", answered("question j2"))
    turns, turn = conversations.begin(None, "c1", "j3")
    assert ([t.job_id for t in turns], turn) == (["j2"], 2)


def test_a_wrong_answers_turn_is_forgotten_and_its_number_not_reused(store):
    """R10: the turn goes; `answered` stays, so the next turn is not a
    second turn 2."""
    conversations = store()
    ask(conversations, "j1")
    ask(conversations, "j2")
    ask(conversations, "j3")
    assert conversations.forget_turn("j2") is True
    turns, turn = conversations.begin(None, "c1", "j4")
    assert ([(t.index, t.job_id) for t in turns], turn) == ([(1, "j1"), (3, "j3")], 4)
    assert conversations.forget_turn("j2") is False, "forgotten already"


def test_a_turn_is_forgotten_only_where_it_is(store):
    conversations = store()
    ask(conversations, "j1", conversation="a")
    ask(conversations, "j2", conversation="b")
    conversations.forget_turn("j1")
    assert [t.job_id for t in conversations.begin(None, "b", "j3")[0]] == ["j2"]


@pytest.mark.parametrize("why", ["asked alone", "answered nothing", "slid out", "conversation forgotten"])
def test_a_turn_no_conversation_remembers_is_not_found(store, why):
    conversations = store(depth=1)
    if why == "answered nothing":
        ask(conversations, "j1", state=None)
    elif why == "slid out":
        ask(conversations, "j1")
        ask(conversations, "j2")
    elif why == "conversation forgotten":
        ask(conversations, "j1")
        conversations.forget(None, "c1")
    assert conversations.forget_turn("j1") is False


def test_the_window_slides_on_after_a_turn_is_forgotten(store):
    conversations = store(depth=2)
    ask(conversations, "j1")
    ask(conversations, "j2")
    conversations.forget_turn("j1")
    ask(conversations, "j3")
    ask(conversations, "j4")
    turns, turn = conversations.begin(None, "c1", "j5")
    assert ([t.job_id for t in turns], turn) == (["j3", "j4"], 5)
    assert conversations.forget_turn("j2") is False, "slid out, and no longer found"

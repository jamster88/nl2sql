"""The conversations the REST API keeps: the turns a question is read with.

arch8 section 23.5. The agent is a function of its inputs -- a question, a
principal, and the turns before it -- and keeps nothing between calls, so
whoever asks the questions keeps the conversation. Over REST that is this
store, beside the job store and built only when `CONVERSATION_MEMORY` is
above zero:

* **Named by the client.** A request names its conversation (an id of its
  own choosing, a UUID for one); the first question with an id starts it,
  and there is no call that creates one.
* **Keyed by the person.** The key is `(owner, id)`, `owner` the signed-in
  principal or `None` for a service token or a server without sign-in --
  the job's owner. Two people's ids of one spelling are two conversations.
* **One question at a time.** A follow-up depends on the answer before it,
  so a question asked while the conversation's last is still queued or
  running is refused (`ConversationBusy`, a 409) rather than read with a
  turn that is not there yet.
* **Bounded.** The last `depth` turns, each bounded by `memory.py`; at most
  `max_conversations`, the least recently touched forgotten first; one
  untouched for `ttl_seconds` forgotten. A busy conversation is never
  forgotten by either, or the turn its question is about to add would be
  appended to nothing.
* **In memory only.** Nothing here is written anywhere; a restart forgets
  every conversation, and `/v1/meta` says so.

A conversation is marked busy by `begin`, when its question is admitted,
and freed by `finish`, which the job store calls on every way a job can
end -- the one thing that keeps a conversation from being refused for the
life of the process (R4 and S2 of arch8's risks by phase) -- and on a
cancel of a running question (8.0.1), with no state: the person has moved
on, and the run's answer, when it comes, is not remembered.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..memory import Turn, turn_from_state
from .jobs import RETRY_AFTER_SECONDS

log = logging.getLogger(__name__)

#: The longest conversation id a client may name. The request holds it
#: (`AskRequest.conversation`) and `/v1/meta` publishes it
#: (`Limits.max_conversation_id_length`), both from here.
MAX_CONVERSATION_ID_LENGTH = 64

_ID = f"[A-Za-z0-9._-]{{1,{MAX_CONVERSATION_ID_LENGTH}}}"

#: The request's pattern for an id: letters, digits, '.', '_' and '-'.
CONVERSATION_ID_PATTERN = f"^{_ID}$"

_CONVERSATION_ID = re.compile(_ID)

#: A conversation's key: its owner, then the id its client named.
Key = tuple[str | None, str]


class ConversationBusy(RuntimeError):
    """The conversation's last question has not been answered yet.

    Refused rather than queued: the question would be read without the turn
    it most likely follows up, and answered as if the person had not asked
    the one before it.
    """

    def __init__(self, message: str, *, retry_after: int = RETRY_AFTER_SECONDS) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class Conversation:
    """One conversation: its turns, and whether a question of it is in flight."""

    owner: str | None
    id: str
    turns: deque[Turn]
    #: Answered turns ever, for the next one's number -- not `len(turns)`,
    #: which stops growing once the window is full.
    answered: int = 0
    #: The job whose question is in flight, if one is.
    busy: str | None = None
    #: When a question last began or finished, by the store's clock; the
    #: TTL counts from here.
    touched: float = field(default=0.0)


class ConversationStore:
    """Every conversation this server is keeping, under one lock.

    `begin` is the one place anything is pruned, so an idle store costs
    nothing. `clock` is the store's notion of now, in seconds, injectable so
    the TTL can be tested without waiting an hour for it.
    """

    def __init__(
        self,
        *,
        depth: int,
        max_conversations: int = 200,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if depth < 1:
            raise ValueError("a conversation store keeps at least one turn; build none for CONVERSATION_MEMORY=0")
        self._depth = depth
        self._cap = max(1, max_conversations)
        self._ttl = max(1.0, float(ttl_seconds))
        self._clock = clock
        # Least recently touched first: every `begin` and `finish` moves
        # its conversation to the end.
        self._conversations: "OrderedDict[Key, Conversation]" = OrderedDict()
        # The job in flight in each busy conversation, and the job behind
        # each remembered turn: how `finish` and `forget_turn` find theirs
        # without a scan (S11).
        self._busy: dict[str, Key] = {}
        self._turns: dict[str, Key] = {}
        self._lock = threading.Lock()

    @property
    def depth(self) -> int:
        """The turns a conversation keeps: `CONVERSATION_MEMORY`."""
        return self._depth

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def count(self) -> int:
        with self._lock:
            return len(self._conversations)

    # --- a question's life ------------------------------------------------

    def begin(self, owner: str | None, conversation_id: str, job_id: str) -> tuple[tuple[Turn, ...], int]:
        """Admit `job_id`'s question to the conversation, starting it if
        need be: the turns it is read with, oldest first, and its number.

        Raises `ConversationBusy` when a question of the conversation is
        still in flight. Called by the job store after the queue has
        admitted the question, so a question the queue refuses never marks
        its conversation busy.
        """
        if not _CONVERSATION_ID.fullmatch(conversation_id):
            raise ValueError(f"a conversation id is 1 to {MAX_CONVERSATION_ID_LENGTH} of A-Z, a-z, 0-9, '.', '_', '-'")
        key = (owner, conversation_id)
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            conversation = self._conversations.get(key)
            if conversation is None:
                self._make_room_locked()
                conversation = Conversation(owner=owner, id=conversation_id, turns=deque(maxlen=self._depth))
                self._conversations[key] = conversation
            elif conversation.busy is not None:
                raise ConversationBusy(
                    f"conversation {conversation_id} is still answering its last question; "
                    "ask the next once that one has finished"
                )
            conversation.busy = job_id
            self._busy[job_id] = key
            self._touch_locked(key, now)
            return tuple(conversation.turns), conversation.answered + 1

    def finish(self, job_id: str, state: Mapping[str, Any] | None) -> None:
        """The job's question has ended: remember its turn when it answered,
        and free its conversation either way.

        A job no conversation is busy with -- asked alone, already finished,
        or in a conversation forgotten while it ran -- is ignored, which is
        what lets the job store call this on every path without asking
        first. Never raises: a conversation left busy is one refused until
        the process restarts.
        """
        with self._lock:
            key = self._busy.pop(job_id, None)
            if key is None:
                return
            conversation = self._conversations[key]
            conversation.busy = None
            self._touch_locked(key, self._clock())
            try:
                turn = turn_from_state(state, index=conversation.answered + 1, job_id=job_id) if state else None
            except (AttributeError, KeyError, TypeError, ValueError):
                # A state of a shape no turn can be made from: the
                # conversation is free already, and goes on without it.
                log.warning("job %s ended with a state no turn could be made from; it is not remembered", job_id,
                            exc_info=True)
                return
            if turn is None:
                return
            if len(conversation.turns) == self._depth:
                # The window slides: the oldest turn is no longer one a
                # verdict can find.
                self._turns.pop(conversation.turns[0].job_id, None)
            conversation.turns.append(turn)
            conversation.answered += 1
            self._turns[job_id] = key

    # --- forgetting ---------------------------------------------------------

    def forget(self, owner: str | None, conversation_id: str) -> bool:
        """Forget the caller's conversation of that id; False when they have
        none. A question of it still running adds no turn when it ends."""
        with self._lock:
            if (owner, conversation_id) not in self._conversations:
                return False
            self._drop_locked((owner, conversation_id))
            return True

    def forget_turn(self, job_id: str) -> bool:
        """Forget the turn that job's answer made -- a wrong answer is no
        turn to read the next question with (R10). `answered` is left as it
        is, so no later turn reuses the forgotten one's number. False when
        no conversation remembers it: asked alone, never answered, slid out
        of the window, or forgotten already."""
        with self._lock:
            key = self._turns.pop(job_id, None)
            if key is None:
                return False
            conversation = self._conversations[key]
            conversation.turns = deque((turn for turn in conversation.turns if turn.job_id != job_id),
                                       maxlen=self._depth)
            return True

    # --- housekeeping -------------------------------------------------------

    def _touch_locked(self, key: Key, now: float) -> None:
        self._conversations[key].touched = now
        self._conversations.move_to_end(key)

    def _expire_locked(self, now: float) -> None:
        """Forget every idle conversation untouched for the TTL."""
        for key, conversation in list(self._conversations.items()):
            if conversation.busy is None and now - conversation.touched > self._ttl:
                self._drop_locked(key)

    def _make_room_locked(self) -> None:
        """Forget the least recently touched idle conversations until a new
        one fits under the cap. Busy ones are passed over; were every one
        busy, the new one is admitted past the cap -- there are no more of
        them than questions in flight, which the job store bounds -- and the
        next `begin` trims."""
        for key, conversation in list(self._conversations.items()):
            if len(self._conversations) < self._cap:
                return
            if conversation.busy is None:
                self._drop_locked(key)

    def _drop_locked(self, key: Key) -> None:
        conversation = self._conversations.pop(key)
        for turn in conversation.turns:
            self._turns.pop(turn.job_id, None)
        if conversation.busy is not None:
            self._busy.pop(conversation.busy, None)

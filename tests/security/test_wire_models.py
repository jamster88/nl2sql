"""Every wire model in every service refuses a field it does not declare.

A lenient model drops what it does not know on the way in and on the way
out. On the way in that turns `pasword` into an empty password; on the way
out it is how the agent's model routing fields went missing over REST for
four releases while the documentation said a client could read them
(V6-18, V6-19). So the rule is held here, over every model in the four
contracts, rather than remembered per class.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from nl2sql_agent.api import models as api_models
from nl2sql_agent.console import models as console_models
from nl2sql_auth import models as auth_models
from nl2sql_review import models as review_models

MODULES = [api_models, console_models, review_models, auth_models]


def _wire_models(module) -> list[type[BaseModel]]:
    return [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, BaseModel) and value is not BaseModel
    ]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__)
def test_every_wire_model_forbids_what_it_does_not_declare(module):
    lenient = [m.__name__ for m in _wire_models(module) if m.model_config.get("extra") != "forbid"]
    assert lenient == []


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__)
def test_each_contract_has_models_to_check(module):
    """A module that stopped exporting its models would pass the test above
    by having nothing in it."""
    assert len(_wire_models(module)) >= 10

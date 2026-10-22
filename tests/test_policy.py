import copy
from decimal import Decimal as D

import pytest

from app.policy import DEFAULT_POLICY, evaluate, satisfied, validate


def ev(amount, role="accountant", wallet="OPERATIONS", new=False, spent=0, available=10**6, asset="USDT"):
    return evaluate(DEFAULT_POLICY, role=role, wallet_type=wallet, asset=asset, amount=D(amount), new_address=new,
                    spent_24h=D(spent), project_available=D(available))


def test_default_policy_is_valid():
    validate(DEFAULT_POLICY)


@pytest.mark.parametrize("breaks", [
    lambda p: p["limits"].update(god={"transaction": 1}),
    lambda p: p["rules"][0]["if"].update(moon_phase="full"),
    lambda p: p["rules"][0].update(approvers=[["janitor"]]),
    lambda p: p["rules"][0].update(approvers=[[]]),
    lambda p: p["wallet_access"].update(cfo=["PIGGY_BANK"]),
    lambda p: p.pop("rules"),
    lambda p: p["limits"]["cfo"].update(transaction=-5),
])
def test_validate_rejects(breaks):
    p = copy.deepcopy(DEFAULT_POLICY)
    breaks(p)
    with pytest.raises(ValueError):
        validate(p)


def test_approval_matrix():
    assert ev(80)["slots"] == [] and ev(250)["slots"] == []  # known counterparty within limits → auto
    assert ev(50, new=True) == {"error": None, "rule": "New address", "slots": [["cfo"]]}
    assert ev(600, role="cfo")["rule"] == "Over 250"
    assert ev(1500, role="cfo")["slots"] == [["owner"], ["cfo"]]
    assert ev(200, spent=600)["rule"] == "Over initiator limit"  # 600 + 200 > 750 daily
    assert ev(200, available=100)["rule"] == "Over project balance"
    assert ev(10, wallet="TREASURY")["error"]  # accountant has no treasury access
    assert ev(10, role="owner", wallet="TREASURY")["slots"] == [["cfo"], ["cfo"]]
    assert ev(10, asset="BTC")["error"]


def test_satisfied():
    assert satisfied([], [])
    assert satisfied([["cfo"]], [(1, "owner")])  # owner fills any slot
    assert not satisfied([["cfo"]], [(1, "accountant")])
    assert not satisfied([["owner"], ["cfo"]], [(1, "owner")])  # one person, one slot
    assert satisfied([["owner"], ["cfo"]], [(1, "cfo"), (2, "owner")])
    assert not satisfied([["owner"], ["cfo"]], [(1, "cfo"), (2, "cfo")])

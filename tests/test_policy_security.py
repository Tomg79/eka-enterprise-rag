"""Security-Regression: PolicyEngine (deny-by-default, fail-closed, Vererbung).

WICHTIG: Diese Tests bringen ihre EIGENEN policy/users mit (tmp), statt die veraenderliche
data/policy.json zu nutzen -- die wird vom Onboarding ueberschrieben. So bleibt der Test
stabil, egal welche Firma gerade eingerichtet ist.
"""
import json

import pytest

from policy import PolicyEngine

_POLICY = {
    "roles": {
        "base": {"inherits": [], "groups": ["PUBLIC"], "dms_tags": ["PUBLIC"],
                 "sap_tables": [], "sql_tables": [], "salesforce": {"mode": "none"}},
        "sales": {"inherits": ["base"], "groups": ["SALES"], "dms_tags": ["SALES"],
                  "sap_tables": ["T_MAT_MASTER"], "sql_tables": ["erp.T_SALES_ORDERS"],
                  "salesforce": {"mode": "all"}},
        "hr": {"inherits": ["base"], "groups": ["HR_CONFIDENTIAL"], "dms_tags": ["HR_CONFIDENTIAL"],
               "sap_tables": ["T_EMP_DATA"], "salesforce": {"mode": "none"}},
        "exec": {"inherits": ["sales", "hr"], "groups": [], "salesforce": {"mode": "all"}},
        "project_phoenix": {"inherits": [], "groups": ["PROJECT_PHOENIX"]},
    }
}
_USERS = {
    "users": {
        "dave": {"display": "Dave", "roles": ["base"]},
        "alice": {"display": "Alice", "roles": ["sales", "project_phoenix"]},
        "bob": {"display": "Bob", "roles": ["hr"]},
        "carol": {"display": "Carol", "roles": ["exec"]},
        "erin": {"display": "Erin", "roles": ["sales", "hr"]},
    }
}


@pytest.fixture
def pol(tmp_path):
    pp = tmp_path / "policy.json"
    up = tmp_path / "users.json"
    pp.write_text(json.dumps(_POLICY))
    up.write_text(json.dumps(_USERS))
    return PolicyEngine(str(pp), str(up))


def test_unknown_user_has_no_roles(pol):
    p = pol.get_principal("does-not-exist")
    assert p.roles == frozenset()
    assert pol.allowed_groups(p) == [] and pol.allowed_sql_tables(p) == []


def test_deny_by_default_base_role(pol):
    dave = pol.get_principal("dave")
    assert pol.allowed_sql_tables(dave) == []
    assert pol.salesforce_scope(dave)[0] == "none"
    assert not pol.can_access_sql_table(dave, "sap.T_EMP_DATA")


def test_role_separation_sales_vs_hr(pol):
    alice = pol.get_principal("alice")
    bob = pol.get_principal("bob")
    assert not pol.can_access_sql_table(alice, "sap.T_EMP_DATA")
    assert not pol.can_access_sql_table(bob, "erp.T_SALES_ORDERS")
    assert not pol.can_access_sql_table(bob, "sap.T_MAT_MASTER")


def test_inheritance_exec_gets_union(pol):
    carol = pol.get_principal("carol")
    for t in ("sap.T_EMP_DATA", "sap.T_MAT_MASTER", "erp.T_SALES_ORDERS"):
        assert pol.can_access_sql_table(carol, t)


def test_multi_role_union(pol):
    erin = pol.get_principal("erin")
    assert pol.can_access_sql_table(erin, "sap.T_EMP_DATA")
    assert pol.can_access_sql_table(erin, "erp.T_SALES_ORDERS")


def test_legacy_sap_tables_map_to_qualified(pol):
    bob = pol.get_principal("bob")
    assert pol.allowed_sap_tables(bob) == ["T_EMP_DATA"]
    assert pol.can_access_sql_table(bob, "sap.T_EMP_DATA")


def test_fail_closed_on_unreadable_policy(tmp_path):
    bad = PolicyEngine(str(tmp_path / "nope.json"), str(tmp_path / "nope.json"))
    p = bad.get_principal("alice")
    assert p.roles == frozenset() and bad.allowed_sql_tables(p) == []

"""Auto-Ableitung der Identity (Rollen+User) aus den Daten, wenn keine identity/ vorliegt."""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from onboarding.discovery import discover
from onboarding.identity_infer import infer_identity
from policy import PolicyEngine


def _gen(root):
    path = os.path.join(ROOT, "tools", "generate_enterprise_landscape.py")
    spec = importlib.util.spec_from_file_location("gen_landscape", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    os.makedirs(root, exist_ok=True)
    mod.gen_file_shares(root); mod.gen_databases(root); mod.gen_object_store(root)


@pytest.fixture
def derived(tmp_path):
    root = str(tmp_path / "co")
    _gen(root)                                  # KEINE identity/ erzeugt
    ls, _ = discover(root)                       # rekonstruiert ohne Identity
    assert not ls.identity.policy_path
    policy, users, _notes = infer_identity(ls)
    pp = tmp_path / "policy.json"; up = tmp_path / "users.json"
    pp.write_text(json.dumps(policy)); up.write_text(json.dumps(users))
    return PolicyEngine(str(pp), str(up)), policy["roles"], users["users"]


def test_users_and_roles_derived(derived):
    pol, roles, users = derived
    assert len(users) > 0                        # Benutzer aus Mitarbeiter-Tabelle
    assert "base" in roles and "hr" in roles and "sales" in roles
    assert any("T_SALARIES" in t for t in roles["hr"]["sql_tables"])


def test_derived_rbac_is_fail_closed(derived):
    pol, roles, users = derived
    hr_u = next(u for u, v in users.items() if "hr" in v["roles"])
    sa_u = next(u for u, v in users.items() if "sales" in v["roles"])
    assert pol.can_access_sql_table(pol.get_principal(hr_u), "hr.T_SALARIES")
    assert not pol.can_access_sql_table(pol.get_principal(sa_u), "hr.T_SALARIES")
    assert pol.can_access_sql_table(pol.get_principal(sa_u), "crm.T_CUSTOMERS")
    # unbekannter User -> keine Rechte
    assert pol.allowed_sql_tables(pol.get_principal("niemand")) == []

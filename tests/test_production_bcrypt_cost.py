"""Guard: production password hashing must stay at bcrypt cost 12.

Unit tests hash at cost 4 for speed (conftest _fast_bcrypt patches
_BCRYPT_ROUNDS at runtime). This file pins the REAL default by reading
the module source — immune to any monkeypatching. Weakening the cost
factor, or bypassing the constant inside hash_password, fails here
(docs/security.md pins cost 12).
"""

import ast
from pathlib import Path

import app.services.auth as auth_module

_SOURCE = Path(auth_module.__file__).read_text(encoding="utf-8")


def _module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    constants: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            constants[node.target.id] = node.value
    return constants


class TestProductionBcryptCost:
    def test_bcrypt_rounds_constant_is_12(self) -> None:
        constants = _module_constants(ast.parse(_SOURCE))
        assert "_BCRYPT_ROUNDS" in constants, "hash cost constant _BCRYPT_ROUNDS must exist"
        value = constants["_BCRYPT_ROUNDS"]
        assert isinstance(value, ast.Constant) and value.value == 12, (
            "docs/security.md pins password hashing at bcrypt cost 12; tests lower "
            "it to 4 via monkeypatch only — the production default must stay 12"
        )

    def test_hash_password_uses_the_rounds_constant(self) -> None:
        tree = ast.parse(_SOURCE)
        func = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "hash_password"
            ),
            None,
        )
        assert func is not None, "hash_password must exist in app.services.auth"
        used_names = {node.id for node in ast.walk(func) if isinstance(node, ast.Name)}
        assert "_BCRYPT_ROUNDS" in used_names, (
            "hash_password must hash with _BCRYPT_ROUNDS; inlining a cost factor "
            "would bypass the guard above"
        )

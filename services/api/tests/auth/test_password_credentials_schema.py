"""AUTH-F1A: user_password_credentials baseline schema contract tests.

纯静态读取 SQL 文件，不依赖真实数据库：
  1. 0001_initial.sql 声明恰好 5 列的最小密码凭证表；
  2. user_id 为主键并外键 users(id) ON DELETE CASCADE；
  3. updated_at trigger 与其他 auth 表对齐；
  4. checker 与两种 reset 清单收录该表。
"""

from __future__ import annotations

import re
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]
BASELINE_SQL = (REPO_ROOT / "infra" / "migrations" / "0001_initial.sql").read_text(
    encoding="utf-8"
)
CHECKER_SQL = (REPO_ROOT / "infra" / "scripts" / "check_schema_baseline.sql").read_text(
    encoding="utf-8"
)
RESET_DEV_SQL = (REPO_ROOT / "infra" / "scripts" / "reset_dev_keep_dict.sql").read_text(
    encoding="utf-8"
)
RESET_FULL_SQL = (REPO_ROOT / "infra" / "scripts" / "reset_full_keep_dict.sql").read_text(
    encoding="utf-8"
)


def _create_table_block(table: str) -> str:
    match = re.search(rf"CREATE TABLE {re.escape(table)} \((.*?)\n\);", BASELINE_SQL, re.DOTALL)
    assert match is not None, f"CREATE TABLE {table} missing from baseline"
    return match.group(1)


def _column_definitions(block: str) -> dict[str, str]:
    columns: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("CONSTRAINT"):
            continue
        name, _, rest = line.partition(" ")
        columns[name] = rest.rstrip(",")
    return columns


class TestUserPasswordCredentialsTable:
    def test_declares_exactly_the_five_contract_columns(self) -> None:
        columns = _column_definitions(_create_table_block("user_password_credentials"))
        assert set(columns) == {
            "user_id",
            "password_hash",
            "password_changed_at",
            "created_at",
            "updated_at",
        }
        assert columns["user_id"] == "uuid NOT NULL"
        assert columns["password_hash"] == "text NOT NULL"
        assert columns["password_changed_at"] == "timestamp with time zone DEFAULT now() NOT NULL"
        assert columns["created_at"] == "timestamp with time zone DEFAULT now() NOT NULL"
        assert columns["updated_at"] == "timestamp with time zone DEFAULT now() NOT NULL"

    def test_user_id_is_primary_key(self) -> None:
        assert (
            "ALTER TABLE ONLY user_password_credentials\n"
            "    ADD CONSTRAINT user_password_credentials_pkey PRIMARY KEY (user_id);"
        ) in BASELINE_SQL

    def test_user_id_references_users_with_cascade_delete(self) -> None:
        assert (
            "ALTER TABLE ONLY user_password_credentials\n"
            "    ADD CONSTRAINT user_password_credentials_user_id_fkey "
            "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;"
        ) in BASELINE_SQL

    def test_updated_at_trigger_matches_auth_table_pattern(self) -> None:
        assert (
            "CREATE TRIGGER trg_user_password_credentials_set_updated_at "
            "BEFORE UPDATE ON user_password_credentials "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        ) in BASELINE_SQL

    def test_password_hash_stays_out_of_user_identities(self) -> None:
        identities_block = _create_table_block("user_identities")
        assert "password" not in identities_block


class TestPasswordCredentialsBaselineSurfaces:
    def test_checker_expected_tables_include_password_credentials(self) -> None:
        block = re.search(
            r"expected_tables text\[\] := ARRAY\[\s*\n(.*?)\s*\];", CHECKER_SQL, re.DOTALL
        )
        assert block is not None
        assert "'user_password_credentials'" in block.group(1)

    def test_checker_guards_password_credentials_contract(self) -> None:
        for marker in (
            "'user_password_credentials.password_hash'",
            "'user_password_credentials.password_changed_at'",
            "user_password_credentials_pkey",
            "user_password_credentials_user_id_fkey",
            "trg_user_password_credentials_set_updated_at",
        ):
            assert marker in CHECKER_SQL, f"checker missing guard marker: {marker}"

    def test_dev_reset_lists_password_credentials(self) -> None:
        assert re.search(
            r"(?m)^\s*user_password_credentials,?\s*$", RESET_DEV_SQL
        ), "reset_dev_keep_dict.sql must TRUNCATE user_password_credentials"

    def test_full_reset_lists_password_credentials(self) -> None:
        assert re.search(
            r"(?m)^\s*user_password_credentials,?\s*$", RESET_FULL_SQL
        ), "reset_full_keep_dict.sql must DROP user_password_credentials"

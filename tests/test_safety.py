import pytest
from nl2sql.safety import Safety


# ---------------------------------------------------------------------------
# Basic allow cases
# ---------------------------------------------------------------------------


def test_safety_allows_select():
    s = Safety()
    r = s.check("SELECT * FROM users;")
    assert r.ok
    assert "sql" in r.data
    assert r.trace.stage == "safety"


def test_safety_allows_select_with_cte():
    s = Safety()
    sql = """
    WITH recent AS (
      SELECT id FROM users WHERE created_at > '2024-01-01'
    )
    SELECT * FROM users;
    """
    assert s.check(sql).ok


def test_safety_allows_select_with_comments_and_newlines():
    s = Safety()
    sql = "/* head */ \n -- inline\n SELECT 1; -- tail"
    assert s.check(sql).ok


def test_safety_allows_keywords_inside_string_literals():
    s = Safety()
    sql = "SELECT 'DROP TABLE x' AS note, 'delete from y' AS text;"
    assert s.check(sql).ok


def test_safety_semicolon_inside_string_literal_is_ignored():
    s = Safety()
    sql = "SELECT 'a; b; c' AS sample;"
    assert s.check(sql).ok


def test_safety_semicolon_inside_comment_is_ignored():
    s = Safety()
    sql = "SELECT 1 -- ; semicolon in comment\n"
    assert s.check(sql).ok


def test_safety_allows_multiple_ctes():
    s = Safety()
    sql = """
    WITH a AS (SELECT 1 AS x),
         b AS (SELECT 2 AS y)
    SELECT a.x, b.y FROM a CROSS JOIN b;
    """
    assert s.check(sql).ok


def test_safety_allows_with_recursive():
    s = Safety()
    sql = """
    WITH RECURSIVE cnt(x) AS (
      SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x < 3
    )
    SELECT * FROM cnt;
    """
    assert s.check(sql).ok


def test_safety_ignores_markdown_fences():
    s = Safety()
    sql = "```sql\nSELECT 1;\n```"
    assert s.check(sql).ok


def test_safety_allows_trailing_double_semicolon():
    s = Safety()
    assert s.check("SELECT 1;;").ok


# ---------------------------------------------------------------------------
# Forbidden statements (policy enforcement)
# ---------------------------------------------------------------------------


def test_safety_blocks_delete():
    s = Safety()
    r = s.check("DELETE FROM users;")
    assert not r.ok
    assert r.error


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE users SET name='X' WHERE id=1;",
        "INSERT INTO users(id) VALUES (1);",
        "DROP TABLE users;",
        "CREATE TABLE x(id INT);",
        "ALTER TABLE users ADD COLUMN x INT;",
        "ATTACH DATABASE 'hack.db' AS h;",
        "PRAGMA journal_mode=WAL;",
    ],
)
def test_safety_blocks_forbidden_statements(sql):
    s = Safety()
    r = s.check(sql)
    assert not r.ok
    assert r.error


def test_safety_blocks_stacked_delete_after_select():
    s = Safety()
    sql = "SELECT * FROM users; DELETE FROM users;"
    assert not s.check(sql).ok


def test_safety_blocks_stacked_delete_with_spaces():
    s = Safety()
    sql = "SELECT * FROM users ;   \n  DELETE users;"
    assert not s.check(sql).ok


def test_safety_blocks_multiple_nonempty_statements():
    s = Safety()
    assert not s.check("SELECT 'abc'; SELECT 1;").ok


# ---------------------------------------------------------------------------
# Obfuscation / bypass attempts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "/*D*/ROP TABLE users;",
        "PR/*x*/AGMA journal_mode=WAL;",
        "AL/* comment */TER TABLE x ADD COLUMN y INT;",
    ],
)
def test_safety_blocks_comment_obfuscation(sql):
    s = Safety()
    assert not s.check(sql).ok


@pytest.mark.parametrize(
    "sql",
    [
        "pragma journal_mode=WAL;",
        "  PRAGMA  user_version = 5 ; ",
        "\nATTACH DATABASE 'hack.db' AS h;",
    ],
)
def test_safety_blocks_case_and_spacing(sql):
    s = Safety()
    assert not s.check(sql).ok


def test_safety_blocks_zero_width_obfuscation():
    s = Safety()
    sql = "DR\u200dOP TABLE users;"
    assert not s.check(sql).ok


def test_safety_blocks_bom_prefixed_forbidden():
    s = Safety()
    sql = "\ufeffDROP TABLE x;"
    assert not s.check(sql).ok


@pytest.mark.parametrize(
    "sql",
    [
        "/* hidden */\u200bDELETE\u200b/* again */ FROM users;",
        "SELECT 1; \u200b /*x*/ DELETE /*y*/ FROM users;",
    ],
)
def test_safety_blocks_obfuscated_dml(sql):
    s = Safety()
    assert not s.check(sql).ok


# ---------------------------------------------------------------------------
# CTE-specific failures
# ---------------------------------------------------------------------------


def test_safety_blocks_delete_inside_cte():
    s = Safety()
    sql = """
    WITH bad AS (DELETE FROM users)
    SELECT * FROM users;
    """
    assert not s.check(sql).ok


def test_safety_blocks_dml_inside_recursive_cte():
    s = Safety()
    sql = """
    WITH RECURSIVE bad(x) AS (
      DELETE FROM users
    )
    SELECT * FROM users;
    """
    assert not s.check(sql).ok


# ---------------------------------------------------------------------------
# EXPLAIN gate
# ---------------------------------------------------------------------------


def test_safety_allows_explain_select_when_enabled():
    s = Safety(allow_explain=True)
    assert s.check("EXPLAIN SELECT * FROM users;").ok


def test_safety_blocks_explain_select_when_disabled():
    s = Safety(allow_explain=False)
    assert not s.check("EXPLAIN SELECT * FROM users;").ok


@pytest.mark.parametrize(
    "q",
    [
        "explain   select 1;",
        "EXPLAIN\nSELECT 1;",
    ],
)
def test_safety_explain_various_spacing_when_enabled(q):
    s = Safety(allow_explain=True)
    assert s.check(q).ok


# ---------------------------------------------------------------------------
# Misc invariants
# ---------------------------------------------------------------------------


def test_safety_duration_ms_is_int():
    s = Safety()
    r = s.check("SELECT 1;")
    assert isinstance(r.trace.duration_ms, int)


def test_safety_stage_name_constant():
    s = Safety()
    r = s.check("SELECT 1;")
    assert r.trace.stage == "safety"

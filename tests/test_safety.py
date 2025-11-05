from nl2sql.safety import Safety
import pytest


def test_safety_allows_select():
    s = Safety()
    result = s.check("SELECT * FROM users;")
    assert result.ok
    assert "sql" in result.data
    assert result.trace.stage == "safety"


def test_safety_allows_with_select_cte():
    s = Safety()
    sql = """
    WITH recent AS (
      SELECT id FROM users WHERE created_at > '2024-01-01'
    )
    SELECT * FROM users u JOIN recent r ON u.id = r.id;
    """
    r = s.check(sql)
    assert r.ok


def test_safety_allows_select_with_comments_and_newlines():
    s = Safety()
    sql = "/* head */ \n -- inline\n SELECT 1; -- tail"
    r = s.check(sql)
    assert r.ok


def test_safety_allows_keywords_inside_string_literals():
    s = Safety()
    sql = "SELECT 'DROP TABLE x' as note, 'delete from y' as text;"
    r = s.check(sql)
    assert r.ok, r.error


def test_safety_blocks_delete():
    s = Safety()
    result = s.check("DELETE FROM users;")
    assert not result.ok
    assert any("Forbidden" in e or "Non-SELECT" in e for e in (result.error or []))


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
    res = s.check(sql)
    assert not res.ok


def test_safety_blocks_stacked_delete_after_select():
    s = Safety()
    sql = "SELECT * FROM users; DELETE FROM users;"
    r = s.check(sql)
    assert not r.ok


def test_safety_blocks_stacked_delete_with_spaces():
    s = Safety()
    sql = "SELECT * FROM users ;   \n  DELETE users;"
    r = s.check(sql)
    assert not r.ok


def test_safety_blocks_delete_inside_cte():
    s = Safety()
    sql = """
    WITH bad AS (DELETE FROM users)
    SELECT * FROM users;
    """
    r = s.check(sql)
    assert not r.ok


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
    r = s.check(sql)
    assert not r.ok


@pytest.mark.parametrize(
    "sql",
    [
        "pragma journal_mode=WAL;",  # lower-case
        "  PRAGMA  user_version = 5 ; ",
        "\nATTACH DATABASE 'hack.db' AS h;",
    ],
)
def test_safety_blocks_forbidden_case_and_spacing(sql):
    s = Safety()
    r = s.check(sql)
    assert not r.ok


def test_safety_blocks_multiple_nonempty_statements_even_if_second_is_comment():
    s = Safety()
    sql = "SELECT 1;  -- now do something bad\n"
    sql_bad = "SELECT 1;  /* spacer */  DROP TABLE x;"
    assert s.check(sql).ok
    assert not s.check(sql_bad).ok


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


def test_safety_blocks_zero_width_obfuscation_in_keyword():
    s = Safety()
    # "DROP" با zero-width joiner وسط حروف
    bad = "DR\u200dOP TABLE users;"
    r = s.check(bad)
    assert not r.ok


def test_safety_ignores_markdown_fences():
    s = Safety()
    sql = "```sql\nSELECT 1;\n```"
    assert s.check(sql).ok


def test_safety_semicolon_inside_string_literal_is_ignored():
    s = Safety()
    sql = "SELECT 'a; b; c' AS sample;"
    assert s.check(sql).ok


def test_safety_forbidden_keyword_inside_string_literal_ok():
    s = Safety()
    sql = "SELECT 'DROP TABLE x' AS note, 'delete from y' AS text;"
    assert s.check(sql).ok


def test_safety_reports_offending_token_in_error_message():
    s = Safety()
    r = s.check("  \n  ReIndex  users;")
    assert not r.ok
    assert any("reindex" in e.lower() for e in (r.error or []))


def test_safety_multiple_statements_with_masked_strings_is_blocked():
    s = Safety()
    sql = "SELECT 'abc'; SELECT 1;"
    r = s.check(sql)
    assert not r.ok


def test_safety_duration_ms_is_int():
    s = Safety()
    r = s.check("SELECT 1;")
    assert isinstance(r.trace.duration_ms, int)


def test_safety_allows_explain_select_when_enabled():
    s = Safety(allow_explain=True)
    r = s.check("EXPLAIN SELECT * FROM users;")
    assert r.ok


def test_safety_blocks_explain_select_when_disabled():
    s = Safety(allow_explain=False)
    r = s.check("EXPLAIN SELECT * FROM users;")
    assert not r.ok


def test_safety_blocks_forbidden_inside_cte_body():
    s = Safety()
    sql = """
    WITH bad AS (DELETE FROM users)
    SELECT * FROM users;
    """
    assert not s.check(sql).ok


def test_safety_permits_with_comments_and_newlines_complex():
    s = Safety()
    sql = """
    /* head */ WITH a AS (SELECT 1 /*x*/ AS x) -- inline
    , b AS (SELECT 2 AS y) /* tail */
    SELECT a.x, b.y FROM a JOIN b; -- end
    """
    assert s.check(sql).ok


def test_safety_blocks_bom_prefixed_forbidden():
    s = Safety()
    sql = "\ufeffDROP TABLE x;"
    assert not s.check(sql).ok


def test_safety_allows_trailing_double_semicolon():
    s = Safety()
    assert s.check("SELECT 1;;").ok


@pytest.mark.parametrize("q", ["explain   select 1;", "EXPLAIN\nSELECT 1;"])
def test_safety_explain_various_spacing_when_enabled(q):
    s = Safety(allow_explain=True)
    assert s.check(q).ok


def test_safety_stage_name_constant():
    s = Safety()
    r = s.check("SELECT 1;")
    assert r.trace.stage == "safety"


# Semicolon inside comments should NOT count as new statement
def test_safety_semicolon_inside_comment_is_ignored():
    s = Safety()
    sql = "SELECT 1 -- ; semicolon in comment\n"
    r = s.check(sql)
    assert r.ok, r.error


# Recursive CTE with DML inside should be blocked
def test_safety_blocks_dml_inside_recursive_cte():
    s = Safety()
    sql = """
    WITH RECURSIVE bad(x) AS (
      DELETE FROM users
    )
    SELECT * FROM users;
    """
    r = s.check(sql)
    assert not r.ok


# --- 3) Zero-width spaces + comment obfuscation around DML
@pytest.mark.parametrize(
    "q",
    [
        "/* hidden */\u200bDELETE\u200b/* again */ FROM users;",
        "SELECT 1; \u200b /*x*/ DELETE /*y*/ FROM users;",
    ],
)
def test_safety_obfuscated_dml_is_blocked(q):
    s = Safety()
    r = s.check(q)
    assert not r.ok


# Multi-statement with stray semicolon and whitespace
def test_safety_blocks_stacked_statements_with_whitespace():
    s = Safety()
    q = "SELECT 1 ;   \n  DELETE FROM users;"
    r = s.check(q)
    assert not r.ok


#  ALLOW EXPLAIN (config gate)
@pytest.mark.parametrize("q", ["explain   select 1;", "EXPLAIN\nSELECT 1;"])
def test_safety_explain_allowed_when_enabled(q):
    s = Safety(allow_explain=True)
    assert s.check(q).ok

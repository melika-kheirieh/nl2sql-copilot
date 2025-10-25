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

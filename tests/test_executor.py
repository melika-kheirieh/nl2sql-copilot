from nl2sql.executor import Executor
from adapters.db.sqlite_adapter import SQLiteAdapter


def test_executor_runs_select(tmp_path):
    db_path = tmp_path / "test.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users(id INT, name TEXT);")
    conn.execute("INSERT INTO users VALUES (1, 'Alice');")
    conn.commit()
    conn.close()

    ex = Executor(SQLiteAdapter(str(db_path)))
    res = ex.run("SELECT * FROM users;")
    assert res.ok
    assert res.data["rows"][0][1] == "Alice"

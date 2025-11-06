"""Curated Chinook subset for deterministic benchmarking.

This module bundles a tiny, self-contained SQLite dataset inspired by the
classic Chinook schema.  It keeps the data footprint extremely small while
still covering the relational patterns we want to exercise during the
benchmark (joins, aggregations, grouping, ordering).

The public surface consists of:

```
CHINOOK_DATASET       # list of benchmark examples (question + gold SQL)
SCHEMA_PREVIEW        # formatted schema preview fed into the pipeline
DEFAULT_DB_PATH       # canonical on-disk location for the SQLite file
ensure_chinook_subset_db(path)  # idempotent DB builder
```

The database is created on demand; if the file already exists the function is
effectively a no-op.  Keeping the creation logic in Python means the repo does
not need to ship a binary SQLite artifact and the dataset can be regenerated on
any machine (including CI) with zero extra tooling.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List

# ---------------------------------------------------------------------------
# Public dataset description
# ---------------------------------------------------------------------------


DEFAULT_DB_PATH = Path("data/chinook_benchmark.sqlite")


SCHEMA_PREVIEW = """
CREATE TABLE artists(
    ArtistId INTEGER PRIMARY KEY,
    Name TEXT
);

CREATE TABLE albums(
    AlbumId INTEGER PRIMARY KEY,
    Title TEXT,
    ArtistId INTEGER REFERENCES artists(ArtistId)
);

CREATE TABLE tracks(
    TrackId INTEGER PRIMARY KEY,
    Name TEXT,
    AlbumId INTEGER REFERENCES albums(AlbumId),
    UnitPrice REAL
);

CREATE TABLE customers(
    CustomerId INTEGER PRIMARY KEY,
    FirstName TEXT,
    LastName TEXT,
    Country TEXT
);

CREATE TABLE invoices(
    InvoiceId INTEGER PRIMARY KEY,
    CustomerId INTEGER REFERENCES customers(CustomerId),
    BillingCountry TEXT,
    Total REAL
);

CREATE TABLE invoice_items(
    InvoiceLineId INTEGER PRIMARY KEY,
    InvoiceId INTEGER REFERENCES invoices(InvoiceId),
    TrackId INTEGER REFERENCES tracks(TrackId),
    UnitPrice REAL,
    Quantity INTEGER
);

CREATE TABLE employees(
    EmployeeId INTEGER PRIMARY KEY,
    FirstName TEXT,
    LastName TEXT,
    City TEXT
);
""".strip()


CHINOOK_DATASET: List[Dict[str, str]] = [
    {
        "id": "Q1",
        "question": "list all customers with their country",
        "gold_sql": (
            "SELECT CustomerId, FirstName, LastName, Country "
            "FROM customers ORDER BY CustomerId;"
        ),
    },
    {
        "id": "Q2",
        "question": "show total invoice amount per billing country",
        "gold_sql": (
            "SELECT BillingCountry, SUM(Total) AS total_revenue "
            "FROM invoices GROUP BY BillingCountry ORDER BY total_revenue DESC;"
        ),
    },
    {
        "id": "Q3",
        "question": "show three albums with the highest sales amount",
        "gold_sql": (
            "SELECT a.Title, SUM(ii.Quantity * ii.UnitPrice) AS total_sales "
            "FROM albums a "
            "JOIN tracks t ON a.AlbumId = t.AlbumId "
            "JOIN invoice_items ii ON t.TrackId = ii.TrackId "
            "GROUP BY a.AlbumId ORDER BY total_sales DESC LIMIT 3;"
        ),
    },
    {
        "id": "Q4",
        "question": "artists who released more than one album",
        "gold_sql": (
            "SELECT ar.Name, COUNT(*) AS album_count "
            "FROM artists ar JOIN albums al ON ar.ArtistId = al.ArtistId "
            "GROUP BY ar.ArtistId HAVING COUNT(*) > 1 ORDER BY album_count DESC;"
        ),
    },
    {
        "id": "Q5",
        "question": "number of employees per city",
        "gold_sql": (
            "SELECT City, COUNT(*) AS employees FROM employees "
            "GROUP BY City ORDER BY employees DESC;"
        ),
    },
    {
        "id": "Q6",
        "question": "average invoice total for each customer",
        "gold_sql": (
            "SELECT c.FirstName || ' ' || c.LastName AS customer_name, "
            "AVG(i.Total) AS avg_invoice_total FROM customers c "
            "JOIN invoices i ON c.CustomerId = i.CustomerId "
            "GROUP BY c.CustomerId ORDER BY avg_invoice_total DESC;"
        ),
    },
    {
        "id": "Q7",
        "question": "total quantity of tracks sold by album",
        "gold_sql": (
            "SELECT a.Title, SUM(ii.Quantity) AS units_sold FROM albums a "
            "JOIN tracks t ON a.AlbumId = t.AlbumId "
            "JOIN invoice_items ii ON t.TrackId = ii.TrackId "
            "GROUP BY a.AlbumId ORDER BY units_sold DESC;"
        ),
    },
    {
        "id": "Q8",
        "question": "customers with more than two invoices",
        "gold_sql": (
            "SELECT c.FirstName || ' ' || c.LastName AS customer_name, "
            "COUNT(*) AS invoice_count FROM customers c "
            "JOIN invoices i ON c.CustomerId = i.CustomerId "
            "GROUP BY c.CustomerId HAVING COUNT(*) > 2 "
            "ORDER BY invoice_count DESC;"
        ),
    },
]


# ---------------------------------------------------------------------------
# SQLite seeding helpers
# ---------------------------------------------------------------------------


def _exec_many(cur: sqlite3.Cursor, query: str, rows: Iterable[Iterable]) -> None:
    cur.executemany(query, list(rows))


def ensure_chinook_subset_db(path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Create the SQLite database with deterministic sample data if missing."""

    db_path = Path(path)
    if db_path.exists():
        return db_path

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE artists(
            ArtistId INTEGER PRIMARY KEY,
            Name TEXT
        );

        CREATE TABLE albums(
            AlbumId INTEGER PRIMARY KEY,
            Title TEXT,
            ArtistId INTEGER REFERENCES artists(ArtistId)
        );

        CREATE TABLE tracks(
            TrackId INTEGER PRIMARY KEY,
            Name TEXT,
            AlbumId INTEGER REFERENCES albums(AlbumId),
            UnitPrice REAL
        );

        CREATE TABLE customers(
            CustomerId INTEGER PRIMARY KEY,
            FirstName TEXT,
            LastName TEXT,
            Country TEXT
        );

        CREATE TABLE invoices(
            InvoiceId INTEGER PRIMARY KEY,
            CustomerId INTEGER REFERENCES customers(CustomerId),
            BillingCountry TEXT,
            Total REAL
        );

        CREATE TABLE invoice_items(
            InvoiceLineId INTEGER PRIMARY KEY,
            InvoiceId INTEGER REFERENCES invoices(InvoiceId),
            TrackId INTEGER REFERENCES tracks(TrackId),
            UnitPrice REAL,
            Quantity INTEGER
        );

        CREATE TABLE employees(
            EmployeeId INTEGER PRIMARY KEY,
            FirstName TEXT,
            LastName TEXT,
            City TEXT
        );
        """
    )

    _exec_many(
        cur,
        "INSERT INTO artists(ArtistId, Name) VALUES (?, ?)",
        [
            (1, "The Imaginaries"),
            (2, "Acoustic Elements"),
            (3, "Electric Pulse"),
        ],
    )

    _exec_many(
        cur,
        "INSERT INTO albums(AlbumId, Title, ArtistId) VALUES (?, ?, ?)",
        [
            (1, "Morning Lights", 1),
            (2, "City Echoes", 1),
            (3, "Unplugged Stories", 2),
            (4, "Voltage", 3),
        ],
    )

    _exec_many(
        cur,
        "INSERT INTO tracks(TrackId, Name, AlbumId, UnitPrice) VALUES (?, ?, ?, ?)",
        [
            (1, "Sunrise", 1, 1.29),
            (2, "Downtown Ride", 2, 0.99),
            (3, "Campfire Nights", 3, 0.89),
            (4, "Circuit Breaker", 4, 1.49),
            (5, "Neon Shadows", 2, 1.09),
            (6, "Acoustic Breeze", 3, 0.95),
        ],
    )

    _exec_many(
        cur,
        "INSERT INTO customers(CustomerId, FirstName, LastName, Country) VALUES (?, ?, ?, ?)",
        [
            (1, "Luís", "Gonçalves", "Brazil"),
            (2, "Leonie", "Köhler", "Germany"),
            (3, "François", "Tremblay", "Canada"),
            (4, "Bjørn", "Hansen", "Norway"),
        ],
    )

    _exec_many(
        cur,
        "INSERT INTO invoices(InvoiceId, CustomerId, BillingCountry, Total) VALUES (?, ?, ?, ?)",
        [
            (1, 1, "Brazil", 14.91),
            (2, 1, "Brazil", 9.90),
            (3, 2, "Germany", 5.94),
            (4, 2, "Germany", 13.86),
            (5, 3, "Canada", 22.86),
            (6, 3, "Canada", 15.98),
            (7, 3, "Canada", 8.91),
            (8, 4, "Norway", 3.96),
        ],
    )

    _exec_many(
        cur,
        """
        INSERT INTO invoice_items(InvoiceLineId, InvoiceId, TrackId, UnitPrice, Quantity)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (1, 1, 1, 1.29, 4),
            (2, 1, 2, 0.99, 2),
            (3, 2, 5, 1.09, 3),
            (4, 3, 3, 0.89, 5),
            (5, 3, 6, 0.95, 2),
            (6, 4, 2, 0.99, 6),
            (7, 4, 5, 1.09, 4),
            (8, 5, 1, 1.29, 6),
            (9, 5, 4, 1.49, 3),
            (10, 6, 3, 0.89, 7),
            (11, 7, 6, 0.95, 4),
            (12, 7, 5, 1.09, 3),
            (13, 8, 2, 0.99, 2),
        ],
    )

    _exec_many(
        cur,
        "INSERT INTO employees(EmployeeId, FirstName, LastName, City) VALUES (?, ?, ?, ?)",
        [
            (1, "Andrew", "Adams", "Edmonton"),
            (2, "Nancy", "Edwards", "Calgary"),
            (3, "Jane", "Peacock", "Calgary"),
            (4, "Margaret", "Park", "Vancouver"),
        ],
    )

    conn.commit()
    conn.close()

    return db_path


__all__ = [
    "CHINOOK_DATASET",
    "SCHEMA_PREVIEW",
    "DEFAULT_DB_PATH",
    "ensure_chinook_subset_db",
]


CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT
);

INSERT INTO users (name, city)
VALUES ('Alice', 'Tehran'), ('Bob', 'Karaj'), ('Caro', 'Isfahan');

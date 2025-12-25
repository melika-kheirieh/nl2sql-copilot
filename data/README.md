# Data folder

This repository includes small, redistributable SQLite databases to make help and smoke tests self-contained.

## Included databases
- `demo.db`: Minimal demo database used by `make demo-up`.
- `bench_demo.db`: Small database used for benchmark UI / smoke benchmark runs.
- `Chinook_Sqlite.sqlite`: Public sample DB (music store) used for demo queries.
- `WMSales.sqlite`: Public sample DB used for demo/benchmark queries.

## Large datasets (not included)
Large benchmark datasets (e.g., Spider) are intentionally not stored in this repository to keep it lightweight and CI-friendly.
See `data/spider/README.md` for instructions.

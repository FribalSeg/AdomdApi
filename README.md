# OLAP Pivot Studio API

FastAPI application for authenticated OLAP querying with Pyadomd plus a PivotTable.js web interface.

## Features

- Basic Auth protected API and web UI.
- Query execution endpoint for read-only `xmla`, `dax`, and `mdx` requests.
- Metadata endpoints:
	- `SELECT * FROM $SYSTEM.TMSCHEMA_TABLES`
	- `SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS`
	- `SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES`
	- `SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS`
- Additional discover metadata endpoints for model exploration.
- Root web tool (`/`) with:
	- Query editor and run action
	- PivotTable.js drag-and-drop table designer
	- Filter/value/row/column pivot controls
	- Copy API code and pivot config
	- Save/load/update/delete query definitions in SQLite
- Legacy endpoint `/generate_olap_report` remains available.

## Run

1. Create `.env` from `.env.sample` and set valid values.
2. Install dependencies from `requirements.txt`.
3. Start server:

```bash
uvicorn main:app --reload
```

4. Open:
	 - UI: `http://127.0.0.1:8000/`
	 - API docs: `http://127.0.0.1:8000/docs`

## Important Notes

- UI and API operations require Basic Auth.
- Saved definitions are shared across authenticated users.
- SQLite file defaults to `app.db` in project root, configurable with `SQLITE_PATH`.


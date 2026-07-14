import sys
import os
import secrets
import sqlite3
import json
import re
from datetime import datetime, timezone
from typing import Annotated
from pathlib import Path
from pydantic import BaseModel, Field
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles


app = FastAPI(
    title='OLAP Report API',
    description='API to generate OLAP reports using Pyadomd and FastAPI',
    docs_url='/docs',
    redoc_url=None,
    swagger_ui_parameters={
        "docExpansion": "list",
        "syntaxHighlight.theme": "obsidian",
        "persistAuthorization": True,
        "tryItOutEnabled": True,  # "Try it out" section open by default
    }
)

security = HTTPBasic()
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'app.db'
STATIC_DIR = BASE_DIR / 'static'

ALLOWED_QUERY_LANGUAGES = {'xmla', 'dax', 'mdx'}
READONLY_DENYLIST = {
    'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER', 'MERGE',
    'TRUNCATE', 'GRANT', 'REVOKE', 'BACKUP', 'RESTORE', 'PROCESS',
    'REFRESH', 'RENAME', 'ATTACH', 'DETACH', 'CALL'
}
XMLA_WRITE_TOKENS = {
    '<ALTER', '<CREATE', '<DELETE', '<DROP', '<PROCESS', '<MERGE',
    '<REFRESH', '<BATCH', '<TRANSACTION'
}


def load_env():
    env_path = BASE_DIR / '.env'
    if not env_path.exists():
        return

    with open(env_path) as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            key, value = line.strip().split('=', 1)
            # clean value by removing surrounding quotes if present and trim
            # whitespace
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            value = value.strip()

            os.environ[key] = value


def _normalize_column_ref(reference: str) -> str:
    ref = reference.strip()
    if not ref:
        raise ValueError('Column reference cannot be empty')
    if '[' not in ref or ']' not in ref:
        raise ValueError(f'Invalid column reference: {reference}')
    return ref


def _normalize_measure_ref(reference: str) -> str:
    ref = reference.strip()
    if not ref:
        raise ValueError('Measure reference cannot be empty')
    if ref.startswith('[') and ref.endswith(']'):
        return ref
    if '[' in ref or ']' in ref:
        return ref
    return f'[{ref}]'


def _measure_alias(measure_ref: str) -> str:
    cleaned = measure_ref.strip()
    if cleaned.startswith('[') and cleaned.endswith(']'):
        return cleaned[1:-1]
    return cleaned.replace('[', '').replace(']', '')


def _format_dax_scalar(value):
    if value is None:
        return 'BLANK()'
    if isinstance(value, bool):
        return 'TRUE()' if value else 'FALSE()'
    if isinstance(value, str):
        escaped = value.replace('"', '""')
        return f'"{escaped}"'
    return str(value)


def _normalize_filter_operator(operator: str | None) -> str:
    if not operator:
        return 'in'

    normalized = operator.strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'does_not_contains': 'not_contains',
        'does_not_contain': 'not_contains',
        'not_contains': 'not_contains',
        'contains': 'contains',
        'does_not_starts_with': 'not_starts_with',
        'does_not_start_with': 'not_starts_with',
        'not_starts_with': 'not_starts_with',
        'starts_with': 'starts_with',
        'in': 'in',
        'not_in': 'not_in',
        'is': 'is',
        'is_not': 'is_not',
        'is_blank': 'is_blank',
        'is_not_blank': 'is_not_blank',
    }
    return aliases.get(normalized, normalized)


def _build_filter_clause(column_ref: str, operator: str, values: list) -> str:
    def _ensure_values(op_name: str):
        if not values:
            raise ValueError(f'Filter operator "{op_name}" requires at least one value')

    def _scalar(value):
        return _format_dax_scalar(value)

    normalized_operator = _normalize_filter_operator(operator)
    formatted_values = [_scalar(item) for item in values]
    values_set = ','.join(formatted_values)

    if normalized_operator == 'contains':
        _ensure_values('contains')
        return (
            f'KEEPFILTERS( FILTER( ALL( {column_ref} ), '
            f'SEARCH( {_scalar(values[0])}, {column_ref}, 1, 0 ) >= 1 ))'
        )

    if normalized_operator == 'not_contains':
        _ensure_values('not contains')
        return (
            f'KEEPFILTERS( FILTER( ALL( {column_ref} ), '
            f'NOT( SEARCH( {_scalar(values[0])}, {column_ref}, 1, 0 ) >= 1 )))'
        )

    if normalized_operator == 'starts_with':
        _ensure_values('starts with')
        return (
            f'KEEPFILTERS( FILTER( ALL( {column_ref} ), '
            f'SEARCH( {_scalar(values[0])}, {column_ref}, 1, 0 ) = 1 ))'
        )

    if normalized_operator == 'not_starts_with':
        _ensure_values('not starts with')
        return (
            f'KEEPFILTERS( FILTER( ALL( {column_ref} ), '
            f'NOT( SEARCH( {_scalar(values[0])}, {column_ref}, 1, 0 ) = 1 )))'
        )

    if normalized_operator == 'in':
        _ensure_values('in')
        return f'KEEPFILTERS( TREATAS( {{{values_set}}}, {column_ref} ))'

    if normalized_operator == 'not_in':
        _ensure_values('not in')
        return (
            f'KEEPFILTERS( FILTER( ALL( {column_ref} ), '
            f'NOT( {column_ref} IN {{{values_set}}} )))'
        )

    if normalized_operator == 'is':
        _ensure_values('is')
        return f'KEEPFILTERS( TREATAS( {{{_scalar(values[0])}}}, {column_ref} ))'

    if normalized_operator == 'is_not':
        _ensure_values('is not')
        return f'KEEPFILTERS( FILTER( ALL( {column_ref} ), {column_ref} <> {_scalar(values[0])} ))'

    if normalized_operator == 'is_blank':
        return f'KEEPFILTERS( FILTER( ALL( {column_ref} ), ISBLANK( {column_ref} )))'

    if normalized_operator == 'is_not_blank':
        return f'KEEPFILTERS( FILTER( ALL( {column_ref} ), NOT( ISBLANK( {column_ref} ))))'

    raise ValueError(f'Unsupported filter operator: {operator}')


def dax_query_builder(rows: list, columns: list, values_measures: list, filters: list) -> dict:
    """
    Constrói uma query DAX (SUMMARIZECOLUMNS) dinâmica para tabelas dinâmicas.

    :param rows: Lista de colunas para as Linhas (Ex: ['Product[Category]', 'Product[Subcategory]'])
    :param columns: Lista de colunas para as Colunas (Ex: ['Date[Calendar Year]'])
    :param values_measures: Lista de medidas para os Valores (Ex: ['[Total Sales]', '[Total Margin]'])
    :param filters: Lista de dicionários contendo os filtros.
                   Ex: [{'column': 'Customer[Country]', 'values': ['Brazil', 'Portugal']}]
    """

    rows = rows or []
    columns = columns or []
    values_measures = values_measures or []
    filters = filters or []

    normalized_rows = [_normalize_column_ref(item) for item in rows]
    normalized_columns = [_normalize_column_ref(item) for item in columns]
    normalized_measures = [_normalize_measure_ref(item) for item in values_measures]

    # 1. Processamento dos filtros
    filter_clauses = []
    for f in filters:
        col = f.get('column')
        vals = f.get('values', [])
        operator = f.get('operator', 'in')

        if col:
            normalized_column = _normalize_column_ref(col)
            filter_clauses.append(_build_filter_clause(normalized_column, operator, vals))

    # 2. Agrupamento (linhas + colunas)
    group_fields = normalized_rows + normalized_columns

    # 3. Medidas no formato "Alias", [Medida]
    measure_clauses = [
        f'"{_measure_alias(measure_ref)}", {measure_ref}'
        for measure_ref in normalized_measures
    ]

    if not group_fields and not normalized_measures:
        raise ValueError('At least one grouping column or measure is required')

    # 4. Montagem da query final
    # Caso apenas medidas: retorna tabela de uma linha para suportar cenários KPI/cards.
    if not group_fields and normalized_measures:
        row_pairs = ",\n    ".join(measure_clauses)
        query_parts = [
            'EVALUATE',
            'CALCULATETABLE(',
            '  ROW(',
            f'    {row_pairs}',
            '  )'
        ]

        if filter_clauses:
            query_parts[-1] += ','
            query_parts.append(f"  {',\\n  '.join(filter_clauses)}")

        query_parts.append(')')
        query_text = '\n'.join(query_parts)
    else:
        summarize_parts = []
        summarize_parts.extend(group_fields)
        summarize_parts.extend(filter_clauses)
        summarize_parts.extend(measure_clauses)
        summarize_body = ",\n    ".join(summarize_parts)
        query_text = f"""EVALUATE
SUMMARIZECOLUMNS(
    {summarize_body}
)"""

    return {
        "query_text": query_text.strip(),
        "rows": normalized_rows,
        "columns": normalized_columns,
        "values_measures": normalized_measures,
        "filters": filters
    }


load_env()
DB_PATH = Path(os.getenv('SQLITE_PATH', str(BASE_DIR / 'app.db')))
path_to_adomd_net = os.getenv('PATH_TO_ADOMD_NET', None)

if path_to_adomd_net:
    sys.path.append(path_to_adomd_net)


# setup basic auth
BASIC_AUTH_USER = os.getenv('BASIC_AUTH_USER')
BASIC_AUTH_PASSWORD = os.getenv('BASIC_AUTH_PASSWORD')


def ensure_sqlite_ready() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                language TEXT NOT NULL,
                query_text TEXT NOT NULL,
                pivot_config TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


ensure_sqlite_ready()


def get_connection_string():
    """
    Returns the connection string.
    """
    CONNECTION_STRING = os.getenv('CONNECTION_STRING')
    return CONNECTION_STRING


CONN_STR = get_connection_string()


def PyadomdToJSON(cursor):
    """
    Converts the results of a Pyadomd query to JSON.
    """

    try:
        from pyadomd._type_code import adomd_type_map
    except ImportError as e:
        print(
            f'Error: ADOMD dependencies not available in this environment. {e}')
        return {'status': 'Failed. Environment did not work correctly'}

    columns = [desc[0] for desc in cursor.description]
    results = []
    dataset = cursor.fetchall()

    for row in dataset:
        row_dict = {}
        for i, value in enumerate(row):
            column_name = columns[i]
            row_dict[column_name] = value
        results.append(row_dict)

    # check ValueError: Out of range float values are not JSON compliant: nan
    for row in results:
        for key, value in row.items():
            if isinstance(value, float) and (value != value or value == float('inf') or value == float('-inf')):
                row[key] = None

    return results


def process_olap_report(parameters: dict):
    return run_olap_query(parameters.get('query_text', ''))


def run_olap_query(query_text: str):
    from pyadomd import Pyadomd

    conn_str = get_connection_string()
    if not conn_str:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='CONNECTION_STRING is not configured'
        )

    with Pyadomd(conn_str) as conn:
        with conn.cursor().execute(query_text) as cursor:
            # convert to json
            data_json = PyadomdToJSON(cursor)

    return {'status': 'OLAP report generated successfully',
            'query_text': query_text, 'data': data_json}


def enforce_readonly_query(language: str, query_text: str) -> None:
    normalized = query_text.upper()
    for keyword in READONLY_DENYLIST:
        if re.search(rf'\b{keyword}\b', normalized):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Read-only mode: keyword not allowed ({keyword})'
            )

    if language == 'xmla':
        compact = normalized.replace(' ', '')
        for token in XMLA_WRITE_TOKENS:
            if token in compact:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='Read-only mode: XMLA write operations are blocked'
                )


def get_current_username(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    if not BASIC_AUTH_USER or not BASIC_AUTH_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='BASIC_AUTH_USER and BASIC_AUTH_PASSWORD must be configured'
        )

    current_username_bytes = credentials.username.encode('utf8')
    correct_username_bytes = BASIC_AUTH_USER.encode('utf8')
    is_correct_username = secrets.compare_digest(
        current_username_bytes,
        correct_username_bytes
    )
    current_password_bytes = credentials.password.encode('utf8')
    correct_password_bytes = BASIC_AUTH_PASSWORD.encode('utf8')
    is_correct_password = secrets.compare_digest(
        current_password_bytes, correct_password_bytes
    )
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Incorrect username or password',
            headers={'WWW-Authenticate': 'Basic'},
        )
    return credentials.username


class OLAPReportRequest(BaseModel):
    query_text: str


class QueryExecutionRequest(BaseModel):
    language: str = Field(min_length=1)
    query_text: str = Field(min_length=1)


class DaxFilterItem(BaseModel):
    column: str = Field(min_length=1)
    operator: str = 'in'
    values: list = Field(default_factory=list)


class DaxBuildRequest(BaseModel):
    rows: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    values_measures: list[str] = Field(default_factory=list)
    filters: list[DaxFilterItem] = Field(default_factory=list)


class SaveQueryRequest(BaseModel):
    name: str = Field(min_length=1)
    language: str = Field(min_length=1)
    query_text: str = Field(min_length=1)
    pivot_config: dict | None = None


class UpdateQueryRequest(BaseModel):
    name: str | None = None
    language: str | None = None
    query_text: str | None = None
    pivot_config: dict | None = None


def validate_query_language(language: str) -> str:
    normalized = language.lower().strip()
    if normalized not in ALLOWED_QUERY_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='language must be one of: xmla, dax, mdx'
        )
    return normalized


def row_to_saved_query(row: sqlite3.Row) -> dict:
    pivot_config = row['pivot_config']
    parsed_config = None
    if pivot_config:
        try:
            parsed_config = json.loads(pivot_config)
        except json.JSONDecodeError:
            parsed_config = None

    return {
        'id': row['id'],
        'name': row['name'],
        'language': row['language'],
        'query_text': row['query_text'],
        'pivot_config': parsed_config,
        'created_by': row['created_by'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at']
    }


DMV_QUERY_MAP = {
    'tables': 'SELECT * FROM $SYSTEM.TMSCHEMA_TABLES',
    'columns': 'SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS',
    'measures': 'SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES',
    'relationships': 'SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS'
}

DISCOVER_QUERY_MAP = {
    'catalogs': 'SELECT * FROM $SYSTEM.DBSCHEMA_CATALOGS',
    'models': 'SELECT * FROM $SYSTEM.TMSCHEMA_MODEL',
    'partitions': 'SELECT * FROM $SYSTEM.TMSCHEMA_PARTITIONS',
    'perspectives': 'SELECT * FROM $SYSTEM.TMSCHEMA_PERSPECTIVES'
}


def safe_run_metadata_query(query_text: str) -> dict:
    try:
        result = run_olap_query(query_text)
        return {'ok': True, 'data': result.get('data', [])}
    except Exception as exc:
        return {'ok': False, 'error': str(exc), 'data': []}


def first_present_key(row: dict, keys: list[str]):
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def normalize_identifier(value):
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.endswith('.0'):
        text = text[:-2]

    return text


def parse_table_name_from_full_name(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Typical DAX full name: 'Table Name'[Column]
    if text.startswith("'") and "'" in text[1:]:
        parts = text.split("'", 2)
        if len(parts) > 1 and parts[1]:
            return parts[1]

    # Fallback format: Table[Column]
    if '[' in text:
        return text.split('[', 1)[0].strip().strip("'")

    return None


def find_table_name_in_row(row: dict, known_table_names: set[str]):
    for key, value in row.items():
        if value is None:
            continue

        key_text = str(key).upper()
        value_text = str(value).strip()
        if not value_text:
            continue

        # Explicit table-like keys from DMV payloads.
        if 'TABLE' in key_text and value_text in known_table_names:
            return value_text

        parsed = parse_table_name_from_full_name(value_text)
        if parsed and parsed in known_table_names:
            return parsed

    return None


def normalize_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y'}
    return False


def build_model_hierarchy() -> dict:
    tables_result = safe_run_metadata_query(DMV_QUERY_MAP['tables'])
    columns_result = safe_run_metadata_query(DMV_QUERY_MAP['columns'])
    measures_result = safe_run_metadata_query(DMV_QUERY_MAP['measures'])
    relationships_result = safe_run_metadata_query(DMV_QUERY_MAP['relationships'])

    if not tables_result.get('ok'):
        return {
            'status': 'failed',
            'detail': tables_result.get('error', 'Could not load tables metadata'),
            'data': {'tables': [], 'relationships': []}
        }

    tables_data = tables_result.get('data', [])
    columns_data = columns_result.get('data', []) if columns_result.get('ok') else []
    measures_data = measures_result.get('data', []) if measures_result.get('ok') else []
    relationships_data = relationships_result.get('data', []) if relationships_result.get('ok') else []

    table_index_by_id = {}
    hierarchy_tables = []

    for row in tables_data:
        table_id = first_present_key(row, ['ID', 'TableID', 'TABLE_ID', 'TableId'])
        table_name = first_present_key(row, ['Name', 'NAME', 'TableName', 'TABLE_NAME'])
        if table_name is None:
            continue

        normalized_table_id = normalize_identifier(table_id)

        table_entry = {
            'table_id': normalized_table_id,
            'table_name': str(table_name),
            'columns': [],
            'measures': []
        }
        hierarchy_tables.append(table_entry)
        if normalized_table_id is not None:
            table_index_by_id[normalized_table_id] = table_entry

    table_index_by_name = {item['table_name']: item for item in hierarchy_tables}
    known_table_names = set(table_index_by_name.keys())

    orphan_columns = 0
    orphan_measures = 0

    def resolve_table_reference(row: dict):
        table_id = first_present_key(
            row,
            ['TableID', 'TABLE_ID', 'TableId', 'ParentTableID', 'PARENT_TABLE_ID']
        )
        table_name = first_present_key(
            row,
            ['TableName', 'TABLE_NAME', 'ParentTableName', 'PARENT_TABLE_NAME']
        )
        full_name = first_present_key(
            row,
            ['DaxObjectFullName', 'DAX_OBJECT_FULL_NAME', 'FullName', 'FULL_NAME']
        )

        normalized_table_id = normalize_identifier(table_id)
        if normalized_table_id is not None:
            table_ref = table_index_by_id.get(normalized_table_id)
            if table_ref:
                return table_ref

        if table_name is not None:
            table_ref = table_index_by_name.get(str(table_name))
            if table_ref:
                return table_ref

        parsed_name = parse_table_name_from_full_name(full_name)
        if parsed_name is not None:
            table_ref = table_index_by_name.get(parsed_name)
            if table_ref:
                return table_ref

        scanned_name = find_table_name_in_row(row, known_table_names)
        if scanned_name is not None:
            table_ref = table_index_by_name.get(scanned_name)
            if table_ref:
                return table_ref

        return None

    for row in columns_data:
        column_name = first_present_key(row, ['ExplicitName', 'SourceColumn'])
        is_hidden = normalize_bool(first_present_key(row, ['IsHidden']))

        if column_name is None:
            continue

        table_ref = resolve_table_reference(row)
        if not table_ref:
            orphan_columns += 1
            continue

        table_ref['columns'].append({
            'name': str(column_name),
            'hidden': is_hidden
        })

    for row in measures_data:
        measure_name = first_present_key(row, ['Name', 'NAME', 'MeasureName', 'MEASURE_NAME'])
        expression = first_present_key(row, ['Expression', 'EXPRESSION'])
        is_hidden = normalize_bool(first_present_key(row, ['IsHidden', 'ISHIDDEN', 'HIDDEN']))

        if measure_name is None:
            continue

        table_ref = resolve_table_reference(row)
        if not table_ref:
            orphan_measures += 1
            continue

        table_ref['measures'].append({
            'name': str(measure_name),
            'expression': str(expression) if expression is not None else None,
            'hidden': is_hidden
        })

    for table in hierarchy_tables:
        table['columns'].sort(key=lambda item: item['name'])
        table['measures'].sort(key=lambda item: item['name'])

    hierarchy_tables.sort(key=lambda item: item['table_name'])

    relationships = []
    for row in relationships_data:
        from_table = first_present_key(row, ['FromTableName', 'FROM_TABLE_NAME'])
        from_column = first_present_key(row, ['FromColumnName', 'FROM_COLUMN_NAME'])
        to_table = first_present_key(row, ['ToTableName', 'TO_TABLE_NAME'])
        to_column = first_present_key(row, ['ToColumnName', 'TO_COLUMN_NAME'])
        relationships.append({
            'from': f"{from_table}[{from_column}]" if from_table and from_column else None,
            'to': f"{to_table}[{to_column}]" if to_table and to_column else None
        })

    return {
        'status': 'ok',
        'data': {
            'tables': hierarchy_tables,
            'relationships': relationships,
            'source_status': {
                'tables': tables_result.get('ok', False),
                'columns': columns_result.get('ok', False),
                'measures': measures_result.get('ok', False),
                'relationships': relationships_result.get('ok', False)
            },
            'unmapped': {
                'columns': orphan_columns,
                'measures': orphan_measures
            }
        }
    }


if STATIC_DIR.exists():
    app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/', tags=['Pivot UI'])
async def get_pivot_ui(username: Annotated[str, Depends(get_current_username)]):
    index_file = STATIC_DIR / 'index.html'
    if not index_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Pivot UI is not available. Missing static/index.html'
        )
    return FileResponse(index_file)


@app.post('/api/query', tags=['OLAP Query'])
async def execute_query(
    request: QueryExecutionRequest,
    username: Annotated[str, Depends(get_current_username)]
):
    language = validate_query_language(request.language)
    enforce_readonly_query(language, request.query_text)
    result = run_olap_query(request.query_text)
    result['language'] = language
    result['requested_by'] = username
    return result


@app.post('/api/dax/build', tags=['DAX Builder'])
async def build_dax_query(
    request: DaxBuildRequest,
    username: Annotated[str, Depends(get_current_username)]
):
    try:
        payload = dax_query_builder(
            rows=request.rows,
            columns=request.columns,
            values_measures=request.values_measures,
            filters=[item.model_dump() for item in request.filters]
        )
        return {'status': 'ok', **payload}
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )


@app.get('/api/metadata/dmvs', tags=['Metadata'])
async def get_dmv_metadata(username: Annotated[str, Depends(get_current_username)]):
    data = {}
    for key, query in DMV_QUERY_MAP.items():
        data[key] = safe_run_metadata_query(query)
    return {'status': 'ok', 'data': data}


@app.get('/api/metadata/discover', tags=['Metadata'])
async def get_discovery_metadata(username: Annotated[str, Depends(get_current_username)]):
    data = {}
    for key, query in DISCOVER_QUERY_MAP.items():
        data[key] = safe_run_metadata_query(query)
    return {'status': 'ok', 'data': data}


@app.get('/api/metadata/hierarchy', tags=['Metadata'])
async def get_hierarchy_metadata(username: Annotated[str, Depends(get_current_username)]):
    return build_model_hierarchy()


@app.get('/api/saved', tags=['Saved Queries'])
async def list_saved_queries(username: Annotated[str, Depends(get_current_username)]):
    with get_db_connection() as conn:
        rows = conn.execute(
            'SELECT * FROM saved_queries ORDER BY updated_at DESC, id DESC'
        ).fetchall()
    return {'status': 'ok', 'items': [row_to_saved_query(row) for row in rows]}


@app.post('/api/saved', tags=['Saved Queries'])
async def create_saved_query(
    request: SaveQueryRequest,
    username: Annotated[str, Depends(get_current_username)]
):
    language = validate_query_language(request.language)
    enforce_readonly_query(language, request.query_text)

    now = datetime.now(timezone.utc).isoformat()
    pivot_config = json.dumps(request.pivot_config) if request.pivot_config else None

    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO saved_queries (
                name, language, query_text, pivot_config,
                created_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (request.name.strip(), language, request.query_text, pivot_config,
             username, now, now)
        )
        conn.commit()
        new_id = cursor.lastrowid
        row = conn.execute(
            'SELECT * FROM saved_queries WHERE id = ?', (new_id,)
        ).fetchone()

    return {'status': 'ok', 'item': row_to_saved_query(row)}


@app.put('/api/saved/{query_id}', tags=['Saved Queries'])
async def update_saved_query(
    query_id: int,
    request: UpdateQueryRequest,
    username: Annotated[str, Depends(get_current_username)]
):
    with get_db_connection() as conn:
        existing = conn.execute(
            'SELECT * FROM saved_queries WHERE id = ?', (query_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Saved query not found'
            )

        updated_name = request.name.strip() if request.name is not None else existing['name']
        updated_language = request.language if request.language is not None else existing['language']
        updated_query = request.query_text if request.query_text is not None else existing['query_text']

        language = validate_query_language(updated_language)
        enforce_readonly_query(language, updated_query)

        if request.pivot_config is None:
            pivot_config_json = existing['pivot_config']
        else:
            pivot_config_json = json.dumps(request.pivot_config)

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE saved_queries
            SET name = ?, language = ?, query_text = ?, pivot_config = ?,
                updated_at = ?, created_by = ?
            WHERE id = ?
            """,
            (
                updated_name,
                language,
                updated_query,
                pivot_config_json,
                now,
                username,
                query_id
            )
        )
        conn.commit()
        row = conn.execute(
            'SELECT * FROM saved_queries WHERE id = ?', (query_id,)
        ).fetchone()

    return {'status': 'ok', 'item': row_to_saved_query(row)}


@app.delete('/api/saved/{query_id}', tags=['Saved Queries'])
async def delete_saved_query(
    query_id: int,
    username: Annotated[str, Depends(get_current_username)]
):
    with get_db_connection() as conn:
        existing = conn.execute(
            'SELECT id FROM saved_queries WHERE id = ?', (query_id,)
        ).fetchone()
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Saved query not found'
            )
        conn.execute('DELETE FROM saved_queries WHERE id = ?', (query_id,))
        conn.commit()
    return {'status': 'ok', 'deleted_id': query_id}


@app.post('/generate_olap_report', tags=['OLAP Report'])
async def generate_olap_report(
        parameters: OLAPReportRequest, username: Annotated[str, Depends(get_current_username)]):
    """
    Endpoint to generate an OLAP report based on the provided parameters.
    """

    try:
        result = process_olap_report(parameters.dict())
        return result
    except Exception as e:
        return {'status': 'Failed to generate OLAP report', 'error': str(e)}

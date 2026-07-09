import sys
import os
import secrets
from typing import Annotated
from pydantic import BaseModel
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials


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


def load_env():
    with open('.env') as f:
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


load_env()
path_to_adomd_net = os.getenv('PATH_TO_ADOMD_NET', None)

if path_to_adomd_net:
    sys.path.append(path_to_adomd_net)


# setup basic auth
BASIC_AUTH_USER = os.getenv('BASIC_AUTH_USER')
BASIC_AUTH_PASSWORD = os.getenv('BASIC_AUTH_PASSWORD')


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

    return results


def process_olap_report(parameters: dict):
    from pyadomd import Pyadomd

    query_text = parameters.get('query_text', '')
    CONN_STR = get_connection_string()

    with Pyadomd(CONN_STR) as conn:
        with conn.cursor().execute(query_text) as cursor:
            # convert to json
            data_json = PyadomdToJSON(cursor)

    return {'status': 'OLAP report generated successfully',
            'parameters': parameters, 'data': data_json}


def get_current_username(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
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

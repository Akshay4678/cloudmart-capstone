import os
import json
import hashlib
import secrets
import re

import boto3
import pymysql


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_HOST = os.environ["DB_HOST"]

DB_NAME = os.environ.get(
    "DB_NAME",
    "cloudmart"
)

DB_USER = os.environ["DB_USER"]

DB_PASSWORD_PARAMETER = os.environ["DB_PASSWORD_PARAMETER"]

DB_PORT = int(
    os.environ.get(
        "DB_PORT",
        "3306"
    )
)

ssm_client = boto3.client("ssm")


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():

    parameter = ssm_client.get_parameter(
        Name=DB_PASSWORD_PARAMETER,
        WithDecryption=True
    )

    db_password = parameter["Parameter"]["Value"]

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=db_password,
        database=DB_NAME,
        port=DB_PORT,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=10,
        write_timeout=10,
        autocommit=False
    )


# ============================================================
# TOKEN GENERATION
# ============================================================

def generate_customer_token(email, phone):

    email = str(email).strip().lower()
    phone = str(phone).strip()

    if not phone.isdigit():
        raise ValueError(
            "Phone number must contain only digits"
        )

    if len(phone) < 5:
        raise ValueError(
            "Phone number must contain at least five digits"
        )

    last_five_digits = phone[-5:]

    random_value = secrets.token_urlsafe(32)

    return (
        f"{email}"
        f"{last_five_digits}"
        f"{random_value}"
    )


# ============================================================
# TOKEN HASHING
# ============================================================

def hash_token(token):

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


# ============================================================
# EMAIL VALIDATION
# ============================================================

def validate_email(email):

    if email is None:
        raise ValueError(
            "email is required"
        )

    email = str(email).strip().lower()

    if not email:
        raise ValueError(
            "email is required"
        )

    if len(email) > 255:
        raise ValueError(
            "email must not exceed 255 characters"
        )

    email_pattern = (
        r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
        r"@"
        r"[A-Za-z0-9]"
        r"(?:[A-Za-z0-9-]{0,61}"
        r"[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9]"
        r"(?:[A-Za-z0-9-]{0,61}"
        r"[A-Za-z0-9])?)+$"
    )

    if not re.match(
        email_pattern,
        email
    ):
        raise ValueError(
            "email must be a valid email address"
        )

    return email


# ============================================================
# PHONE VALIDATION
# ============================================================

def validate_phone(phone):

    if phone is None:
        raise ValueError(
            "phone is required"
        )

    phone = str(phone).strip()

    if not phone:
        raise ValueError(
            "phone is required"
        )

    if not phone.isdigit():
        raise ValueError(
            "Phone number must contain only digits"
        )

    if len(phone) < 5:
        raise ValueError(
            "Phone number must contain at least five digits"
        )

    if len(phone) > 20:
        raise ValueError(
            "phone must not exceed 20 digits"
        )

    return phone


# ============================================================
# NAME VALIDATION
# ============================================================

def validate_name(name):

    if name is None:
        raise ValueError(
            "name is required"
        )

    name = str(name).strip()

    if not name:
        raise ValueError(
            "name is required"
        )

    if len(name) > 100:
        raise ValueError(
            "name must not exceed 100 characters"
        )

    return name


# ============================================================
# RESPONSE
# ============================================================

def response(status_code, body):

    return {
        "statusCode": status_code,

        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": (
                "Content-Type,Authorization"
            ),
            "Access-Control-Allow-Methods": (
                "GET,POST,PUT,OPTIONS"
            )
        },

        "body": json.dumps(
            body,
            default=str
        )
    }


# ============================================================
# REQUEST BODY
# ============================================================

def parse_request_body(event):

    if not isinstance(event, dict):
        raise ValueError(
            "Invalid Lambda event"
        )

    body = event.get("body")

    if body is None or body == "":
        return {}

    if isinstance(body, str):

        try:
            body = json.loads(body)

        except json.JSONDecodeError:
            raise ValueError(
                "Request body must contain valid JSON"
            )

    if not isinstance(body, dict):
        raise ValueError(
            "Request body must be a JSON object"
        )

    return body


# ============================================================
# CUSTOMER ID
# ============================================================

def generate_customer_id():

    return (
        f"CUST"
        f"{secrets.randbelow(900000) + 100000}"
    )


# ============================================================
# GET AUTHENTICATED CUSTOMER
# ============================================================

def get_authenticated_customer(event):

    request_context = event.get(
        "requestContext",
        {}
    )

    authorizer = request_context.get(
        "authorizer",
        {}
    )

    return (
        authorizer.get("customer_id"),
        authorizer.get("role")
    )


# ============================================================
# CREATE CUSTOMER
# ============================================================

def create_customer(body):

    name = validate_name(
        body.get("name")
    )

    email = validate_email(
        body.get("email")
    )

    phone = validate_phone(
        body.get("phone")
    )

    # IMPORTANT:
    # Customers are ALWAYS created as USER.
    # The client cannot provide role.
    role = "USER"

    token = generate_customer_token(
        email,
        phone
    )

    token_hash = hash_token(
        token
    )

    connection = get_connection()

    try:

        customer_id = generate_customer_id()

        with connection.cursor() as cursor:

            sql = """
                INSERT INTO customers
                (
                    customer_id,
                    name,
                    email,
                    phone,
                    auth_token_hash,
                    role
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
            """

            cursor.execute(
                sql,
                (
                    customer_id,
                    name,
                    email,
                    phone,
                    token_hash,
                    role
                )
            )

        connection.commit()

        return response(
            201,
            {
                "message": (
                    "Customer created successfully"
                ),
                "customer_id": customer_id,
                "name": name,
                "email": email,
                "phone": phone,
                "role": role,

                # Token is returned only once.
                "token": token
            }
        )

    except pymysql.err.IntegrityError as exc:

        connection.rollback()

        error_code = (
            exc.args[0]
            if exc.args
            else None
        )

        if error_code == 1062:

            return response(
                409,
                {
                    "message": (
                        "A customer with this "
                        "email already exists"
                    )
                }
            )

        return response(
            500,
            {
                "message": (
                    "Customer creation failed"
                ),
                "error": str(exc)
            }
        )

    except pymysql.MySQLError as exc:

        connection.rollback()

        return response(
            500,
            {
                "message": (
                    "Database operation failed"
                ),
                "error": str(exc)
            }
        )

    finally:

        connection.close()


# ============================================================
# GET CUSTOMER
# ============================================================

def get_customer(event):

    path_parameters = event.get(
        "pathParameters"
    ) or {}

    requested_customer_id = (
        path_parameters.get("customerId")
    )

    if not requested_customer_id:

        return response(
            400,
            {
                "message": (
                    "customerId is required"
                )
            }
        )

    authenticated_customer_id, role = (
        get_authenticated_customer(event)
    )

    role = (
        role or ""
    ).upper()

    # USER can only read their own record.
    if (
        role == "USER"
        and authenticated_customer_id
        != requested_customer_id
    ):

        return response(
            403,
            {
                "message": (
                    "You can only access "
                    "your own customer details"
                )
            }
        )

    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    customer_id,
                    name,
                    email,
                    phone,
                    role
                FROM customers
                WHERE customer_id = %s
                LIMIT 1
                """,
                (
                    requested_customer_id,
                )
            )

            customer = cursor.fetchone()

        if not customer:

            return response(
                404,
                {
                    "message": (
                        "Customer not found"
                    )
                }
            )

        return response(
            200,
            {
                "customer": customer
            }
        )

    finally:

        connection.close()


# ============================================================
# UPDATE CUSTOMER
# ============================================================

def update_customer(event):

    path_parameters = event.get(
        "pathParameters"
    ) or {}

    requested_customer_id = (
        path_parameters.get("customerId")
    )

    if not requested_customer_id:

        return response(
            400,
            {
                "message": (
                    "customerId is required"
                )
            }
        )

    authenticated_customer_id, role = (
        get_authenticated_customer(event)
    )

    role = (
        role or ""
    ).upper()

    # USER can only update their own record.
    if (
        role == "USER"
        and authenticated_customer_id
        != requested_customer_id
    ):

        return response(
            403,
            {
                "message": (
                    "You can only update "
                    "your own customer details"
                )
            }
        )

    body = parse_request_body(event)

    if not body:

        return response(
            400,
            {
                "message": (
                    "Request body is required"
                )
            }
        )

    connection = get_connection()

    try:

        # ----------------------------------------------------
        # GET CURRENT CUSTOMER
        # ----------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    customer_id,
                    name,
                    email,
                    phone,
                    role
                FROM customers
                WHERE customer_id = %s
                LIMIT 1
                """,
                (
                    requested_customer_id,
                )
            )

            current_customer = cursor.fetchone()

        if not current_customer:

            return response(
                404,
                {
                    "message": (
                        "Customer not found"
                    )
                }
            )

        # ----------------------------------------------------
        # ONLY THESE FIELDS CAN BE UPDATED
        # ----------------------------------------------------

        name = validate_name(
            body.get(
                "name",
                current_customer["name"]
            )
        )

        email = validate_email(
            body.get(
                "email",
                current_customer["email"]
            )
        )

        phone = validate_phone(
            body.get(
                "phone",
                current_customer["phone"]
            )
        )

        # role is NEVER taken from request.
        # auth_token_hash is NEVER updated here.

        with connection.cursor() as cursor:

            cursor.execute(
                """
                UPDATE customers
                SET
                    name = %s,
                    email = %s,
                    phone = %s
                WHERE customer_id = %s
                """,
                (
                    name,
                    email,
                    phone,
                    requested_customer_id
                )
            )

        connection.commit()

        return response(
            200,
            {
                "message": (
                    "Customer updated successfully"
                ),
                "customer_id": (
                    requested_customer_id
                ),
                "name": name,
                "email": email,
                "phone": phone,
                "role": current_customer["role"]
            }
        )

    except pymysql.err.IntegrityError as exc:

        connection.rollback()

        error_code = (
            exc.args[0]
            if exc.args
            else None
        )

        if error_code == 1062:

            return response(
                409,
                {
                    "message": (
                        "A customer with this "
                        "email already exists"
                    )
                }
            )

        return response(
            500,
            {
                "message": (
                    "Customer update failed"
                ),
                "error": str(exc)
            }
        )

    except pymysql.MySQLError as exc:

        connection.rollback()

        return response(
            500,
            {
                "message": (
                    "Database operation failed"
                ),
                "error": str(exc)
            }
        )

    finally:

        connection.close()


# ============================================================
# LAMBDA HANDLER
# ============================================================

def lambda_handler(event, context):

    try:

        method = (
            event.get("httpMethod")
            or event.get(
                "requestContext",
                {}
            )
            .get(
                "http",
                {}
            )
            .get("method")
            or ""
        ).upper()

        # ----------------------------------------------------
        # OPTIONS
        # ----------------------------------------------------

        if method == "OPTIONS":

            return response(
                204,
                {}
            )

        # ----------------------------------------------------
        # CREATE
        # ----------------------------------------------------

        if method == "POST":

            body = parse_request_body(
                event
            )

            return create_customer(
                body
            )

        # ----------------------------------------------------
        # READ
        # ----------------------------------------------------

        if method == "GET":

            return get_customer(
                event
            )

        # ----------------------------------------------------
        # UPDATE
        # ----------------------------------------------------

        if method == "PUT":

            return update_customer(
                event
            )

        # ----------------------------------------------------
        # DELETE IS INTENTIONALLY NOT IMPLEMENTED
        # ----------------------------------------------------

        return response(
            405,
            {
                "message": (
                    "Method not allowed"
                )
            }
        )

    except ValueError as exc:

        return response(
            400,
            {
                "message": str(exc)
            }
        )

    except pymysql.MySQLError as exc:

        return response(
            500,
            {
                "message": (
                    "Database operation failed"
                ),
                "error": str(exc)
            }
        )

    except Exception as exc:

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
                "error": str(exc)
            }
        )
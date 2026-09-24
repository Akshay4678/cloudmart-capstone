import os
import json
import hashlib
import secrets
import re

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

DB_PASSWORD = os.environ["DB_PASSWORD"]

DB_PORT = int(
    os.environ.get(
        "DB_PORT",
        "3306"
    )
)


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
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

def generate_customer_token(
    email,
    phone
):
    """
    Generates a customer token using:

    email + last five digits of phone + random value

    The original token is returned to the caller only once.
    Only its SHA-256 hash is stored in MySQL.
    """

    email = str(
        email
    ).strip().lower()

    phone = str(
        phone
    ).strip()

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

    token = (
        f"{email}"
        f"{last_five_digits}"
        f"{random_value}"
    )

    return token


# ============================================================
# TOKEN HASHING
# ============================================================

def hash_token(token):

    """
    Stores only the SHA-256 hash in the database.
    """

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

    email = str(
        email
    ).strip().lower()

    if not email:

        raise ValueError(
            "email is required"
        )

    if len(email) > 255:

        raise ValueError(
            "email must not exceed 255 characters"
        )

    # Basic email validation.
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

    phone = str(
        phone
    ).strip()

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

    name = str(
        name
    ).strip()

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
# ROLE VALIDATION
# ============================================================

def validate_role(role):

    if role is None:

        role = "USER"

    role = str(
        role
    ).strip().upper()

    if not role:

        role = "USER"

    if role not in (
        "USER",
        "ADMIN"
    ):

        raise ValueError(
            "role must be either USER or ADMIN"
        )

    return role


# ============================================================
# RESPONSE
# ============================================================

def response(
    status_code,
    body
):

    return {
        "statusCode": status_code,

        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": (
                "Content-Type,Authorization"
            ),
            "Access-Control-Allow-Methods": (
                "POST,OPTIONS"
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

    if not isinstance(
        event,
        dict
    ):

        raise ValueError(
            "Invalid Lambda event"
        )

    body = event.get(
        "body"
    )

    if body is None or body == "":

        return {}

    if isinstance(
        body,
        str
    ):

        try:

            body = json.loads(
                body
            )

        except json.JSONDecodeError:

            raise ValueError(
                "Request body must contain valid JSON"
            )

    if not isinstance(
        body,
        dict
    ):

        raise ValueError(
            "Request body must be a JSON object"
        )

    return body


# ============================================================
# GENERATE CUSTOMER ID
# ============================================================

def generate_customer_id():

    return (
        f"CUST"
        f"{secrets.randbelow(900000) + 100000}"
    )


# ============================================================
# CREATE CUSTOMER
# ============================================================

def create_customer(body):

    # --------------------------------------------------------
    # VALIDATE BODY
    # --------------------------------------------------------

    if not isinstance(
        body,
        dict
    ):

        raise ValueError(
            "Request body must be a JSON object"
        )


    # --------------------------------------------------------
    # READ INPUT
    # --------------------------------------------------------

    name = validate_name(
        body.get("name")
    )

    email = validate_email(
        body.get("email")
    )

    phone = validate_phone(
        body.get("phone")
    )

    role = validate_role(
        body.get(
            "role",
            "USER"
        )
    )


    # --------------------------------------------------------
    # GENERATE TOKEN
    # --------------------------------------------------------

    token = generate_customer_token(
        email,
        phone
    )


    # --------------------------------------------------------
    # HASH TOKEN
    # --------------------------------------------------------

    token_hash = hash_token(
        token
    )


    # --------------------------------------------------------
    # DATABASE CONNECTION
    # --------------------------------------------------------

    connection = get_connection()


    try:

        # ----------------------------------------------------
        # GENERATE CUSTOMER ID
        # ----------------------------------------------------

        customer_id = (
            generate_customer_id()
        )


        # ----------------------------------------------------
        # INSERT CUSTOMER
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # COMMIT
        # ----------------------------------------------------

        connection.commit()


        # ----------------------------------------------------
        # SUCCESS RESPONSE
        # ----------------------------------------------------

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

                # IMPORTANT:
                # Return the original token only once.
                # Never store this original token in MySQL.
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


        # ----------------------------------------------------
        # DUPLICATE CUSTOMER
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # OTHER DATABASE INTEGRITY ERROR
        # ----------------------------------------------------

        return response(
            500,
            {
                "message": (
                    "Customer creation failed"
                ),

                "error": str(
                    exc
                )
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

                "error": str(
                    exc
                )
            }
        )


    except Exception as exc:

        connection.rollback()

        return response(
            500,
            {
                "message": (
                    "Customer creation failed"
                ),

                "error": str(
                    exc
                )
            }
        )


    finally:

        connection.close()


# ============================================================
# LAMBDA HANDLER
# ============================================================

def lambda_handler(
    event,
    context
):

    try:

        # ----------------------------------------------------
        # GET HTTP METHOD
        # ----------------------------------------------------

        method = (
            event.get(
                "httpMethod"
            )

            or event.get(
                "requestContext",
                {}
            )
            .get(
                "http",
                {}
            )
            .get(
                "method"
            )

            or ""
        ).upper()


        # ----------------------------------------------------
        # CORS PREFLIGHT
        # ----------------------------------------------------

        if method == "OPTIONS":

            return response(
                204,
                {}
            )


        # ----------------------------------------------------
        # CREATE CUSTOMER
        # ----------------------------------------------------

        if method == "POST":

            body = parse_request_body(
                event
            )

            return create_customer(
                body
            )


        # ----------------------------------------------------
        # METHOD NOT ALLOWED
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
                "message": str(
                    exc
                )
            }
        )


    except json.JSONDecodeError:

        return response(
            400,
            {
                "message": (
                    "Request body must contain "
                    "valid JSON"
                )
            }
        )


    except pymysql.MySQLError as exc:

        return response(
            500,
            {
                "message": (
                    "Database operation failed"
                ),

                "error": str(
                    exc
                )
            }
        )


    except Exception as exc:

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),

                "error": str(
                    exc
                )
            }
        )
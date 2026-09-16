import os
import json
import hashlib
import secrets
import pymysql


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ.get("DB_NAME", "cloudmart")
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))


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
        autocommit=False
    )


# ============================================================
# TOKEN GENERATION
# ============================================================

def generate_customer_token(email, phone):
    """
    Generates a customer token using:

    email + last five digits of phone + random value
    """

    email = str(email).strip().lower()
    phone = str(phone).strip()

    if not phone.isdigit():
        raise ValueError("Phone number must contain only digits")

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


def hash_token(token):
    """
    Stores only the SHA-256 hash in the database.
    """

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


# ============================================================
# RESPONSE
# ============================================================

def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS"
        },
        "body": json.dumps(body, default=str)
    }


# ============================================================
# REQUEST BODY
# ============================================================

def parse_request_body(event):
    body = event.get("body")

    if not body:
        return {}

    if isinstance(body, str):
        return json.loads(body)

    return body


# ============================================================
# CREATE CUSTOMER
# ============================================================

def create_customer(body):
    name = body.get("name")
    email = body.get("email")
    phone = body.get("phone")
    role = body.get("role", "USER").strip().upper()

    if not name:
        raise ValueError("name is required")

    if not email:
        raise ValueError("email is required")

    if not phone:
        raise ValueError("phone is required")

    if role not in ("USER", "ADMIN"):
        raise ValueError(
            "role must be either USER or ADMIN"
        )

    name = str(name).strip()
    email = str(email).strip().lower()
    phone = str(phone).strip()

    token = generate_customer_token(
        email,
        phone
    )

    token_hash = hash_token(token) 

    customer_id = (
        f"CUST{secrets.randbelow(900000) + 100000}"
    )

    connection = get_connection()

    try:
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
                "message": "Customer created successfully",
                "customer_id": customer_id,
                "name": name,
                "email": email,
                "phone": phone,
                "role": role,

                # Return this only once.
                # Do not store this original token in MySQL.
                "token": token
            }
        )

    except pymysql.err.IntegrityError as exc:
        connection.rollback()

        error_code = exc.args[0] if exc.args else None

        if error_code == 1062:
            return response(
                409,
                {
                    "message": (
                        "A customer with this email "
                        "or token already exists"
                    )
                }
            )

        return response(
            500,
            {
                "message": "Customer creation failed",
                "error": str(exc)
            }
        )

    except Exception as exc:
        connection.rollback()

        return response(
            500,
            {
                "message": "Customer creation failed",
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
            or event.get("requestContext", {})
            .get("http", {})
            .get("method")
            or ""
        ).upper()

        if method == "OPTIONS":
            return response(204, {})

        if method == "POST":
            body = parse_request_body(event)

            return create_customer(body)

        return response(
            405,
            {
                "message": "Method not allowed"
            }
        )

    except json.JSONDecodeError:
        return response(
            400,
            {
                "message": "Request body must contain valid JSON"
            }
        )

    except ValueError as exc:
        return response(
            400,
            {
                "message": str(exc)
            }
        )

    except Exception as exc:
        return response(
            500,
            {
                "message": "Internal server error",
                "error": str(exc)
            }
        )
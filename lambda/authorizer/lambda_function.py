import os
import hashlib
import pymysql


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

APP_ENVIRONMENT = os.environ.get(
    "APP_ENVIRONMENT",
    "dev"
)


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=5,
        write_timeout=5
    )


# =========================================================
# TOKEN HELPERS
# =========================================================

def normalize_token(value):
    """
    Removes leading/trailing spaces and the Bearer prefix.
    """

    token = (value or "").strip()

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    return token


def hash_token(token):
    """
    Creates a SHA-256 hash of the supplied token.
    """

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


# =========================================================
# GET CUSTOMER USING HASHED TOKEN
# =========================================================

def get_customer_by_token(authorization):

    provided_token = normalize_token(
        authorization
    )

    if not provided_token:
        return None

    token_hash = hash_token(
        provided_token
    )

    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    customer_id,
                    role
                FROM customers
                WHERE auth_token_hash = %s
                LIMIT 1
                """,
                (token_hash,)
            )

            customer = cursor.fetchone()

            return customer

    finally:

        if connection:
            connection.close()


# =========================================================
# API ACCESS RULES
# =========================================================

def is_allowed(role, method, path):

    role = (role or "").upper()
    method = (method or "").upper()

    normalized_path = path.rstrip("/") or "/"


    # =====================================================
    # USER PERMISSIONS
    # =====================================================

    if role == "USER":

        # -------------------------------------------------
        # Products - read only
        # -------------------------------------------------

        if normalized_path == "/products":
            return method == "GET"

        if normalized_path.startswith("/products/"):
            return method == "GET"


        # -------------------------------------------------
        # Orders
        # -------------------------------------------------

        if normalized_path == "/orders":
            return method in {"GET", "POST"}

        if normalized_path.startswith("/orders/"):
            return method in {
                "GET",
                "PUT",
                "PATCH"
            }

        return False


    # =====================================================
    # ADMIN PERMISSIONS
    # =====================================================

    if role == "ADMIN":

        # -------------------------------------------------
        # Products
        # -------------------------------------------------

        if normalized_path == "/products":
            return method in {
                "GET",
                "POST"
            }

        if normalized_path.startswith("/products/"):
            return method in {
                "GET",
                "PUT",
                "PATCH",
                "DELETE"
            }


        # -------------------------------------------------
        # Orders
        # -------------------------------------------------

        if normalized_path == "/orders":
            return method == "GET"

        if normalized_path.startswith("/orders/"):
            return method in {
                "GET",
                "PUT",
                "PATCH"
            }

        return False


    # =====================================================
    # UNKNOWN ROLE
    # =====================================================

    return False


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(event, context):

    print("========== AUTHORIZER START ==========")

    try:

        # =================================================
        # GET AUTHORIZATION HEADER
        # =================================================

        authorization = event.get(
            "authorizationToken",
            ""
        )

        method_arn = event.get(
            "methodArn",
            ""
        )


        # =================================================
        # CHECK AUTHORIZATION HEADER
        # =================================================

        if not authorization:

            print("TOKEN MISSING")

            raise Exception("Unauthorized")


        # =================================================
        # FIND CUSTOMER USING TOKEN HASH
        # =================================================

        customer = get_customer_by_token(
            authorization
        )

        if not customer:

            print("TOKEN INVALID")

            raise Exception("Unauthorized")


        # =================================================
        # GET CUSTOMER DETAILS
        # =================================================

        customer_id = customer["customer_id"]

        role = (
            customer["role"] or ""
        ).upper()


        print(
            f"TOKEN VALID: "
            f"customer_id={customer_id}, "
            f"role={role}"
        )


        # =================================================
        # PARSE METHOD ARN
        # =================================================

        arn_parts = method_arn.split("/")

        if len(arn_parts) < 3:

            print("INVALID METHOD ARN")

            raise Exception("Unauthorized")


        method = arn_parts[2].upper()


        if len(arn_parts) > 3:

            path = "/" + "/".join(
                arn_parts[3:]
            )

        else:

            path = "/"


        # =================================================
        # CHECK ROLE PERMISSION
        # =================================================

        if not is_allowed(
            role,
            method,
            path
        ):

            print(
                f"ACCESS DENIED: "
                f"customer_id={customer_id}, "
                f"role={role}, "
                f"method={method}, "
                f"path={path}"
            )

            raise Exception("Unauthorized")


        # =================================================
        # AUTHORIZATION SUCCESS
        # =================================================

        print(
            f"AUTHORIZATION SUCCESS: "
            f"customer_id={customer_id}, "
            f"role={role}, "
            f"method={method}, "
            f"path={path}"
        )


        # =================================================
        # RETURN IAM POLICY
        # =================================================

        return {
            "principalId": (
                f"cloudmart-{customer_id}"
            ),

            "policyDocument": {
                "Version": "2012-10-17",

                "Statement": [
                    {
                        "Action": "execute-api:Invoke",

                        "Effect": "Allow",

                        "Resource": method_arn
                    }
                ]
            },

            "context": {
                "customer_id": str(
                    customer_id
                ),

                "role": role,

                "user": str(
                    customer_id
                ),

                "environment": APP_ENVIRONMENT
            }
        }


    except Exception as exc:

        print(
            "========== AUTHORIZER ERROR =========="
        )

        print(
            "Authorization failed:",
            type(exc).__name__,
            str(exc)
        )

        raise Exception("Unauthorized")
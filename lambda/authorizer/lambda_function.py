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

DB_PORT = int(
    os.environ.get(
        "DB_PORT",
        "3306"
    )
)

APP_ENVIRONMENT = os.environ.get(
    "APP_ENVIRONMENT",
    "dev"
)


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_connection():

    """
    Creates a connection to the RDS MySQL database.
    """

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

    Examples:

        akshaytoken123

        Bearer akshaytoken123

        bearer akshaytoken123
    """

    token = (
        value or ""
    ).strip()

    if token.lower().startswith(
        "bearer "
    ):

        token = token[7:].strip()

    return token


def hash_token(token):

    """
    Creates a SHA-256 hash of the supplied token.

    The database stores the hashed token in:

        auth_token_hash
    """

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


# =========================================================
# GET CUSTOMER USING HASHED TOKEN
# =========================================================

def get_customer_by_token(authorization):

    """
    Finds a customer using the SHA-256 hash of
    the supplied authorization token.

    Returns:

        {
            "customer_id": "CUST101",
            "role": "USER"
        }

    Returns None when the token is invalid.
    """

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

                (
                    token_hash,
                )
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

    """
    Checks whether the authenticated role can access
    the requested HTTP method and API path.

    USER permissions:

        GET    /products
        GET    /products/{productId}

        GET    /orders
        POST   /orders

        GET    /orders/{orderId}
        PUT    /orders/{orderId}

        PATCH is not allowed for users.

    ADMIN permissions:

        GET    /products
        POST   /products

        GET    /products/{productId}
        PUT    /products/{productId}
        PATCH  /products/{productId}
        DELETE /products/{productId}

        GET    /orders

        GET    /orders/{orderId}
        PUT    /orders/{orderId}
        PATCH  /orders/{orderId}
    """

    role = (
        role or ""
    ).upper()

    method = (
        method or ""
    ).upper()

    normalized_path = (
        path or "/"
    ).rstrip("/") or "/"


    # =====================================================
    # USER PERMISSIONS
    # =====================================================

    if role == "USER":


        # -------------------------------------------------
        # Products - read only
        # -------------------------------------------------

        if normalized_path == "/products":

            return method == "GET"


        if normalized_path.startswith(
            "/products/"
        ):

            return method == "GET"


        # -------------------------------------------------
        # Orders
        # -------------------------------------------------

        if normalized_path == "/orders":

            return method in {
                "GET",
                "POST"
            }


        if normalized_path.startswith(
            "/orders/"
        ):

            # Users can read their own orders.

            # Users can use the existing PUT endpoint
            # if the Order Lambda permits the operation.

            # PATCH is intentionally not allowed
            # for general users.

            return method in {
                "GET",
                "PUT"
            }


        # -------------------------------------------------
        # Unknown USER path
        # -------------------------------------------------

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


        if normalized_path.startswith(
            "/products/"
        ):

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


        if normalized_path.startswith(
            "/orders/"
        ):

            # Administrators can read, update and
            # partially update any order.

            return method in {
                "GET",
                "PUT",
                "PATCH"
            }


        # -------------------------------------------------
        # Unknown ADMIN path
        # -------------------------------------------------

        return False


    # =====================================================
    # UNKNOWN ROLE
    # =====================================================

    return False


# =========================================================
# PARSE METHOD ARN
# =========================================================

def parse_method_arn(method_arn):

    """
    Extracts the HTTP method and resource path
    from the API Gateway method ARN.

    Example ARN:

    arn:aws:execute-api:ap-south-1:123456789012:
    abcdef1234/dev/PATCH/products/1

    Returns:

        (
            "PATCH",
            "/products/1"
        )
    """

    if not method_arn:

        raise Exception(
            "Unauthorized"
        )

    arn_parts = method_arn.split(
        "/"
    )

    if len(arn_parts) < 3:

        raise Exception(
            "Unauthorized"
        )

    method = arn_parts[2].upper()

    if len(arn_parts) > 3:

        path = "/" + "/".join(
            arn_parts[3:]
        )

    else:

        path = "/"


    return method, path


# =========================================================
# CREATE ALLOW POLICY
# =========================================================

def create_allow_policy(
    principal_id,
    method_arn,
    customer_id,
    role
):

    """
    Creates the IAM policy returned to API Gateway
    when authorization succeeds.
    """

    return {

        "principalId": principal_id,

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

            "role": str(
                role
            ),

            "user": str(
                customer_id
            ),

            "environment": APP_ENVIRONMENT

        }

    }


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(event, context):

    """
    Main Lambda authorizer function.

    Flow:

        1. Read the authorization token.
        2. Read the API Gateway method ARN.
        3. Hash the supplied token.
        4. Find the customer in RDS MySQL.
        5. Read the customer's role.
        6. Check role-based permissions.
        7. Return an Allow policy.
        8. Otherwise raise Unauthorized.
    """

    print(
        "========== AUTHORIZER START =========="
    )

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

            print(
                "TOKEN MISSING"
            )

            raise Exception(
                "Unauthorized"
            )


        # =================================================
        # FIND CUSTOMER USING TOKEN HASH
        # =================================================

        customer = get_customer_by_token(
            authorization
        )

        if not customer:

            print(
                "TOKEN INVALID"
            )

            raise Exception(
                "Unauthorized"
            )


        # =================================================
        # GET CUSTOMER DETAILS
        # =================================================

        customer_id = customer.get(
            "customer_id"
        )

        role = (
            customer.get(
                "role"
            ) or ""
        ).upper()


        if not customer_id:

            print(
                "CUSTOMER ID MISSING"
            )

            raise Exception(
                "Unauthorized"
            )


        if role not in {
            "USER",
            "ADMIN"
        }:

            print(
                f"INVALID ROLE: {role}"
            )

            raise Exception(
                "Unauthorized"
            )


        print(
            f"TOKEN VALID: "
            f"customer_id={customer_id}, "
            f"role={role}"
        )


        # =================================================
        # PARSE METHOD ARN
        # =================================================

        method, path = parse_method_arn(
            method_arn
        )


        print(
            f"REQUEST: "
            f"method={method}, "
            f"path={path}"
        )


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

            raise Exception(
                "Unauthorized"
            )


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
        # CREATE PRINCIPAL ID
        # =================================================

        principal_id = (
            f"cloudmart-{customer_id}"
        )


        # =================================================
        # RETURN IAM POLICY
        # =================================================

        policy = create_allow_policy(

            principal_id=principal_id,

            method_arn=method_arn,

            customer_id=customer_id,

            role=role

        )


        return policy


    except Exception as exc:

        print(
            "========== AUTHORIZER ERROR =========="
        )

        print(
            "Authorization failed:",
            type(exc).__name__,
            str(exc)
        )

        raise Exception(
            "Unauthorized"
        )
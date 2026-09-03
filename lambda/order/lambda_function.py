import base64
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import pymysql


# ================================================================
# AWS CLIENTS
# ================================================================

sqs = boto3.client("sqs")
cloudwatch = boto3.client("cloudwatch")


# ================================================================
# ENVIRONMENT VARIABLES
# ================================================================

QUEUE_URL = os.environ["ORDER_QUEUE_URL"]

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")


# ================================================================
# FILE PATHS
# ================================================================

SCHEMA_FILE = os.path.join(
    os.path.dirname(__file__),
    "schema.sql",
)


# ================================================================
# DATABASE CONNECTION
# ================================================================

def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# ================================================================
# METRICS
# ================================================================

def put_metric(metric_name, value=1):
    try:
        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "Count",
                    "Dimensions": [
                        {
                            "Name": "Environment",
                            "Value": ENVIRONMENT,
                        },
                        {
                            "Name": "Service",
                            "Value": "Order",
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        print(f"Metric error: {exc}")


# ================================================================
# JSON SERIALIZATION
# ================================================================

def json_default(value):
    """
    Convert MySQL/Python values into JSON-safe values.
    """

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, (datetime,)):
        return value.isoformat()

    return str(value)


# ================================================================
# HTTP RESPONSE
# ================================================================

def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(
            body,
            default=json_default,
        ),
    }


# ================================================================
# ACTOR / PERFORMED BY
# ================================================================

def get_performed_by(event=None, body=None):
    """
    Determine who performed the operation.

    Priority:

    1. Cognito/JWT subject
    2. Legacy API Gateway authorizer subject
    3. performed_by supplied in request body
    4. customer_id from request body
    5. fallback service identity
    """

    event = event or {}
    body = body or {}

    # ------------------------------------------------------------
    # HTTP API JWT AUTHORIZE
    # ------------------------------------------------------------

    request_context = (
        event.get("requestContext")
        or {}
    )

    authorizer = (
        request_context.get("authorizer")
        or {}
    )

    jwt = (
        authorizer.get("jwt")
        or {}
    )

    claims = (
        jwt.get("claims")
        or {}
    )

    if claims.get("sub"):
        return str(claims["sub"])

    # ------------------------------------------------------------
    # REST API / LEGACY COGNITO AUTHORIZE
    # ------------------------------------------------------------

    legacy_claims = (
        authorizer.get("claims")
        or {}
    )

    if legacy_claims.get("sub"):
        return str(legacy_claims["sub"])

    # ------------------------------------------------------------
    # OPTIONAL REQUEST ACTOR
    # ------------------------------------------------------------

    if body.get("performed_by"):
        return str(
            body["performed_by"]
        ).strip()

    # ------------------------------------------------------------
    # CUSTOMER FALLBACK
    # ------------------------------------------------------------

    if body.get("customer_id"):
        return str(
            body["customer_id"]
        ).strip()

    # ------------------------------------------------------------
    # SERVICE FALLBACK
    # ------------------------------------------------------------

    return "order-api"


# ================================================================
# AUDIT LOG
# ================================================================

def write_audit_log(
    connection,
    entity_type,
    entity_id,
    action,
    old_value=None,
    new_value=None,
    performed_by=None,
):
    """
    Write an audit/history record.

    This function intentionally does NOT commit.
    The caller controls the transaction so that
    the business operation and audit entry commit
    together.
    """

    old_json = (
        json.dumps(
            old_value,
            default=json_default,
        )
        if old_value is not None
        else None
    )

    new_json = (
        json.dumps(
            new_value,
            default=json_default,
        )
        if new_value is not None
        else None
    )

    with connection.cursor() as cursor:

        cursor.execute(
            """
            INSERT INTO audit_logs
            (
                entity_type,
                entity_id,
                action,
                old_value,
                new_value,
                performed_by,
                created_at
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                entity_type,
                str(entity_id),
                action,
                old_json,
                new_json,
                performed_by,
                datetime.now(timezone.utc)
                .replace(tzinfo=None),
            ),
        )

    print(
        "Audit log written: "
        f"{entity_type} "
        f"{entity_id} "
        f"{action} "
        f"by {performed_by}"
    )


# ================================================================
# REQUEST BODY
# ================================================================

def parse_request_body(event):
    body = event.get("body")

    if body is None:
        return {}

    if isinstance(body, dict):
        return body

    if not isinstance(body, str):
        raise ValueError(
            "Request body must be JSON"
        )

    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(
                body
            ).decode("utf-8")
        except Exception as exc:
            raise ValueError(
                "Invalid base64 request body"
            ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Invalid JSON body"
        ) from exc


# ================================================================
# SQL STATEMENT SPLITTER
# ================================================================

def split_sql_statements(sql):
    """
    Split schema.sql into SQL statements.

    This handles semicolons inside quoted strings and ignores
    SQL comments beginning with --.
    """

    statements = []
    current = []

    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    escape_next = False

    lines = sql.splitlines()

    for line in lines:

        stripped = line.strip()

        # --------------------------------------------------------
        # SKIP FULL-LINE COMMENTS
        # --------------------------------------------------------

        if stripped.startswith("--"):
            continue

        i = 0

        while i < len(line):

            char = line[i]

            if escape_next:
                current.append(char)
                escape_next = False
                i += 1
                continue

            if char == "\\":
                current.append(char)
                escape_next = True
                i += 1
                continue

            if (
                char == "'"
                and not in_double_quote
                and not in_backtick
            ):
                in_single_quote = (
                    not in_single_quote
                )
                current.append(char)
                i += 1
                continue

            if (
                char == '"'
                and not in_single_quote
                and not in_backtick
            ):
                in_double_quote = (
                    not in_double_quote
                )
                current.append(char)
                i += 1
                continue

            if (
                char == "`"
                and not in_single_quote
                and not in_double_quote
            ):
                in_backtick = (
                    not in_backtick
                )
                current.append(char)
                i += 1
                continue

            if (
                char == ";"
                and not in_single_quote
                and not in_double_quote
                and not in_backtick
            ):
                statement = (
                    "".join(current)
                    .strip()
                )

                if statement:
                    statements.append(
                        statement
                    )

                current = []
                i += 1
                continue

            current.append(char)
            i += 1

        current.append("\n")

    final_statement = (
        "".join(current)
        .strip()
    )

    if final_statement:
        statements.append(
            final_statement
        )

    return statements


# ================================================================
# PRODUCTS STATUS MIGRATION
# ================================================================

def migrate_products_status(connection):
    """
    Safely migrate an existing products table to support
    the new soft-delete/status requirement.

    Existing databases may already have the products table
    without a status column.

    This function:

    1. Checks whether products.status exists.
    2. Adds it when missing.
    3. Sets existing products to ACTIVE when stock > 0.
    4. Sets existing products to INACTIVE when stock = 0.

    This operation is safe to run multiple times.
    """

    print(
        "Checking products.status migration..."
    )

    with connection.cursor() as cursor:

        # --------------------------------------------------------
        # CHECK WHETHER status COLUMN EXISTS
        # --------------------------------------------------------

        cursor.execute(
            """
            SELECT COUNT(*) AS column_count
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = 'products'
              AND COLUMN_NAME = 'status'
            """,
            (DB_NAME,),
        )

        result = cursor.fetchone()

        column_exists = (
            result
            and int(
                result["column_count"]
            ) > 0
        )

        # --------------------------------------------------------
        # ADD status COLUMN IF REQUIRED
        # --------------------------------------------------------

        if not column_exists:

            print(
                "products.status does not exist. "
                "Adding status column..."
            )

            cursor.execute(
                """
                ALTER TABLE products
                ADD COLUMN status VARCHAR(20)
                NOT NULL DEFAULT 'ACTIVE'
                AFTER stock_count
                """
            )

            print(
                "products.status column added successfully."
            )

        else:

            print(
                "products.status already exists. "
                "No column migration required."
            )

        # --------------------------------------------------------
        # SYNCHRONIZE STATUS WITH EXISTING STOCK
        # --------------------------------------------------------

        cursor.execute(
            """
            UPDATE products
            SET status =
                CASE
                    WHEN stock_count > 0
                        THEN 'ACTIVE'
                    ELSE 'INACTIVE'
                END
            WHERE status IS NULL
               OR status NOT IN (
                    'ACTIVE',
                    'INACTIVE'
               )
               OR (
                    stock_count = 0
                    AND status <> 'INACTIVE'
               )
               OR (
                    stock_count > 0
                    AND status <> 'ACTIVE'
               )
            """
        )

        updated_rows = cursor.rowcount

        print(
            "Product status synchronization completed. "
            f"Rows updated: {updated_rows}"
        )

    connection.commit()

    print(
        "Products status migration completed successfully."
    )


# ================================================================
# SCHEMA INITIALIZATION
# ================================================================

def initialize_schema():
    """
    Initialize the CloudMart MySQL schema using schema.sql
    packaged inside the Order Lambda deployment ZIP.

    Also performs safe migration of the existing products table
    so the new status column can be introduced without requiring
    manual database changes.
    """

    connection = None

    try:

        # --------------------------------------------------------
        # VERIFY schema.sql EXISTS
        # --------------------------------------------------------

        if not os.path.exists(
            SCHEMA_FILE
        ):

            print(
                f"Schema file not found: "
                f"{SCHEMA_FILE}"
            )

            return response(
                500,
                {
                    "message": (
                        "schema.sql not found "
                        "in Lambda package"
                    ),
                },
            )

        print(
            f"Reading schema file: "
            f"{SCHEMA_FILE}"
        )

        with open(
            SCHEMA_FILE,
            "r",
            encoding="utf-8",
        ) as schema_file:

            sql = schema_file.read()

        statements = (
            split_sql_statements(sql)
        )

        print(
            f"Found {len(statements)} SQL statements "
            f"in schema.sql"
        )

        # --------------------------------------------------------
        # CONNECT TO RDS
        # --------------------------------------------------------

        connection = get_connection()

        # --------------------------------------------------------
        # PERFORM PRODUCTS STATUS MIGRATION
        # --------------------------------------------------------

        migrate_products_status(
            connection
        )

        # --------------------------------------------------------
        # EXECUTE schema.sql
        # --------------------------------------------------------

        executed = 0
        skipped = 0

        with connection.cursor() as cursor:

            for index, statement in enumerate(
                statements,
                start=1,
            ):

                try:

                    print(
                        f"Executing SQL statement "
                        f"{index}/{len(statements)}"
                    )

                    cursor.execute(
                        statement
                    )

                    executed += 1

                except pymysql.MySQLError as exc:

                    error_code = (
                        exc.args[0]
                        if exc.args
                        else None
                    )

                    error_message = str(
                        exc
                    )

                    # ------------------------------------------------
                    # EXISTING TABLE / INDEX
                    # ------------------------------------------------

                    if error_code in (
                        1050,
                        1061,
                    ):

                        print(
                            "Skipping existing "
                            "database object "
                            f"for statement {index}: "
                            f"{error_message}"
                        )

                        skipped += 1
                        continue

                    # ------------------------------------------------
                    # DUPLICATE DATA
                    # ------------------------------------------------

                    if error_code == 1062:

                        print(
                            "Skipping duplicate data "
                            f"for statement {index}: "
                            f"{error_message}"
                        )

                        skipped += 1
                        continue

                    # ------------------------------------------------
                    # REAL SQL FAILURE
                    # ------------------------------------------------

                    print(
                        f"Schema statement "
                        f"{index} failed."
                    )

                    print(
                        f"MySQL error: "
                        f"{error_message}"
                    )

                    raise

        # --------------------------------------------------------
        # COMMIT
        # --------------------------------------------------------

        connection.commit()

        print(
            "Database schema initialization "
            "completed successfully."
        )

        put_metric(
            "SchemaInitializationSuccess"
        )

        return response(
            200,
            {
                "message": (
                    "Database schema initialized "
                    "successfully"
                ),
                "statements_found": len(
                    statements
                ),
                "statements_executed": executed,
                "statements_skipped": skipped,
            },
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(
            "Schema initialization error: "
            f"{type(exc).__name__}: {exc}"
        )

        put_metric(
            "SchemaInitializationFailures"
        )

        return response(
            500,
            {
                "message": (
                    "Database schema initialization failed"
                ),
                "error": str(exc),
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# CREATE ORDER
# ================================================================

def create_order(body, performed_by=None):

    if not isinstance(body, dict):
        raise ValueError(
            "Request body must be a JSON object"
        )

    customer_id = body.get(
        "customer_id"
    )

    items = body.get("items")

    # ------------------------------------------------------------
    # CUSTOMER VALIDATION
    # ------------------------------------------------------------

    if not customer_id:
        raise ValueError(
            "customer_id is required"
        )

    customer_id = str(
        customer_id
    ).strip()

    if not customer_id:
        raise ValueError(
            "customer_id cannot be empty"
        )

    # ------------------------------------------------------------
    # ITEM VALIDATION
    # ------------------------------------------------------------

    if not isinstance(items, list):
        raise ValueError(
            "items must be an array"
        )

    if len(items) == 0:
        raise ValueError(
            "items must contain at least one item"
        )

    if len(items) > 100:
        raise ValueError(
            "Too many order items"
        )

    # ------------------------------------------------------------
    # NORMALIZE ITEMS
    # ------------------------------------------------------------

    item_map = {}

    for item in items:

        if not isinstance(item, dict):
            raise ValueError(
                "Each item must be an object"
            )

        if "product_id" not in item:
            raise ValueError(
                "product_id is required"
            )

        if "quantity" not in item:
            raise ValueError(
                "quantity is required"
            )

        try:

            product_id = int(
                item["product_id"]
            )

            quantity = int(
                item["quantity"]
            )

        except (TypeError, ValueError):

            raise ValueError(
                "product_id and quantity "
                "must be numbers"
            )

        if product_id <= 0:
            raise ValueError(
                "product_id must be greater than zero"
            )

        if quantity <= 0:
            raise ValueError(
                "quantity must be greater than zero"
            )

        item_map[product_id] = (
            item_map.get(
                product_id,
                0,
            )
            + quantity
        )

    normalized_items = [
        {
            "product_id": product_id,
            "quantity": quantity,
        }
        for product_id, quantity
        in sorted(
            item_map.items()
        )
    ]

    order_id = (
        "ORD-"
        + uuid.uuid4().hex[:12].upper()
    )

    now = (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
    )

    connection = None

    try:

        connection = get_connection()

        # --------------------------------------------------------
        # VERIFY CUSTOMER
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    customer_id
                FROM customers
                WHERE customer_id = %s
                """,
                (customer_id,),
            )

            customer = cursor.fetchone()

        if not customer:

            connection.rollback()

            return response(
                400,
                {
                    "message": (
                        "Customer does not exist"
                    ),
                    "customer_id": customer_id,
                },
            )

        # --------------------------------------------------------
        # READ PRODUCTS AND CHECK STOCK
        # --------------------------------------------------------

        total_amount = Decimal(
            "0.00"
        )

        priced_items = []

        with connection.cursor() as cursor:

            for item in normalized_items:

                product_id = item[
                    "product_id"
                ]

                quantity = item[
                    "quantity"
                ]

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        name,
                        description,
                        price,
                        stock_count,
                        status
                    FROM products
                    WHERE product_id = %s
                    FOR UPDATE
                    """,
                    (product_id,),
                )

                product = cursor.fetchone()

                if not product:

                    raise ValueError(
                        f"Product {product_id} "
                        f"does not exist"
                    )

                price = Decimal(
                    str(
                        product["price"]
                    )
                )

                stock_count = int(
                    product[
                        "stock_count"
                    ]
                )

                product_status = (
                    product["status"]
                )

                # ------------------------------------------------
                # INACTIVE PRODUCT
                # ------------------------------------------------

                if product_status != "ACTIVE":

                    connection.rollback()

                    return response(
                        409,
                        {
                            "message": (
                                "Product is inactive"
                            ),
                            "product_id": product_id,
                            "product_name": product[
                                "name"
                            ],
                            "status": product_status,
                        },
                    )

                # ------------------------------------------------
                # OUT OF STOCK
                # ------------------------------------------------

                if stock_count <= 0:

                    connection.rollback()

                    return response(
                        409,
                        {
                            "message": (
                                "Product is out of stock"
                            ),
                            "product_id": product_id,
                            "product_name": product[
                                "name"
                            ],
                            "available_stock": 0,
                        },
                    )

                # ------------------------------------------------
                # INSUFFICIENT STOCK
                # ------------------------------------------------

                if quantity > stock_count:

                    connection.rollback()

                    return response(
                        409,
                        {
                            "message": (
                                "Insufficient stock"
                            ),
                            "product_id": product_id,
                            "product_name": product[
                                "name"
                            ],
                            "requested_quantity": quantity,
                            "available_stock": stock_count,
                        },
                    )

                priced_items.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "price": price,
                    }
                )

                total_amount += (
                    price * quantity
                )

        # --------------------------------------------------------
        # INSERT ORDER
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                INSERT INTO orders
                (
                    order_id,
                    customer_id,
                    status,
                    total_amount,
                    created_at,
                    updated_at
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
                """,
                (
                    order_id,
                    customer_id,
                    "PROCESSING",
                    total_amount,
                    now,
                    now,
                ),
            )

        # --------------------------------------------------------
        # INSERT ORDER ITEMS
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            for item in priced_items:

                cursor.execute(
                    """
                    INSERT INTO order_items
                    (
                        order_id,
                        product_id,
                        quantity,
                        price,
                        created_at
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        order_id,
                        item[
                            "product_id"
                        ],
                        item[
                            "quantity"
                        ],
                        item["price"],
                        now,
                    ),
                )

        # --------------------------------------------------------
        # AUDIT: ORDER CREATED
        # --------------------------------------------------------

        new_order_snapshot = {
            "order_id": order_id,
            "customer_id": customer_id,
            "status": "PROCESSING",
            "total_amount": total_amount,
            "items": [
                {
                    "product_id": item[
                        "product_id"
                    ],
                    "quantity": item[
                        "quantity"
                    ],
                    "price": item[
                        "price"
                    ],
                }
                for item in priced_items
            ],
            "created_at": now,
            "updated_at": now,
        }

        write_audit_log(
            connection=connection,
            entity_type="ORDER",
            entity_id=order_id,
            action="ORDER_CREATED",
            old_value=None,
            new_value=new_order_snapshot,
            performed_by=performed_by,
        )

        # --------------------------------------------------------
        # COMMIT ORDER + AUDIT TOGETHER
        # --------------------------------------------------------

        connection.commit()

        print(
            f"Order {order_id} created successfully "
            f"with PROCESSING status"
        )

        # --------------------------------------------------------
        # SEND ORDER TO SQS
        # --------------------------------------------------------

        message = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": [
                {
                    "product_id": item[
                        "product_id"
                    ],
                    "quantity": item[
                        "quantity"
                    ],
                    "price": str(
                        item["price"]
                    ),
                }
                for item in priced_items
            ],
            "total_amount": str(
                total_amount
            ),
        }

        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(
                message
            ),
        )

        print(
            f"Order {order_id} sent to SQS successfully"
        )

        put_metric(
            "OrdersCreated"
        )

        put_metric(
            "OrderRequests"
        )

        return response(
            202,
            {
                "message": (
                    "Order accepted for processing"
                ),
                "order_id": order_id,
                "customer_id": customer_id,
                "status": "PROCESSING",
                "total_amount": str(
                    total_amount
                ),
            },
        )

    except ValueError as exc:

        if connection:
            connection.rollback()

        print(
            f"Order validation error: {exc}"
        )

        put_metric(
            "OrderRequests"
        )

        return response(
            400,
            {
                "message": str(exc),
            },
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(
            "Order creation error: "
            f"{type(exc).__name__}: {exc}"
        )

        put_metric(
            "OrdersFailed"
        )

        put_metric(
            "OrderRequests"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# GET SINGLE ORDER
# ================================================================

def get_order(order_id):

    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    o.order_id,
                    o.customer_id,
                    c.name AS customer_name,
                    c.email AS customer_email,
                    o.status,
                    o.total_amount,
                    o.created_at,
                    o.updated_at
                FROM orders o
                INNER JOIN customers c
                    ON o.customer_id = c.customer_id
                WHERE o.order_id = %s
                """,
                (order_id,),
            )

            order = cursor.fetchone()

        if not order:

            return response(
                404,
                {
                    "message": "Order not found",
                },
            )

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    oi.product_id,
                    p.name AS product_name,
                    p.description AS product_description,
                    oi.quantity,
                    oi.price,
                    oi.created_at
                FROM order_items oi
                INNER JOIN products p
                    ON oi.product_id = p.product_id
                WHERE oi.order_id = %s
                ORDER BY oi.product_id
                """,
                (order_id,),
            )

            order["items"] = (
                cursor.fetchall()
            )

        return response(
            200,
            order,
        )

    except Exception as exc:

        print(
            f"Get order error: {exc}"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# GET CUSTOMER ORDERS
# ================================================================

def get_customer_orders(customer_id):

    connection = None

    try:

        connection = get_connection()

        # --------------------------------------------------------
        # GET CUSTOMER
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    customer_id,
                    name,
                    email,
                    phone
                FROM customers
                WHERE customer_id = %s
                """,
                (customer_id,),
            )

            customer = cursor.fetchone()

        if not customer:

            return response(
                404,
                {
                    "message": (
                        "Customer not found"
                    ),
                    "customer_id": customer_id,
                },
            )

        # --------------------------------------------------------
        # GET ORDERS + PRODUCTS
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    o.order_id,
                    o.customer_id,
                    o.status,
                    o.total_amount,
                    o.created_at,
                    o.updated_at,

                    oi.product_id,
                    p.name AS product_name,
                    p.description AS product_description,
                    oi.quantity,
                    oi.price AS item_price

                FROM orders o

                INNER JOIN order_items oi
                    ON o.order_id = oi.order_id

                INNER JOIN products p
                    ON oi.product_id = p.product_id

                WHERE o.customer_id = %s

                ORDER BY
                    o.created_at DESC,
                    oi.product_id
                """,
                (customer_id,),
            )

            rows = cursor.fetchall()

        # --------------------------------------------------------
        # GROUP ITEMS BY ORDER
        # --------------------------------------------------------

        orders = {}

        for row in rows:

            order_id = row[
                "order_id"
            ]

            if order_id not in orders:

                orders[order_id] = {
                    "order_id": order_id,
                    "customer_id": row[
                        "customer_id"
                    ],
                    "status": row[
                        "status"
                    ],
                    "total_amount": row[
                        "total_amount"
                    ],
                    "created_at": row[
                        "created_at"
                    ],
                    "updated_at": row[
                        "updated_at"
                    ],
                    "items": [],
                }

            orders[order_id][
                "items"
            ].append(
                {
                    "product_id": row[
                        "product_id"
                    ],
                    "product_name": row[
                        "product_name"
                    ],
                    "product_description": row[
                        "product_description"
                    ],
                    "quantity": row[
                        "quantity"
                    ],
                    "price": row[
                        "item_price"
                    ],
                }
            )

        return response(
            200,
            {
                "customer": customer,
                "count": len(orders),
                "orders": list(
                    orders.values()
                ),
            },
        )

    except Exception as exc:

        print(
            "Get customer orders error: "
            f"{exc}"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# UPDATE ORDER
# ================================================================

def update_order(
    order_id,
    body,
    performed_by=None,
):

    if not isinstance(body, dict):

        return response(
            400,
            {
                "message": (
                    "Request body must be "
                    "a JSON object"
                ),
            },
        )

    status = body.get(
        "status"
    )

    allowed_statuses = [
        "PROCESSING",
        "CONFIRMED",
        "FAILED",
        "CANCELLED",
    ]

    if status not in allowed_statuses:

        return response(
            400,
            {
                "message": (
                    "Invalid order status"
                ),
                "allowed_statuses": (
                    allowed_statuses
                ),
            },
        )

    connection = None

    try:

        connection = get_connection()

        now = (
            datetime.now(timezone.utc)
            .replace(tzinfo=None)
        )

        # --------------------------------------------------------
        # GET OLD ORDER
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    order_id,
                    customer_id,
                    status,
                    total_amount,
                    created_at,
                    updated_at
                FROM orders
                WHERE order_id = %s
                FOR UPDATE
                """,
                (order_id,),
            )

            old_order = (
                cursor.fetchone()
            )

            if not old_order:

                connection.rollback()

                return response(
                    404,
                    {
                        "message": (
                            "Order not found"
                        ),
                    },
                )

        # --------------------------------------------------------
        # UPDATE ORDER
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = %s,
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    status,
                    now,
                    order_id,
                ),
            )

        # --------------------------------------------------------
        # AUDIT: ORDER UPDATED
        # --------------------------------------------------------

        old_order_snapshot = {
            "order_id": old_order[
                "order_id"
            ],
            "customer_id": old_order[
                "customer_id"
            ],
            "status": old_order[
                "status"
            ],
            "total_amount": old_order[
                "total_amount"
            ],
            "created_at": old_order[
                "created_at"
            ],
            "updated_at": old_order[
                "updated_at"
            ],
        }

        new_order_snapshot = {
            "order_id": old_order[
                "order_id"
            ],
            "customer_id": old_order[
                "customer_id"
            ],
            "status": status,
            "total_amount": old_order[
                "total_amount"
            ],
            "created_at": old_order[
                "created_at"
            ],
            "updated_at": now,
        }

        write_audit_log(
            connection=connection,
            entity_type="ORDER",
            entity_id=order_id,
            action="ORDER_UPDATED",
            old_value=old_order_snapshot,
            new_value=new_order_snapshot,
            performed_by=performed_by,
        )

        # --------------------------------------------------------
        # COMMIT
        # --------------------------------------------------------

        connection.commit()

        print(
            f"Order {order_id} updated: "
            f"{old_order['status']} -> {status}"
        )

        return response(
            200,
            {
                "message": "Order updated",
                "order_id": order_id,
                "old_status": old_order[
                    "status"
                ],
                "status": status,
            },
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(
            f"Update order error: {exc}"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# CANCEL ORDER
# ================================================================

def cancel_order(
    order_id,
    performed_by=None,
):

    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            # ----------------------------------------------------
            # LOCK ORDER
            # ----------------------------------------------------

            cursor.execute(
                """
                SELECT
                    order_id,
                    customer_id,
                    status,
                    total_amount,
                    created_at,
                    updated_at
                FROM orders
                WHERE order_id = %s
                FOR UPDATE
                """,
                (order_id,),
            )

            order = cursor.fetchone()

            if not order:

                connection.rollback()

                return response(
                    404,
                    {
                        "message": (
                            "Order not found"
                        ),
                    },
                )

            # ----------------------------------------------------
            # ONLY PROCESSING ORDERS CAN BE CANCELLED
            # ----------------------------------------------------

            if (
                order["status"]
                != "PROCESSING"
            ):

                connection.rollback()

                return response(
                    400,
                    {
                        "message": (
                            "Order cannot be "
                            "cancelled because "
                            "it is no longer "
                            "processing"
                        ),
                        "current_status": (
                            order["status"]
                        ),
                    },
                )

            now = (
                datetime.now(
                    timezone.utc
                )
                .replace(tzinfo=None)
            )

            # ----------------------------------------------------
            # UPDATE ORDER
            # ----------------------------------------------------

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = 'CANCELLED',
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    now,
                    order_id,
                ),
            )

        # --------------------------------------------------------
        # AUDIT: ORDER CANCELLED
        # --------------------------------------------------------

        old_order_snapshot = {
            "order_id": order[
                "order_id"
            ],
            "customer_id": order[
                "customer_id"
            ],
            "status": order[
                "status"
            ],
            "total_amount": order[
                "total_amount"
            ],
            "created_at": order[
                "created_at"
            ],
            "updated_at": order[
                "updated_at"
            ],
        }

        new_order_snapshot = {
            "order_id": order[
                "order_id"
            ],
            "customer_id": order[
                "customer_id"
            ],
            "status": "CANCELLED",
            "total_amount": order[
                "total_amount"
            ],
            "created_at": order[
                "created_at"
            ],
            "updated_at": now,
        }

        write_audit_log(
            connection=connection,
            entity_type="ORDER",
            entity_id=order_id,
            action="ORDER_CANCELLED",
            old_value=old_order_snapshot,
            new_value=new_order_snapshot,
            performed_by=performed_by,
        )

        # --------------------------------------------------------
        # COMMIT
        # --------------------------------------------------------

        connection.commit()

        print(
            f"Order {order_id} cancelled successfully"
        )

        put_metric(
            "OrdersCancelled"
        )

        return response(
            200,
            {
                "message": "Order cancelled",
                "order_id": order_id,
                "status": "CANCELLED",
            },
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(
            f"Cancel order error: {exc}"
        )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(
    event,
    context,
):

    if not isinstance(event, dict):
        event = {}

    print(
        "Order Lambda request: "
        f"{event.get('httpMethod', '')} "
        f"{event.get('path', '')}"
    )

    # ============================================================
    # INTERNAL SCHEMA INITIALIZATION
    # ============================================================

    if (
        event.get("action")
        == "initialize_schema"
    ):

        print(
            "Received initialize_schema action"
        )

        return initialize_schema()

    # ============================================================
    # API GATEWAY INFORMATION
    # ============================================================

    method = (
        event.get(
            "httpMethod",
            "",
        )
        .upper()
    )

    path = event.get(
        "path",
        "",
    )

    path_parameters = (
        event.get("pathParameters")
        or {}
    )

    query_parameters = (
        event.get(
            "queryStringParameters"
        )
        or {}
    )

    order_id = path_parameters.get(
        "orderId"
    )

    # ============================================================
    # POST /orders
    # ============================================================

    if (
        method == "POST"
        and not order_id
    ):

        try:

            body = parse_request_body(
                event
            )

            performed_by = (
                get_performed_by(
                    event,
                    body,
                )
            )

            return create_order(
                body,
                performed_by,
            )

        except ValueError as exc:

            return response(
                400,
                {
                    "message": str(exc),
                },
            )

    # ============================================================
    # POST /orders/{orderId}/cancel
    # ============================================================

    if (
        method == "POST"
        and order_id
        and path.endswith(
            "/cancel"
        )
    ):

        performed_by = (
            get_performed_by(
                event,
                {},
            )
        )

        return cancel_order(
            order_id,
            performed_by,
        )

    # ============================================================
    # GET /orders/{orderId}
    # ============================================================

    if (
        method == "GET"
        and order_id
    ):

        return get_order(
            order_id
        )

    # ============================================================
    # GET /orders?customer_id=CUST101
    # ============================================================

    if method == "GET":

        customer_id = (
            query_parameters.get(
                "customer_id"
            )
        )

        if not customer_id:

            return response(
                400,
                {
                    "message": (
                        "customer_id query "
                        "parameter is required"
                    ),
                },
            )

        customer_id = str(
            customer_id
        ).strip()

        if not customer_id:

            return response(
                400,
                {
                    "message": (
                        "customer_id cannot "
                        "be empty"
                    ),
                },
            )

        return get_customer_orders(
            customer_id
        )

    # ============================================================
    # PUT /orders/{orderId}
    # ============================================================

    if (
        method == "PUT"
        and order_id
    ):

        try:

            body = parse_request_body(
                event
            )

            performed_by = (
                get_performed_by(
                    event,
                    body,
                )
            )

            return update_order(
                order_id,
                body,
                performed_by,
            )

        except ValueError as exc:

            return response(
                400,
                {
                    "message": str(exc),
                },
            )

    # ============================================================
    # UNKNOWN ROUTE
    # ============================================================

    return response(
        404,
        {
            "message": "Route not found",
        },
    )
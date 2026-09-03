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
# HTTP RESPONSE
# ================================================================

def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, default=str),
    }


# ================================================================
# REQUEST BODY PARSER
# ================================================================

def parse_request_body(event):
    body = event.get("body")

    if body is None:
        return {}

    if isinstance(body, dict):
        return body

    if not isinstance(body, str):
        raise ValueError("Request body must be JSON")

    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception as exc:
            raise ValueError("Invalid base64 request body") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc


# ================================================================
# SCHEMA INITIALIZATION
# ================================================================

def execute_schema_file(connection):
    """
    Executes the schema.sql file packaged inside the Lambda ZIP.

    The deployment workflow copies the repository root schema.sql
    into the Order Lambda package before creating the ZIP.
    """

    schema_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "schema.sql",
    )

    if not os.path.exists(schema_path):
        raise FileNotFoundError(
            f"schema.sql was not found in Lambda package: {schema_path}"
        )

    print(f"Executing schema file: {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        sql = schema_file.read()

    # Remove simple SQL comments.
    cleaned_lines = []

    for line in sql.splitlines():
        stripped = line.strip()

        if stripped.startswith("--"):
            continue

        cleaned_lines.append(line)

    sql = "\n".join(cleaned_lines)

    # The current CloudMart schema contains normal SQL statements
    # separated by semicolons. This is intentionally simple because
    # the schema does not contain stored procedures or triggers.
    statements = [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip()
    ]

    with connection.cursor() as cursor:
        for statement in statements:
            print(
                "Executing schema statement: "
                + statement[:120].replace("\n", " ")
            )
            cursor.execute(statement)

    connection.commit()

    print(
        f"schema.sql executed successfully. "
        f"Statements executed: {len(statements)}"
    )


def ensure_customer_table(connection):
    """
    Safety migration for existing RDS databases.

    If the existing database was created before customers was added
    to schema.sql, this creates the missing table automatically.
    """

    sql = """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id VARCHAR(100) NOT NULL,
        name VARCHAR(100) NOT NULL,
        email VARCHAR(255) NOT NULL,
        phone VARCHAR(20),
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (customer_id),
        UNIQUE KEY uk_customers_email (email)
    ) ENGINE=InnoDB
    """

    with connection.cursor() as cursor:
        cursor.execute(sql)

    connection.commit()

    print("Customer table verified.")


def ensure_order_customer_foreign_key(connection):
    """
    Adds the customer foreign key if it does not already exist.

    This is important because CREATE TABLE IF NOT EXISTS does not
    modify an already-existing orders table.
    """

    with connection.cursor() as cursor:

        cursor.execute(
            """
            SELECT COUNT(*) AS constraint_count
            FROM information_schema.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE()
              AND TABLE_NAME = 'orders'
              AND CONSTRAINT_NAME = 'fk_orders_customer'
            """
        )

        result = cursor.fetchone()

        constraint_exists = result["constraint_count"] > 0

        if constraint_exists:
            print("Customer foreign key already exists.")
            connection.commit()
            return

        # Make sure existing orders do not violate the new FK.
        cursor.execute(
            """
            SELECT DISTINCT o.customer_id
            FROM orders o
            LEFT JOIN customers c
                ON c.customer_id = o.customer_id
            WHERE o.customer_id IS NOT NULL
              AND c.customer_id IS NULL
            """
        )

        missing_customers = cursor.fetchall()

        if missing_customers:
            print(
                f"Found {len(missing_customers)} existing customer IDs "
                f"without customer records."
            )

            for row in missing_customers:
                customer_id = row["customer_id"]

                safe_suffix = uuid.uuid5(
                    uuid.NAMESPACE_DNS,
                    str(customer_id),
                ).hex[:20]

                placeholder_email = (
                    f"customer-{safe_suffix}@cloudmart.local"
                )

                cursor.execute(
                    """
                    INSERT INTO customers
                    (
                        customer_id,
                        name,
                        email
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        customer_id,
                        f"Customer {customer_id}",
                        placeholder_email,
                    ),
                )

        cursor.execute(
            """
            ALTER TABLE orders
            ADD CONSTRAINT fk_orders_customer
            FOREIGN KEY (customer_id)
            REFERENCES customers(customer_id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT
            """
        )

    connection.commit()

    print("Customer foreign key verified/created.")


def initialize_schema():
    """
    Initializes/migrates the CloudMart MySQL schema.

    This function is called by the deployment workflow using:
        {"action": "initialize_schema"}
    """

    connection = None

    try:
        connection = get_connection()

        # Execute repository schema.sql first.
        execute_schema_file(connection)

        # Existing databases may not have these newer objects.
        ensure_customer_table(connection)

        # Existing orders table may have been created without the FK.
        ensure_order_customer_foreign_key(connection)

        return {
            "statusCode": 200,
            "message": "Database schema initialized successfully",
            "database": DB_NAME,
        }

    except Exception as exc:
        if connection:
            connection.rollback()

        print(f"Schema initialization error: {exc}")

        raise

    finally:
        if connection:
            connection.close()


# ================================================================
# CREATE ORDER
# ================================================================

def create_order(body):

    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")

    customer_id = body.get("customer_id")
    items = body.get("items")

    if not customer_id:
        raise ValueError("customer_id is required")

    customer_id = str(customer_id).strip()

    if not customer_id:
        raise ValueError("customer_id cannot be empty")

    if len(customer_id) > 100:
        raise ValueError("customer_id must not exceed 100 characters")

    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("items must contain at least one item")

    if len(items) > 100:
        raise ValueError("Too many order items")

    normalized_items = []

    for item in items:

        if not isinstance(item, dict):
            raise ValueError("Each item must be an object")

        if "product_id" not in item:
            raise ValueError("product_id is required")

        if "quantity" not in item:
            raise ValueError("quantity is required")

        try:
            product_id = int(item["product_id"])
            quantity = int(item["quantity"])
        except (TypeError, ValueError):
            raise ValueError(
                "product_id and quantity must be numbers"
            )

        if product_id <= 0:
            raise ValueError(
                "product_id must be greater than zero"
            )

        if quantity <= 0:
            raise ValueError(
                "quantity must be greater than zero"
            )

        normalized_items.append(
            {
                "product_id": product_id,
                "quantity": quantity,
            }
        )

    order_id = "ORD-" + uuid.uuid4().hex[:12].upper()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    connection = None

    try:

        connection = get_connection()

        # ------------------------------------------------------------
        # Verify customer
        # ------------------------------------------------------------

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
                    "message": "Customer does not exist",
                    "customer_id": customer_id,
                },
            )

        # ------------------------------------------------------------
        # Read product prices
        #
        # IMPORTANT:
        # The actual products table uses:
        #   product_id
        #   price
        #   stock_count
        #
        # It does NOT use:
        #   id
        #   active
        # ------------------------------------------------------------

        total_amount = Decimal("0.00")
        priced_items = []

        with connection.cursor() as cursor:

            for item in normalized_items:

                product_id = item["product_id"]
                quantity = item["quantity"]

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        price,
                        stock_count
                    FROM products
                    WHERE product_id = %s
                    """,
                    (product_id,),
                )

                product = cursor.fetchone()

                if not product:
                    raise ValueError(
                        f"Product {product_id} does not exist"
                    )

                price = Decimal(str(product["price"]))

                stock_count = int(product["stock_count"])

                if quantity > stock_count:
                    raise ValueError(
                        f"Insufficient stock for product {product_id}. "
                        f"Available stock: {stock_count}"
                    )

                priced_items.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "price": price,
                    }
                )

                total_amount += price * quantity

        # ------------------------------------------------------------
        # Insert order
        # ------------------------------------------------------------

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

        # ------------------------------------------------------------
        # Insert order items
        # ------------------------------------------------------------

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
                        item["product_id"],
                        item["quantity"],
                        item["price"],
                        now,
                    ),
                )

        # Commit database transaction before publishing to SQS.
        connection.commit()

        # ------------------------------------------------------------
        # Send order to SQS
        # ------------------------------------------------------------

        message = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": [
                {
                    "product_id": item["product_id"],
                    "quantity": item["quantity"],
                    "price": str(item["price"]),
                }
                for item in priced_items
            ],
            "total_amount": str(total_amount),
        }

        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(message),
        )

        put_metric("OrdersCreated")
        put_metric("OrderRequests")

        return response(
            202,
            {
                "message": "Order accepted for processing",
                "order_id": order_id,
                "status": "PROCESSING",
            },
        )

    except ValueError as exc:

        if connection:
            connection.rollback()

        print(f"Order validation error: {exc}")

        put_metric("OrderRequests")

        return response(
            400,
            {
                "message": str(exc),
            },
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(f"Order creation error: {exc}")

        put_metric("OrderRequests")

        return response(
            500,
            {
                "message": "Internal server error",
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
                    order_id,
                    customer_id,
                    status,
                    total_amount,
                    created_at,
                    updated_at
                FROM orders
                WHERE order_id = %s
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
                    order_item_id,
                    product_id,
                    quantity,
                    price,
                    created_at
                FROM order_items
                WHERE order_id = %s
                ORDER BY order_item_id
                """,
                (order_id,),
            )

            items = cursor.fetchall()

        order["items"] = items

        return response(
            200,
            order,
        )

    except Exception as exc:

        print(f"Get order error: {exc}")

        return response(
            500,
            {
                "message": "Internal server error",
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# GET ORDERS FOR CUSTOMER
# ================================================================

def get_customer_orders(customer_id):

    connection = None

    try:

        connection = get_connection()

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
                WHERE customer_id = %s
                ORDER BY created_at DESC
                """,
                (customer_id,),
            )

            orders = cursor.fetchall()

        return response(
            200,
            {
                "orders": orders,
            },
        )

    except Exception as exc:

        print(f"Get customer orders error: {exc}")

        return response(
            500,
            {
                "message": "Internal server error",
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# UPDATE ORDER
# ================================================================

def update_order(order_id, body):

    if not isinstance(body, dict):
        return response(
            400,
            {
                "message": "Request body must be a JSON object",
            },
        )

    status = body.get("status")

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
                "message": "Invalid order status",
            },
        )

    connection = None

    try:

        connection = get_connection()

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
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    order_id,
                ),
            )

            if cursor.rowcount == 0:

                connection.rollback()

                return response(
                    404,
                    {
                        "message": "Order not found",
                    },
                )

        connection.commit()

        return response(
            200,
            {
                "message": "Order updated",
                "order_id": order_id,
                "status": status,
            },
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(f"Update order error: {exc}")

        return response(
            500,
            {
                "message": "Internal server error",
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# CANCEL ORDER
# ================================================================

def cancel_order(order_id):

    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    status
                FROM orders
                WHERE order_id = %s
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

            if order["status"] != "PROCESSING":

                return response(
                    400,
                    {
                        "message": "Order cannot be cancelled",
                    },
                )

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = 'CANCELLED',
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    datetime.now(timezone.utc).replace(tzinfo=None),
                    order_id,
                ),
            )

        connection.commit()

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

        print(f"Cancel order error: {exc}")

        return response(
            500,
            {
                "message": "Internal server error",
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    if not isinstance(event, dict):
        event = {}

    print(
        f"Order Lambda request: "
        f"{event.get('httpMethod', '')} "
        f"{event.get('path', '')}"
    )

    # ============================================================
    # SCHEMA INITIALIZATION
    #
    # Used by GitHub Actions deployment:
    #
    # {
    #     "action": "initialize_schema"
    # }
    # ============================================================

    if event.get("action") == "initialize_schema":

        try:

            result = initialize_schema()

            return response(
                200,
                result,
            )

        except Exception as exc:

            print(f"Schema initialization failed: {exc}")

            return response(
                500,
                {
                    "message": "Schema initialization failed",
                    "error": str(exc),
                },
            )

    # ============================================================
    # API GATEWAY REQUEST
    # ============================================================

    method = event.get("httpMethod", "").upper()

    path = event.get("path", "")

    path_parameters = event.get("pathParameters") or {}

    query_parameters = event.get("queryStringParameters") or {}

    order_id = path_parameters.get("orderId")

    # ============================================================
    # POST /orders
    # ============================================================

    if method == "POST" and not order_id:

        try:

            body = parse_request_body(event)

            return create_order(body)

        except ValueError as exc:

            return response(
                400,
                {
                    "message": str(exc),
                },
            )

    # ============================================================
    # GET /orders/{orderId}
    # ============================================================

    if method == "GET" and order_id:

        return get_order(order_id)

    # ============================================================
    # GET /orders?customer_id=...
    # ============================================================

    if method == "GET":

        customer_id = query_parameters.get("customer_id")

        if not customer_id:

            return response(
                400,
                {
                    "message": (
                        "customer_id query parameter is required"
                    ),
                },
            )

        return get_customer_orders(customer_id)

    # ============================================================
    # PUT /orders/{orderId}
    # ============================================================

    if method == "PUT" and order_id:

        try:

            body = parse_request_body(event)

            return update_order(order_id, body)

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

    if method == "POST" and order_id:

        if path.endswith("/cancel"):

            return cancel_order(order_id)

    # ============================================================
    # UNKNOWN ROUTE
    # ============================================================

    return response(
        404,
        {
            "message": "Route not found",
        },
    )
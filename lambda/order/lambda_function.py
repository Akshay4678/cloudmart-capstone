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
        autocommit=False
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
                            "Value": ENVIRONMENT
                        },
                        {
                            "Name": "Service",
                            "Value": "Order"
                        }
                    ]
                }
            ]
        )
    except Exception as exc:
        print(f"Metric error: {exc}")


# ================================================================
# RESPONSE
# ================================================================

def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(body, default=str)
    }


# ================================================================
# CREATE ORDERS TABLE
# ================================================================

def initialize_orders_table(connection):

    sql = """
    CREATE TABLE IF NOT EXISTS orders (
        order_id VARCHAR(50) PRIMARY KEY,
        customer_id VARCHAR(100) NOT NULL,
        status VARCHAR(30) NOT NULL,
        total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """

    with connection.cursor() as cursor:
        cursor.execute(sql)

    connection.commit()


# ================================================================
# CREATE ORDER ITEMS TABLE
# ================================================================

def initialize_order_items_table(connection):

    sql = """
    CREATE TABLE IF NOT EXISTS order_items (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        order_id VARCHAR(50) NOT NULL,
        product_id INT NOT NULL,
        quantity INT NOT NULL,
        price DECIMAL(12,2) NOT NULL DEFAULT 0.00,
        created_at DATETIME NOT NULL,
        INDEX idx_order_items_order_id (order_id)
    )
    """

    with connection.cursor() as cursor:
        cursor.execute(sql)

    connection.commit()


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

    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("items must contain at least one item")

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
            raise ValueError("product_id and quantity must be numbers")

        if product_id <= 0:
            raise ValueError("product_id must be greater than zero")

        if quantity <= 0:
            raise ValueError("quantity must be greater than zero")

    order_id = "ORD-" + uuid.uuid4().hex[:12].upper()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    connection = None

    try:

        connection = get_connection()

        initialize_orders_table(connection)
        initialize_order_items_table(connection)

        total_amount = Decimal("0.00")

        # --------------------------------------------------------
        # Read product prices
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            for item in items:

                cursor.execute(
                    """
                    SELECT id, price
                    FROM products
                    WHERE id = %s
                      AND active = 1
                    """,
                    (int(item["product_id"]),)
                )

                product = cursor.fetchone()

                if not product:
                    raise ValueError(
                        f"Product {item['product_id']} does not exist"
                    )

                price = Decimal(str(product["price"]))

                item["price"] = price

                total_amount += (
                    price * int(item["quantity"])
                )

        # --------------------------------------------------------
        # Insert order
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
                    now
                )
            )

        # --------------------------------------------------------
        # Insert order items
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            for item in items:

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
                        int(item["product_id"]),
                        int(item["quantity"]),
                        item["price"],
                        now
                    )
                )

        connection.commit()

        # --------------------------------------------------------
        # Send order to SQS
        # --------------------------------------------------------

        message = {
            "order_id": order_id
        }

        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(message)
        )

        put_metric("OrdersCreated")
        put_metric("OrderRequests")

        return response(
            202,
            {
                "message": "Order accepted for processing",
                "order_id": order_id,
                "status": "PROCESSING"
            }
        )

    except ValueError as exc:

        if connection:
            connection.rollback()

        put_metric("OrderRequests")

        return response(
            400,
            {
                "message": str(exc)
            }
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(f"Order creation error: {exc}")

        put_metric("OrderRequests")

        return response(
            500,
            {
                "message": "Internal server error"
            }
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

        initialize_orders_table(connection)

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
                (order_id,)
            )

            order = cursor.fetchone()

        if not order:

            return response(
                404,
                {
                    "message": "Order not found"
                }
            )

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    product_id,
                    quantity,
                    price
                FROM order_items
                WHERE order_id = %s
                """,
                (order_id,)
            )

            items = cursor.fetchall()

        order["items"] = items

        return response(
            200,
            order
        )

    except Exception as exc:

        print(f"Get order error: {exc}")

        return response(
            500,
            {
                "message": "Internal server error"
            }
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

        initialize_orders_table(connection)

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
                (customer_id,)
            )

            orders = cursor.fetchall()

        return response(
            200,
            {
                "orders": orders
            }
        )

    except Exception as exc:

        print(f"Get customer orders error: {exc}")

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# UPDATE ORDER
# ================================================================

def update_order(order_id, body):

    status = body.get("status")

    allowed_statuses = [
        "PROCESSING",
        "CONFIRMED",
        "FAILED",
        "CANCELLED"
    ]

    if status not in allowed_statuses:

        return response(
            400,
            {
                "message": "Invalid order status"
            }
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
                    order_id
                )
            )

            if cursor.rowcount == 0:

                connection.rollback()

                return response(
                    404,
                    {
                        "message": "Order not found"
                    }
                )

        connection.commit()

        return response(
            200,
            {
                "message": "Order updated",
                "order_id": order_id,
                "status": status
            }
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(f"Update order error: {exc}")

        return response(
            500,
            {
                "message": "Internal server error"
            }
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
                SELECT status
                FROM orders
                WHERE order_id = %s
                """,
                (order_id,)
            )

            order = cursor.fetchone()

            if not order:

                return response(
                    404,
                    {
                        "message": "Order not found"
                    }
                )

            if order["status"] not in ["PROCESSING"]:

                return response(
                    400,
                    {
                        "message": "Order cannot be cancelled"
                    }
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
                    order_id
                )
            )

        connection.commit()

        return response(
            200,
            {
                "message": "Order cancelled",
                "order_id": order_id,
                "status": "CANCELLED"
            }
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(f"Cancel order error: {exc}")

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    print(
        f"Order Lambda request: "
        f"{event.get('httpMethod')} "
        f"{event.get('path')}"
    )

    method = event.get("httpMethod", "")
    path_parameters = event.get("pathParameters") or {}
    query_parameters = event.get("queryStringParameters") or {}

    order_id = path_parameters.get("orderId")

    # ------------------------------------------------------------
    # POST /orders
    # ------------------------------------------------------------

    if method == "POST" and not order_id:

        try:

            body = event.get("body")

            if isinstance(body, str):
                body = json.loads(body)

            return create_order(body)

        except json.JSONDecodeError:

            return response(
                400,
                {
                    "message": "Invalid JSON body"
                }
            )

    # ------------------------------------------------------------
    # GET /orders/{orderId}
    # ------------------------------------------------------------

    if method == "GET" and order_id:

        return get_order(order_id)

    # ------------------------------------------------------------
    # GET /orders?customer_id=...
    # ------------------------------------------------------------

    if method == "GET":

        customer_id = query_parameters.get("customer_id")

        if not customer_id:

            return response(
                400,
                {
                    "message": "customer_id query parameter is required"
                }
            )

        return get_customer_orders(customer_id)

    # ------------------------------------------------------------
    # PUT /orders/{orderId}
    # ------------------------------------------------------------

    if method == "PUT" and order_id:

        try:

            body = event.get("body")

            if isinstance(body, str):
                body = json.loads(body)

            return update_order(order_id, body)

        except json.JSONDecodeError:

            return response(
                400,
                {
                    "message": "Invalid JSON body"
                }
            )

    # ------------------------------------------------------------
    # POST /orders/{orderId}/cancel
    # ------------------------------------------------------------

    if method == "POST" and order_id:

        if event.get("path", "").endswith("/cancel"):

            return cancel_order(order_id)

    return response(
        404,
        {
            "message": "Route not found"
        }
    )
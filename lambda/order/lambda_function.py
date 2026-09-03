import os
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import pymysql


# ================================================================
# CONFIGURATION
# ================================================================

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

ORDER_QUEUE_URL = os.environ["ORDER_QUEUE_URL"]

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

sqs = boto3.client("sqs")
cloudwatch = boto3.client("cloudwatch")


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
# DATABASE INITIALIZATION
# ================================================================

def ensure_order_tables(connection):

    with connection.cursor() as cursor:

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id VARCHAR(50) PRIMARY KEY,
                customer_id VARCHAR(100) NOT NULL,
                status VARCHAR(30) NOT NULL,
                total_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS order_items (
                order_item_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                order_id VARCHAR(50) NOT NULL,
                product_id INT NOT NULL,
                quantity INT NOT NULL,
                price DECIMAL(12,2) NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL,
                CONSTRAINT fk_order_items_order
                    FOREIGN KEY (order_id)
                    REFERENCES orders(order_id)
                    ON DELETE CASCADE
            )
            """
        )

    connection.commit()


# ================================================================
# CLOUDWATCH METRICS
# ================================================================

def put_metric(metric_name, value=1):

    try:

        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": metric_name,
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
                    "Value": value,
                    "Unit": "Count",
                }
            ],
        )

    except Exception as exc:
        print(f"Metric publishing failed: {type(exc).__name__}")


# ================================================================
# RESPONSE
# ================================================================

def response(status_code, body):

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(body, default=str),
    }


# ================================================================
# REQUEST BODY
# ================================================================

def get_body(event):

    body = event.get("body")

    if body is None:
        return {}

    if isinstance(body, str):

        try:
            return json.loads(body)

        except json.JSONDecodeError:
            return None

    if isinstance(body, dict):
        return body

    return None


# ================================================================
# CREATE ORDER
# ================================================================

def create_order(event):

    body = get_body(event)

    if body is None:
        return response(
            400,
            {
                "message": "Request body must be valid JSON"
            },
        )

    customer_id = body.get("customer_id")
    items = body.get("items")

    if not customer_id:
        return response(
            400,
            {
                "message": "customer_id is required"
            },
        )

    if not isinstance(items, list) or not items:
        return response(
            400,
            {
                "message": "items must be a non-empty list"
            },
        )

    connection = None

    try:

        order_id = f"ORD-{uuid.uuid4().hex[:12].upper()}"

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        print(
            json.dumps(
                {
                    "message": "Creating order",
                    "order_id": order_id,
                    "customer_id": customer_id,
                }
            )
        )

        connection = get_connection()

        ensure_order_tables(connection)

        total_amount = Decimal("0.00")

        validated_items = []

        with connection.cursor() as cursor:

            for item in items:

                product_id = item.get("product_id")
                quantity = item.get("quantity")

                if product_id is None:
                    raise ValueError("Each item requires product_id")

                if quantity is None:
                    raise ValueError("Each item requires quantity")

                try:
                    product_id = int(product_id)
                    quantity = int(quantity)
                except (TypeError, ValueError):
                    raise ValueError(
                        "product_id and quantity must be numbers"
                    )

                if quantity <= 0:
                    raise ValueError(
                        "quantity must be greater than zero"
                    )

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        price,
                        status
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

                total_amount += price * quantity

                validated_items.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "price": price,
                    }
                )

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

            for item in validated_items:

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

        connection.commit()

        print("Order successfully stored in RDS")

        print("Sending order to SQS")

        sqs.send_message(
            QueueUrl=ORDER_QUEUE_URL,
            MessageBody=json.dumps(
                {
                    "order_id": order_id,
                }
            ),
        )

        print("Order successfully sent to SQS")

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

        print(f"Order validation failed: {str(exc)}")

        put_metric("OrderRequests")

        return response(
            400,
            {
                "message": str(exc)
            },
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(
            f"Order creation failed: {type(exc).__name__}"
        )

        return response(
            500,
            {
                "message": "Unable to create order"
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# GET ORDER BY ID
# ================================================================

def get_order(order_id):

    connection = None

    try:

        connection = get_connection()

        ensure_order_tables(connection)

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
                        "message": "Order not found"
                    },
                )

            cursor.execute(
                """
                SELECT
                    product_id,
                    quantity,
                    price
                FROM order_items
                WHERE order_id = %s
                ORDER BY order_item_id
                """,
                (order_id,),
            )

            order["items"] = cursor.fetchall()

        return response(
            200,
            order,
        )

    except Exception as exc:

        print(
            f"Get order failed: {type(exc).__name__}"
        )

        return response(
            500,
            {
                "message": "Unable to retrieve order"
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# GET ORDERS
# ================================================================

def get_orders(event):

    customer_id = None

    query_params = event.get("queryStringParameters")

    if query_params:
        customer_id = query_params.get("customer_id")

    connection = None

    try:

        connection = get_connection()

        ensure_order_tables(connection)

        with connection.cursor() as cursor:

            if customer_id:

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

            else:

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
                    ORDER BY created_at DESC
                    """
                )

            orders = cursor.fetchall()

        return response(
            200,
            {
                "orders": orders
            },
        )

    except Exception as exc:

        print(
            f"Get orders failed: {type(exc).__name__}"
        )

        return response(
            500,
            {
                "message": "Unable to retrieve orders"
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# UPDATE ORDER
# ================================================================

def update_order(order_id, event):

    body = get_body(event)

    if body is None:
        return response(
            400,
            {
                "message": "Request body must be valid JSON"
            },
        )

    status = body.get("status")

    allowed_statuses = {
        "PROCESSING",
        "CONFIRMED",
        "FAILED",
        "CANCELLED",
    }

    if status not in allowed_statuses:

        return response(
            400,
            {
                "message": "Invalid order status"
            },
        )

    connection = None

    try:

        connection = get_connection()

        ensure_order_tables(connection)

        now = datetime.now(timezone.utc).replace(tzinfo=None)

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

            if cursor.rowcount == 0:

                connection.rollback()

                return response(
                    404,
                    {
                        "message": "Order not found"
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

        print(
            f"Update order failed: {type(exc).__name__}"
        )

        return response(
            500,
            {
                "message": "Unable to update order"
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

        ensure_order_tables(connection)

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT status
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
                        "message": "Order not found"
                    },
                )

            if order["status"] != "PROCESSING":

                return response(
                    400,
                    {
                        "message": "Only processing orders can be cancelled"
                    },
                )

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = %s,
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    "CANCELLED",
                    now,
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

        print(
            f"Cancel order failed: {type(exc).__name__}"
        )

        return response(
            500,
            {
                "message": "Unable to cancel order"
            },
        )

    finally:

        if connection:
            connection.close()


# ================================================================
# MAIN HANDLER
# ================================================================

def lambda_handler(event, context):

    print(
        json.dumps(
            {
                "http_method": event.get("httpMethod"),
                "path": event.get("path"),
            }
        )
    )

    method = event.get("httpMethod", "GET").upper()

    path = event.get("path", "")

    path_parameters = event.get("pathParameters") or {}

    order_id = path_parameters.get("orderId")

    try:

        if method == "POST" and path.endswith("/cancel"):

            if not order_id:
                return response(
                    400,
                    {
                        "message": "orderId is required"
                    },
                )

            return cancel_order(order_id)

        if method == "POST":

            return create_order(event)

        if method == "GET" and order_id:

            return get_order(order_id)

        if method == "GET":

            return get_orders(event)

        if method == "PUT" and order_id:

            return update_order(order_id, event)

        return response(
            405,
            {
                "message": "Method not allowed"
            },
        )

    except Exception as exc:

        print(
            f"Unhandled order Lambda error: {type(exc).__name__}"
        )

        return response(
            500,
            {
                "message": "Internal server error"
            },
        )
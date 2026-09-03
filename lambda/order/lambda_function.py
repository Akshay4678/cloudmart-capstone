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
# REQUEST BODY
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
            raise ValueError(
                "Invalid base64 request body"
            ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc


# ================================================================
# CREATE ORDER
# ================================================================

def create_order(body):

    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")

    customer_id = body.get("customer_id")
    items = body.get("items")

    # ------------------------------------------------------------
    # CUSTOMER VALIDATION
    # ------------------------------------------------------------

    if not customer_id:
        raise ValueError("customer_id is required")

    customer_id = str(customer_id).strip()

    if not customer_id:
        raise ValueError("customer_id cannot be empty")

    # ------------------------------------------------------------
    # ITEM VALIDATION
    # ------------------------------------------------------------

    if not isinstance(items, list):
        raise ValueError("items must be an array")

    if len(items) == 0:
        raise ValueError("items must contain at least one item")

    if len(items) > 100:
        raise ValueError("Too many order items")

    # ------------------------------------------------------------
    # NORMALIZE ITEMS
    #
    # If the same product is supplied twice, combine quantities.
    # This prevents composite-PK conflicts in order_items.
    # ------------------------------------------------------------

    item_map = {}

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

        item_map[product_id] = (
            item_map.get(product_id, 0) + quantity
        )

    normalized_items = [
        {
            "product_id": product_id,
            "quantity": quantity,
        }
        for product_id, quantity in sorted(item_map.items())
    ]

    order_id = "ORD-" + uuid.uuid4().hex[:12].upper()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

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
                    "message": "Customer does not exist",
                    "customer_id": customer_id,
                },
            )

        # --------------------------------------------------------
        # READ PRODUCTS AND CHECK STOCK
        # --------------------------------------------------------

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
                        name,
                        description,
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

                # ------------------------------------------------
                # IMPORTANT:
                # Do not create an order when stock is 0 or
                # insufficient.
                # ------------------------------------------------

                if stock_count <= 0:
                    connection.rollback()

                    return response(
                        409,
                        {
                            "message": "Product is out of stock",
                            "product_id": product_id,
                            "product_name": product["name"],
                            "available_stock": 0,
                        },
                    )

                if quantity > stock_count:
                    connection.rollback()

                    return response(
                        409,
                        {
                            "message": "Insufficient stock",
                            "product_id": product_id,
                            "product_name": product["name"],
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

                total_amount += price * quantity

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
                        item["product_id"],
                        item["quantity"],
                        item["price"],
                        now,
                    ),
                )

        # --------------------------------------------------------
        # COMMIT ORDER
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

        print(
            f"Order {order_id} sent to SQS successfully"
        )

        put_metric("OrdersCreated")
        put_metric("OrderRequests")

        # --------------------------------------------------------
        # RESPONSE
        # --------------------------------------------------------

        return response(
            202,
            {
                "message": "Order accepted for processing",
                "order_id": order_id,
                "customer_id": customer_id,
                "status": "PROCESSING",
                "total_amount": str(total_amount),
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

        print(
            f"Order creation error: "
            f"{type(exc).__name__}: {exc}"
        )

        put_metric("OrdersFailed")
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

            order["items"] = cursor.fetchall()

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
# GET CUSTOMER ORDERS
# ================================================================

def get_customer_orders(customer_id):

    connection = None

    try:
        connection = get_connection()

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
                    "message": "Customer not found",
                    "customer_id": customer_id,
                },
            )

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

        orders = {}

        for row in rows:

            order_id = row["order_id"]

            if order_id not in orders:

                orders[order_id] = {
                    "order_id": order_id,
                    "customer_id": row["customer_id"],
                    "status": row["status"],
                    "total_amount": row["total_amount"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "items": [],
                }

            orders[order_id]["items"].append(
                {
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "product_description": row[
                        "product_description"
                    ],
                    "quantity": row["quantity"],
                    "price": row["item_price"],
                }
            )

        return response(
            200,
            {
                "customer": customer,
                "count": len(orders),
                "orders": list(orders.values()),
            },
        )

    except Exception as exc:

        print(
            f"Get customer orders error: {exc}"
        )

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
                "allowed_statuses": allowed_statuses,
            },
        )

    connection = None

    try:

        connection = get_connection()

        now = datetime.now(timezone.utc).replace(
            tzinfo=None
        )

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
                        "message": "Order not found",
                    },
                )

            if order["status"] != "PROCESSING":

                connection.rollback()

                return response(
                    400,
                    {
                        "message": (
                            "Order cannot be cancelled "
                            "because it is no longer "
                            "processing"
                        ),
                        "current_status": order["status"],
                    },
                )

            now = datetime.now(
                timezone.utc
            ).replace(tzinfo=None)

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
        "Order Lambda request: "
        f"{event.get('httpMethod', '')} "
        f"{event.get('path', '')}"
    )

    method = (
        event.get("httpMethod", "")
        .upper()
    )

    path = event.get("path", "")

    path_parameters = (
        event.get("pathParameters")
        or {}
    )

    query_parameters = (
        event.get("queryStringParameters")
        or {}
    )

    order_id = path_parameters.get("orderId")

    # ============================================================
    # POST /orders
    # ============================================================

    if (
        method == "POST"
        and not order_id
    ):

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
    # POST /orders/{orderId}/cancel
    # ============================================================

    if (
        method == "POST"
        and order_id
        and path.endswith("/cancel")
    ):

        return cancel_order(order_id)

    # ============================================================
    # GET /orders/{orderId}
    # ============================================================

    if (
        method == "GET"
        and order_id
    ):

        return get_order(order_id)

    # ============================================================
    # GET /orders?customer_id=CUST101
    # ============================================================

    if method == "GET":

        customer_id = query_parameters.get(
            "customer_id"
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
                        "customer_id cannot be empty"
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

            body = parse_request_body(event)

            return update_order(
                order_id,
                body,
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
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import pymysql


# ================================================================
# AWS CLIENTS
# ================================================================

events = boto3.client("events")
cloudwatch = boto3.client("cloudwatch")


# ================================================================
# ENVIRONMENT
# ================================================================

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")


# ================================================================
# DATABASE
# ================================================================

def get_connection():

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=5,
        read_timeout=20,
        write_timeout=20,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )


# ================================================================
# METRICS
# ================================================================

def put_metric(metric_name, service="OrderProcessing"):

    try:

        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [
                        {
                            "Name": "Environment",
                            "Value": ENVIRONMENT
                        },
                        {
                            "Name": "Service",
                            "Value": service
                        }
                    ]
                }
            ]
        )

    except Exception as exc:

        print(f"Metric error: {exc}")


# ================================================================
# EVENTBRIDGE
# ================================================================

def publish_event(detail_type, detail):

    events.put_events(
        Entries=[
            {
                "Source": "cloudmart.orders",
                "DetailType": detail_type,
                "Detail": json.dumps(detail, default=str),
                "EventBusName": "default"
            }
        ]
    )


# ================================================================
# INITIALIZE TABLES
# ================================================================

def initialize_tables(connection):

    with connection.cursor() as cursor:

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id VARCHAR(50) PRIMARY KEY,
                customer_id VARCHAR(100) NOT NULL,
                status VARCHAR(30) NOT NULL,
                total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

        cursor.execute(
            """
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
        )

    connection.commit()


# ================================================================
# PROCESS ORDER
# ================================================================

def process_order(order_id):

    connection = None

    try:

        connection = get_connection()

        initialize_tables(connection)

        # --------------------------------------------------------
        # Read order
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    order_id,
                    customer_id,
                    status,
                    total_amount
                FROM orders
                WHERE order_id = %s
                FOR UPDATE
                """,
                (order_id,)
            )

            order = cursor.fetchone()

        if not order:

            raise ValueError(
                f"Order {order_id} does not exist"
            )

        # --------------------------------------------------------
        # Idempotency
        # --------------------------------------------------------

        if order["status"] == "CONFIRMED":

            print(
                f"Order {order_id} is already confirmed"
            )

            connection.commit()

            return

        if order["status"] == "CANCELLED":

            print(
                f"Order {order_id} was cancelled"
            )

            connection.commit()

            return

        # --------------------------------------------------------
        # Read order items
        # --------------------------------------------------------

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    product_id,
                    quantity
                FROM order_items
                WHERE order_id = %s
                """,
                (order_id,)
            )

            items = cursor.fetchall()

        if not items:

            raise ValueError(
                f"Order {order_id} contains no items"
            )

        calculated_total = Decimal("0.00")

        # --------------------------------------------------------
        # Process every product
        # --------------------------------------------------------

        for item in items:

            product_id = int(item["product_id"])
            quantity = int(item["quantity"])

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    SELECT
                        id,
                        price,
                        stock_count,
                        active
                    FROM products
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (product_id,)
                )

                product = cursor.fetchone()

            if not product:

                raise ValueError(
                    f"Product {product_id} does not exist"
                )

            if not product["active"]:

                raise ValueError(
                    f"Product {product_id} is inactive"
                )

            stock_count = int(product["stock_count"])

            if stock_count < quantity:

                raise ValueError(
                    f"Insufficient stock for product {product_id}"
                )

            price = Decimal(str(product["price"]))

            calculated_total += (
                price * quantity
            )

            # ----------------------------------------------------
            # Reduce product stock
            # ----------------------------------------------------

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    UPDATE products
                    SET
                        stock_count = stock_count - %s,
                        updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        quantity,
                        datetime.now(
                            timezone.utc
                        ).replace(tzinfo=None),
                        product_id
                    )
                )

            # ----------------------------------------------------
            # Update inventory
            # ----------------------------------------------------

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    SELECT product_id
                    FROM inventory
                    WHERE product_id = %s
                    FOR UPDATE
                    """,
                    (product_id,)
                )

                inventory = cursor.fetchone()

            if inventory:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        UPDATE inventory
                        SET
                            quantity = quantity - %s,
                            updated_at = %s
                        WHERE product_id = %s
                        """,
                        (
                            quantity,
                            datetime.now(
                                timezone.utc
                            ).replace(tzinfo=None),
                            product_id
                        )
                    )

        # --------------------------------------------------------
        # Update order
        # --------------------------------------------------------

        now = datetime.now(
            timezone.utc
        ).replace(tzinfo=None)

        with connection.cursor() as cursor:

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = 'CONFIRMED',
                    total_amount = %s,
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    calculated_total,
                    now,
                    order_id
                )
            )

        connection.commit()

        # --------------------------------------------------------
        # Publish success event
        # --------------------------------------------------------

        publish_event(
            "OrderProcessed",
            {
                "order_id": order_id,
                "customer_id": order["customer_id"],
                "status": "CONFIRMED",
                "total_amount": str(calculated_total)
            }
        )

        put_metric("OrdersProcessed")

        print(
            f"Order {order_id} processed successfully"
        )

    except Exception as exc:

        if connection:

            connection.rollback()

        print(
            f"Order processing failed for "
            f"{order_id}: {exc}"
        )

        # --------------------------------------------------------
        # Try to mark order FAILED
        # --------------------------------------------------------

        try:

            if connection:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        UPDATE orders
                        SET
                            status = 'FAILED',
                            updated_at = %s
                        WHERE order_id = %s
                          AND status = 'PROCESSING'
                        """,
                        (
                            datetime.now(
                                timezone.utc
                            ).replace(tzinfo=None),
                            order_id
                        )
                    )

                connection.commit()

            publish_event(
                "OrderFailed",
                {
                    "order_id": order_id,
                    "status": "FAILED",
                    "reason": str(exc)
                }
            )

        except Exception as event_error:

            print(
                f"Failed to publish failure event: "
                f"{event_error}"
            )

        put_metric("OrdersProcessingFailures")

        raise

    finally:

        if connection:

            connection.close()


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    print(
        f"Order Processor received event: "
        f"{json.dumps(event, default=str)}"
    )

    records = event.get("Records", [])

    if not records:

        return {
            "statusCode": 200,
            "body": "No SQS records"
        }

    for record in records:

        body = record.get("body", "{}")

        message = json.loads(body)

        order_id = message.get("order_id")

        if not order_id:

            raise ValueError(
                "order_id missing from SQS message"
            )

        process_order(order_id)

    return {
        "statusCode": 200,
        "body": "Orders processed"
    }
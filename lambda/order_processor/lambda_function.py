import os
import json
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

ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

events_client = boto3.client("events")
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
# CLOUDWATCH METRICS
# ================================================================

def put_metric(metric_name, value=1, service="OrderProcessing"):

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
                            "Value": service,
                        },
                    ],
                    "Value": value,
                    "Unit": "Count",
                }
            ],
        )

    except Exception as exc:

        print(
            f"Metric publishing failed: {type(exc).__name__}"
        )


# ================================================================
# EVENTBRIDGE
# ================================================================

def publish_order_event(
    detail_type,
    order_id,
    customer_id,
    status,
    total_amount,
    reason=None,
):

    detail = {
        "order_id": order_id,
        "customer_id": customer_id,
        "status": status,
        "total_amount": float(total_amount),
    }

    if reason:
        detail["reason"] = reason

    events_client.put_events(
        Entries=[
            {
                "Source": "cloudmart.orders",
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
                "EventBusName": "default",
            }
        ]
    )


# ================================================================
# ORDER TABLES
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
# PROCESS ORDER
# ================================================================

def process_order(order_id):

    connection = None

    try:

        connection = get_connection()

        ensure_order_tables(connection)

        with connection.cursor() as cursor:

            # ----------------------------------------------------
            # GET ORDER
            # ----------------------------------------------------

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
                (order_id,),
            )

            order = cursor.fetchone()

            if not order:

                raise ValueError(
                    f"Order {order_id} not found"
                )

            # ----------------------------------------------------
            # IDEMPOTENCY
            # ----------------------------------------------------

            if order["status"] == "CONFIRMED":

                print(
                    f"Order {order_id} is already confirmed"
                )

                connection.rollback()

                return

            if order["status"] == "CANCELLED":

                print(
                    f"Order {order_id} was cancelled"
                )

                connection.rollback()

                return

            # ----------------------------------------------------
            # GET ORDER ITEMS
            # ----------------------------------------------------

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

            items = cursor.fetchall()

            if not items:

                raise ValueError(
                    f"Order {order_id} contains no items"
                )

            total_amount = Decimal("0.00")

            # ----------------------------------------------------
            # PROCESS EACH PRODUCT
            # ----------------------------------------------------

            for item in items:

                product_id = item["product_id"]
                quantity = int(item["quantity"])

                # ----------------------------------------------
                # LOCK PRODUCT
                # ----------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        price,
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
                        f"Product {product_id} not found"
                    )

                if product["status"] != "Available":

                    raise ValueError(
                        f"Product {product_id} is unavailable"
                    )

                price = Decimal(
                    str(product["price"])
                )

                # ----------------------------------------------
                # LOCK INVENTORY
                # ----------------------------------------------

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        quantity
                    FROM inventory
                    WHERE product_id = %s
                    FOR UPDATE
                    """,
                    (product_id,),
                )

                inventory = cursor.fetchone()

                if not inventory:

                    raise ValueError(
                        f"Inventory not found for product {product_id}"
                    )

                available_quantity = int(
                    inventory["quantity"]
                )

                if available_quantity < quantity:

                    raise ValueError(
                        f"Insufficient inventory for product {product_id}"
                    )

                new_quantity = (
                    available_quantity - quantity
                )

                # ----------------------------------------------
                # UPDATE INVENTORY
                # ----------------------------------------------

                cursor.execute(
                    """
                    UPDATE inventory
                    SET
                        quantity = %s,
                        updated_at = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_quantity,
                        datetime.now(
                            timezone.utc
                        ).replace(tzinfo=None),
                        product_id,
                    ),
                )

                # ----------------------------------------------
                # UPDATE PRODUCT STOCK
                # ----------------------------------------------

                cursor.execute(
                    """
                    UPDATE products
                    SET
                        stock_count = %s,
                        updated_at = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_quantity,
                        datetime.now(
                            timezone.utc
                        ).replace(tzinfo=None),
                        product_id,
                    ),
                )

                # ----------------------------------------------
                # LOW STOCK EVENT
                # ----------------------------------------------

                if new_quantity <= 5:

                    try:

                        events_client.put_events(
                            Entries=[
                                {
                                    "Source": "cloudmart.inventory",
                                    "DetailType": "InventoryChanged",
                                    "Detail": json.dumps(
                                        {
                                            "product_id": product_id,
                                            "stock_count": new_quantity,
                                        }
                                    ),
                                    "EventBusName": "default",
                                }
                            ]
                        )

                        put_metric(
                            "LowStockEvents",
                            service="OrderProcessing",
                        )

                    except Exception as exc:

                        print(
                            "Low stock event failed: "
                            f"{type(exc).__name__}"
                        )

                total_amount += (
                    price * quantity
                )

            # ----------------------------------------------------
            # UPDATE ORDER
            # ----------------------------------------------------

            now = datetime.now(
                timezone.utc
            ).replace(tzinfo=None)

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = %s,
                    total_amount = %s,
                    updated_at = %s
                WHERE order_id = %s
                """,
                (
                    "CONFIRMED",
                    total_amount,
                    now,
                    order_id,
                ),
            )

        connection.commit()

        # --------------------------------------------------------
        # SUCCESS EVENT
        # --------------------------------------------------------

        publish_order_event(
            detail_type="OrderProcessed",
            order_id=order["order_id"],
            customer_id=order["customer_id"],
            status="CONFIRMED",
            total_amount=total_amount,
        )

        put_metric(
            "OrdersProcessed",
            service="OrderProcessing",
        )

        print(
            json.dumps(
                {
                    "message": "Order processed successfully",
                    "order_id": order_id,
                    "status": "CONFIRMED",
                    "total_amount": float(total_amount),
                }
            )
        )

    except Exception as exc:

        if connection:

            try:
                connection.rollback()
            except Exception:
                pass

        print(
            f"Order processing failed: {type(exc).__name__}"
        )

        put_metric(
            "OrdersProcessingFailures",
            service="OrderProcessing",
        )

        # --------------------------------------------------------
        # MARK ORDER FAILED
        # --------------------------------------------------------

        if connection:

            try:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        SELECT
                            customer_id,
                            total_amount
                        FROM orders
                        WHERE order_id = %s
                        """,
                        (order_id,),
                    )

                    order = cursor.fetchone()

                    if order:

                        cursor.execute(
                            """
                            UPDATE orders
                            SET
                                status = %s,
                                updated_at = %s
                            WHERE order_id = %s
                            """,
                            (
                                "FAILED",
                                datetime.now(
                                    timezone.utc
                                ).replace(tzinfo=None),
                                order_id,
                            ),
                        )

                        connection.commit()

                        try:

                            publish_order_event(
                                detail_type="OrderFailed",
                                order_id=order_id,
                                customer_id=order[
                                    "customer_id"
                                ],
                                status="FAILED",
                                total_amount=order[
                                    "total_amount"
                                ],
                                reason=str(exc),
                            )

                        except Exception as event_exc:

                            print(
                                "Failed to publish "
                                f"OrderFailed event: "
                                f"{type(event_exc).__name__}"
                            )

            except Exception as update_exc:

                print(
                    "Failed to update order status: "
                    f"{type(update_exc).__name__}"
                )

        # Raise so SQS can retry and eventually move
        # the message to the DLQ.

        raise

    finally:

        if connection:

            connection.close()


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    print(
        json.dumps(
            {
                "message": "Order Processor invoked",
                "record_count": len(
                    event.get("Records", [])
                ),
            }
        )
    )

    records = event.get("Records", [])

    if not records:

        print("No SQS records received")

        return {
            "statusCode": 200,
            "body": "No records"
        }

    for record in records:

        body = record.get("body", "{}")

        try:

            message = json.loads(body)

        except json.JSONDecodeError:

            print("Invalid SQS message JSON")

            raise

        order_id = message.get("order_id")

        if not order_id:

            print("SQS message does not contain order_id")

            raise ValueError(
                "order_id is required"
            )

        print(
            f"Processing order {order_id}"
        )

        process_order(order_id)

    return {
        "statusCode": 200,
        "body": "Order processing completed"
    }
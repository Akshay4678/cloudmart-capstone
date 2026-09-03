import json
import os
from datetime import datetime, timezone

import boto3
import pymysql


# ================================================================
# AWS CLIENTS
# ================================================================

events = boto3.client("events")
cloudwatch = boto3.client("cloudwatch")


# ================================================================
# ENVIRONMENT VARIABLES
# ================================================================

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
                            "Value": "OrderProcessing",
                        },
                    ],
                }
            ],
        )

    except Exception as exc:

        print(
            f"CloudWatch metric error: {exc}"
        )


# ================================================================
# CURRENT TIME
# ================================================================

def current_time():

    return datetime.now(
        timezone.utc
    ).replace(tzinfo=None)


# ================================================================
# PUBLISH EVENTBRIDGE EVENT
# ================================================================

def publish_order_event(
    order_id,
    customer_id,
    status,
    total_amount=None,
    reason=None,
):

    detail = {
        "order_id": order_id,
        "customer_id": customer_id,
        "status": status,
    }

    if total_amount is not None:
        detail["total_amount"] = str(
            total_amount
        )

    if reason is not None:
        detail["reason"] = reason

    detail_type = (
        "OrderProcessed"
        if status == "CONFIRMED"
        else "OrderFailed"
    )

    print(
        "Publishing EventBridge event: "
        f"{detail_type}"
    )

    try:

        result = events.put_events(
            Entries=[
                {
                    "Source": "cloudmart.orders",
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail),
                    "EventBusName": "default",
                }
            ]
        )

        print(
            "EventBridge response:",
            json.dumps(
                result,
                default=str
            ),
        )

        if result.get(
            "FailedEntryCount",
            0
        ) > 0:

            raise RuntimeError(
                "EventBridge failed to publish "
                "order event"
            )

    except Exception as exc:

        print(
            "EventBridge publish error: "
            f"{type(exc).__name__}: {exc}"
        )

        # EventBridge failure is treated as a real
        # processing failure so SQS can retry.
        raise


# ================================================================
# EXTRACT SQS MESSAGE
# ================================================================

def extract_order_message(record):

    body = record.get("body")

    if body is None:

        raise ValueError(
            "SQS record does not contain body"
        )

    if isinstance(body, dict):

        message = body

    else:

        try:

            message = json.loads(body)

        except json.JSONDecodeError as exc:

            raise ValueError(
                "SQS message body is not valid JSON"
            ) from exc

    # ------------------------------------------------------------
    # Support an SNS-style envelope too.
    # This makes the processor more tolerant if the message
    # passes through another AWS service.
    # ------------------------------------------------------------

    if isinstance(message, dict):

        if "Message" in message:

            nested = message["Message"]

            if isinstance(nested, str):

                try:
                    message = json.loads(nested)
                except json.JSONDecodeError:
                    pass

            elif isinstance(nested, dict):

                message = nested

    if not isinstance(message, dict):

        raise ValueError(
            "Order message must be a JSON object"
        )

    return message


# ================================================================
# MARK ORDER FAILED
# ================================================================

def mark_order_failed(
    order_id,
    reason,
    customer_id=None,
    total_amount=None,
):

    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            # ----------------------------------------------------
            # Only change PROCESSING orders.
            # Do not overwrite CONFIRMED or CANCELLED.
            # ----------------------------------------------------

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
                    current_time(),
                    order_id,
                ),
            )

        connection.commit()

        print(
            f"Order {order_id} marked FAILED"
        )

    except Exception as exc:

        if connection:
            connection.rollback()

        print(
            "Could not mark order FAILED: "
            f"{type(exc).__name__}: {exc}"
        )

        raise

    finally:

        if connection:
            connection.close()

    put_metric(
        "OrderProcessingFailures"
    )

    # ------------------------------------------------------------
    # Publish OrderFailed event.
    # ------------------------------------------------------------

    try:

        publish_order_event(
            order_id=order_id,
            customer_id=customer_id,
            status="FAILED",
            total_amount=total_amount,
            reason=reason,
        )

    except Exception as exc:

        print(
            "WARNING: Order failed event "
            f"could not be published: {exc}"
        )

        # We have already recorded FAILED in RDS.
        # Do not keep retrying an order merely because
        # notification failed.
        #
        # The business operation itself is already complete.


# ================================================================
# PROCESS ONE ORDER
# ================================================================

def process_order(message):

    order_id = message.get("order_id")

    if not order_id:

        raise ValueError(
            "SQS message is missing order_id"
        )

    order_id = str(order_id)

    print(
        f"=================================================="
    )

    print(
        f"START PROCESSING ORDER: {order_id}"
    )

    connection = None

    try:

        connection = get_connection()

        # ========================================================
        # START TRANSACTION
        # ========================================================

        with connection.cursor() as cursor:

            # ----------------------------------------------------
            # LOCK ORDER ROW
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
                    f"Order {order_id} does not exist"
                )

            customer_id = order[
                "customer_id"
            ]

            total_amount = order[
                "total_amount"
            ]

            current_status = order[
                "status"
            ]

            print(
                f"Order status before processing: "
                f"{current_status}"
            )

            # ----------------------------------------------------
            # Already CONFIRMED
            #
            # This protects against duplicate SQS delivery.
            # ----------------------------------------------------

            if current_status == "CONFIRMED":

                connection.rollback()

                print(
                    f"Order {order_id} is already CONFIRMED"
                )

                return {
                    "order_id": order_id,
                    "status": "CONFIRMED",
                    "message": "Already processed",
                }

            # ----------------------------------------------------
            # CANCELLED
            #
            # Do not deduct stock for cancelled orders.
            # ----------------------------------------------------

            if current_status == "CANCELLED":

                connection.rollback()

                print(
                    f"Order {order_id} was cancelled"
                )

                return {
                    "order_id": order_id,
                    "status": "CANCELLED",
                    "message": "Order was cancelled",
                }

            # ----------------------------------------------------
            # FAILED
            #
            # Do not process the same failed order again.
            # ----------------------------------------------------

            if current_status == "FAILED":

                connection.rollback()

                print(
                    f"Order {order_id} is already FAILED"
                )

                return {
                    "order_id": order_id,
                    "status": "FAILED",
                    "message": "Order already failed",
                }

            # ----------------------------------------------------
            # Only PROCESSING orders should continue.
            # ----------------------------------------------------

            if current_status != "PROCESSING":

                raise ValueError(
                    f"Order {order_id} has unsupported "
                    f"status {current_status}"
                )

            # ----------------------------------------------------
            # READ ORDER ITEMS FROM order_items
            #
            # IMPORTANT:
            # product_id and quantity are NOT columns in orders.
            # They are in order_items.
            # ----------------------------------------------------

            cursor.execute(
                """
                SELECT
                    oi.order_id,
                    oi.product_id,
                    oi.quantity,
                    oi.price,
                    p.name AS product_name,
                    p.stock_count
                FROM order_items oi
                INNER JOIN products p
                    ON oi.product_id = p.product_id
                WHERE oi.order_id = %s
                ORDER BY oi.product_id
                FOR UPDATE
                """,
                (order_id,),
            )

            items = cursor.fetchall()

            if not items:

                raise ValueError(
                    f"Order {order_id} has no order items"
                )

            print(
                f"Order {order_id} contains "
                f"{len(items)} item(s)"
            )

            # ====================================================
            # LOCK PRODUCTS IN CONSISTENT ORDER
            #
            # This protects against race conditions when two
            # orders try to purchase the same product.
            # ====================================================

            product_ids = sorted(
                int(item["product_id"])
                for item in items
            )

            locked_products = {}

            for product_id in product_ids:

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        name,
                        stock_count
                    FROM products
                    WHERE product_id = %s
                    FOR UPDATE
                    """,
                    (product_id,),
                )

                product = cursor.fetchone()

                if not product:

                    raise ValueError(
                        f"Product {product_id} does not exist"
                    )

                locked_products[
                    product_id
                ] = product

            # ====================================================
            # CHECK ALL STOCK BEFORE CHANGING ANY STOCK
            # ====================================================

            for item in items:

                product_id = int(
                    item["product_id"]
                )

                quantity = int(
                    item["quantity"]
                )

                product = locked_products[
                    product_id
                ]

                stock_count = int(
                    product["stock_count"]
                )

                print(
                    f"Product {product_id} "
                    f"({product['name']}): "
                    f"stock={stock_count}, "
                    f"requested={quantity}"
                )

                # ------------------------------------------------
                # ZERO STOCK
                # ------------------------------------------------

                if stock_count <= 0:

                    raise InventoryError(
                        product_id=product_id,
                        product_name=product["name"],
                        requested=quantity,
                        available=0,
                    )

                # ------------------------------------------------
                # INSUFFICIENT STOCK
                # ------------------------------------------------

                if quantity > stock_count:

                    raise InventoryError(
                        product_id=product_id,
                        product_name=product["name"],
                        requested=quantity,
                        available=stock_count,
                    )

            # ====================================================
            # DEDUCT STOCK
            # ====================================================

            for item in items:

                product_id = int(
                    item["product_id"]
                )

                quantity = int(
                    item["quantity"]
                )

                product = locked_products[
                    product_id
                ]

                old_stock = int(
                    product["stock_count"]
                )

                new_stock = (
                    old_stock - quantity
                )

                # ------------------------------------------------
                # PRODUCT STOCK
                # ------------------------------------------------

                cursor.execute(
                    """
                    UPDATE products
                    SET
                        stock_count = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_stock,
                        product_id,
                    ),
                )

                if cursor.rowcount != 1:

                    raise RuntimeError(
                        f"Failed to update stock "
                        f"for product {product_id}"
                    )

                # ------------------------------------------------
                # INVENTORY TABLE
                #
                # Keep inventory.quantity synchronized with
                # products.stock_count.
                # ------------------------------------------------

                cursor.execute(
                    """
                    UPDATE inventory
                    SET
                        quantity = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_stock,
                        product_id,
                    ),
                )

                if cursor.rowcount == 0:

                    raise RuntimeError(
                        f"Inventory record missing "
                        f"for product {product_id}"
                    )

                print(
                    f"Stock updated for product "
                    f"{product_id}: "
                    f"{old_stock} -> {new_stock}"
                )

            # ====================================================
            # CONFIRM ORDER
            # ====================================================

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = 'CONFIRMED',
                    updated_at = %s
                WHERE order_id = %s
                  AND status = 'PROCESSING'
                """,
                (
                    current_time(),
                    order_id,
                ),
            )

            if cursor.rowcount != 1:

                raise RuntimeError(
                    f"Could not confirm order {order_id}"
                )

        # ========================================================
        # COMMIT EVERYTHING
        #
        # Stock deduction + inventory update + order confirmation
        # happen together.
        # ========================================================

        connection.commit()

        print(
            f"ORDER {order_id} SUCCESSFULLY CONFIRMED"
        )

        put_metric(
            "OrdersProcessed"
        )

        # ========================================================
        # EVENTBRIDGE
        # ========================================================

        try:

            publish_order_event(
                order_id=order_id,
                customer_id=customer_id,
                status="CONFIRMED",
                total_amount=total_amount,
            )

        except Exception:

            # ----------------------------------------------------
            # IMPORTANT:
            #
            # The database transaction is already committed.
            # We must NOT return the SQS message to the queue,
            # otherwise the same order could be processed again.
            # ----------------------------------------------------

            print(
                "WARNING: Order confirmed but "
                "EventBridge notification failed"
            )

        return {
            "order_id": order_id,
            "status": "CONFIRMED",
        }

    except InventoryError as exc:

        if connection:
            connection.rollback()

        print(
            f"INSUFFICIENT INVENTORY FOR ORDER "
            f"{order_id}: {exc}"
        )

        reason = (
            f"Insufficient stock for product "
            f"{exc.product_id}. "
            f"Requested: {exc.requested}, "
            f"Available: {exc.available}"
        )

        mark_order_failed(
            order_id=order_id,
            reason=reason,
            customer_id=(
                message.get("customer_id")
            ),
            total_amount=(
                message.get("total_amount")
            ),
        )

        # --------------------------------------------------------
        # Return successfully so SQS removes the message.
        #
        # This is a BUSINESS failure, not a transient Lambda
        # failure. Retrying the same out-of-stock order would
        # just fail again.
        # --------------------------------------------------------

        return {
            "order_id": order_id,
            "status": "FAILED",
            "reason": reason,
        }

    except ValueError as exc:

        if connection:
            connection.rollback()

        print(
            f"ORDER VALIDATION FAILURE "
            f"{order_id}: {exc}"
        )

        mark_order_failed(
            order_id=order_id,
            reason=str(exc),
            customer_id=(
                message.get("customer_id")
            ),
            total_amount=(
                message.get("total_amount")
            ),
        )

        return {
            "order_id": order_id,
            "status": "FAILED",
            "reason": str(exc),
        }

    except Exception as exc:

        if connection:
            connection.rollback()

        print(
            f"ORDER PROCESSING ERROR "
            f"{order_id}: "
            f"{type(exc).__name__}: {exc}"
        )

        # --------------------------------------------------------
        # IMPORTANT:
        #
        # Do NOT mark transient errors as permanently failed here.
        #
        # Raising the exception causes Lambda/SQS to retry.
        # After the configured retry count, SQS moves the message
        # to the DLQ.
        # --------------------------------------------------------

        put_metric(
            "OrderProcessingFailures"
        )

        raise

    finally:

        if connection:

            connection.close()

            print(
                f"Database connection closed "
                f"for order {order_id}"
            )


# ================================================================
# INVENTORY ERROR
# ================================================================

class InventoryError(Exception):

    def __init__(
        self,
        product_id,
        product_name,
        requested,
        available,
    ):

        self.product_id = product_id
        self.product_name = product_name
        self.requested = requested
        self.available = available

        super().__init__(
            f"Insufficient stock for "
            f"product {product_id}"
        )


# ================================================================
# LAMBDA HANDLER
# ================================================================

def lambda_handler(event, context):

    print(
        "=================================================="
    )

    print(
        "ORDER PROCESSOR LAMBDA STARTED"
    )

    print(
        "SQS event received:",
        json.dumps(
            event,
            default=str
        ),
    )

    records = event.get(
        "Records",
        []
    )

    if not records:

        print(
            "No SQS records received"
        )

        return {
            "statusCode": 200,
            "message": "No records to process",
        }

    processed = 0
    failed = 0

    # ============================================================
    # PROCESS EACH SQS MESSAGE
    # ============================================================

    for record in records:

        order_id = "UNKNOWN"

        try:

            message = extract_order_message(
                record
            )

            order_id = str(
                message.get(
                    "order_id",
                    "UNKNOWN"
                )
            )

            print(
                f"Processing SQS order: "
                f"{order_id}"
            )

            result = process_order(
                message
            )

            print(
                "Order processing result:",
                json.dumps(
                    result,
                    default=str
                ),
            )

            processed += 1

        except Exception as exc:

            failed += 1

            print(
                f"FAILED TO PROCESS SQS MESSAGE "
                f"FOR ORDER {order_id}"
            )

            print(
                f"ERROR TYPE: "
                f"{type(exc).__name__}"
            )

            print(
                f"ERROR MESSAGE: {exc}"
            )

            # ----------------------------------------------------
            # CRITICAL:
            #
            # Raise the exception so Lambda reports failure to
            # SQS. The message will then be retried and eventually
            # moved to the DLQ if it continues failing.
            # ----------------------------------------------------

            raise

    print(
        "=================================================="
    )

    print(
        f"ORDER PROCESSOR COMPLETE. "
        f"Processed={processed}, "
        f"Failed={failed}"
    )

    print(
        "=================================================="
    )

    return {
        "statusCode": 200,
        "processed": processed,
        "failed": failed,
    }
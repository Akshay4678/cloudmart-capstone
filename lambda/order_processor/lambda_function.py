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
# JSON SERIALIZATION
# ================================================================

def json_default(value):

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)


# ================================================================
# AUDIT LOG
# ================================================================

def write_audit_log(
    cursor,
    entity_type,
    entity_id,
    action,
    old_value=None,
    new_value=None,
    performed_by=None,
):

    cursor.execute(
        """
        INSERT INTO audit_logs (
            entity_type,
            entity_id,
            action,
            old_value,
            new_value,
            performed_by,
            created_at
        )
        VALUES (
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
            (
                json.dumps(
                    old_value,
                    default=json_default
                )
                if old_value is not None
                else None
            ),
            (
                json.dumps(
                    new_value,
                    default=json_default
                )
                if new_value is not None
                else None
            ),
            performed_by,
            current_time(),
        ),
    )

    print(
        f"Audit log created: "
        f"{entity_type} {entity_id} {action}"
    )


# ================================================================
# ACTOR
# ================================================================

def get_performed_by(message):

    return (
        message.get("performed_by")
        or message.get("customer_id")
        or "order-processor"
    )


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
                default=json_default
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
    # Support SNS-style envelope
    # ------------------------------------------------------------

    if isinstance(message, dict):

        if "Message" in message:

            nested = message["Message"]

            if isinstance(nested, str):

                try:

                    message = json.loads(
                        nested
                    )

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
    performed_by=None,
):

    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            # ----------------------------------------------------
            # Read current order
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

            current_status = order[
                "status"
            ]

            # ----------------------------------------------------
            # Only PROCESSING orders become FAILED.
            # ----------------------------------------------------

            if current_status == "PROCESSING":

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

                if cursor.rowcount != 1:

                    raise RuntimeError(
                        f"Could not mark order "
                        f"{order_id} as FAILED"
                    )

                # ------------------------------------------------
                # ORDER FAILED AUDIT
                # ------------------------------------------------

                write_audit_log(
                    cursor=cursor,
                    entity_type="ORDER",
                    entity_id=order_id,
                    action="ORDER_FAILED",
                    old_value={
                        "status": "PROCESSING",
                        "total_amount": order[
                            "total_amount"
                        ],
                    },
                    new_value={
                        "status": "FAILED",
                        "reason": reason,
                    },
                    performed_by=(
                        performed_by
                        or customer_id
                        or "order-processor"
                    ),
                )

            else:

                print(
                    f"Order {order_id} is already "
                    f"{current_status}; "
                    f"not changing to FAILED"
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

        # Database already records FAILED.
        # Do not retry only because EventBridge failed.


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

    performed_by = get_performed_by(
        message
    )

    print(
        "=================================================="
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
            # ALREADY CONFIRMED
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
            # ONLY PROCESSING ORDERS CONTINUE
            # ----------------------------------------------------

            if current_status != "PROCESSING":

                raise ValueError(
                    f"Order {order_id} has unsupported "
                    f"status {current_status}"
                )

            # ----------------------------------------------------
            # READ ORDER ITEMS
            # ----------------------------------------------------

            cursor.execute(
                """
                SELECT
                    oi.order_id,
                    oi.product_id,
                    oi.quantity,
                    oi.price,
                    p.name AS product_name,
                    p.stock_count,
                    p.status AS product_status
                FROM order_items oi
                INNER JOIN products p
                    ON oi.product_id = p.product_id
                WHERE oi.order_id = %s
                ORDER BY oi.product_id
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
            # ====================================================

            product_ids = sorted(
                set(
                    int(item["product_id"])
                    for item in items
                )
            )

            locked_products = {}

            for product_id in product_ids:

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        name,
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
                        f"Product {product_id} does not exist"
                    )

                locked_products[
                    product_id
                ] = product

            # ====================================================
            # CHECK ALL STOCK BEFORE CHANGING ANYTHING
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

                if stock_count <= 0:

                    raise InventoryError(
                        product_id=product_id,
                        product_name=product["name"],
                        requested=quantity,
                        available=0,
                    )

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

                old_status = product[
                    "status"
                ]

                new_stock = (
                    old_stock - quantity
                )

                # ------------------------------------------------
                # PRODUCT STATUS
                #
                # Stock 0 -> INACTIVE
                # Stock > 0 -> ACTIVE
                # ------------------------------------------------

                new_status = (
                    "ACTIVE"
                    if new_stock > 0
                    else "INACTIVE"
                )

                # ------------------------------------------------
                # UPDATE PRODUCT
                # ------------------------------------------------

                cursor.execute(
                    """
                    UPDATE products
                    SET
                        stock_count = %s,
                        status = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_stock,
                        new_status,
                        product_id,
                    ),
                )

                if cursor.rowcount != 1:

                    raise RuntimeError(
                        f"Failed to update stock "
                        f"for product {product_id}"
                    )

                # ------------------------------------------------
                # UPDATE INVENTORY
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

                # =================================================
                # STOCK DECREASE AUDIT
                # =================================================

                write_audit_log(
                    cursor=cursor,
                    entity_type="PRODUCT",
                    entity_id=product_id,
                    action="STOCK_DECREASED",
                    old_value={
                        "stock_count": old_stock,
                        "status": old_status,
                    },
                    new_value={
                        "stock_count": new_stock,
                        "status": new_status,
                        "quantity_decreased": quantity,
                        "order_id": order_id,
                    },
                    performed_by=performed_by,
                )

                # =================================================
                # STATUS CHANGE AUDIT
                # =================================================

                if old_status != new_status:

                    write_audit_log(
                        cursor=cursor,
                        entity_type="PRODUCT",
                        entity_id=product_id,
                        action="STATUS_CHANGED",
                        old_value={
                            "status": old_status,
                            "stock_count": old_stock,
                        },
                        new_value={
                            "status": new_status,
                            "stock_count": new_stock,
                        },
                        performed_by=performed_by,
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

            # ====================================================
            # ORDER CONFIRMED AUDIT
            # ====================================================

            write_audit_log(
                cursor=cursor,
                entity_type="ORDER",
                entity_id=order_id,
                action="ORDER_CONFIRMED",
                old_value={
                    "status": "PROCESSING",
                    "total_amount": total_amount,
                },
                new_value={
                    "status": "CONFIRMED",
                    "total_amount": total_amount,
                },
                performed_by=performed_by,
            )

        # ========================================================
        # COMMIT EVERYTHING
        #
        # Product stock
        # Inventory
        # Product status
        # Stock audit
        # Status audit
        # Order status
        # Order audit
        #
        # All commit together.
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

            print(
                "WARNING: Order confirmed but "
                "EventBridge notification failed"
            )

        return {
            "order_id": order_id,
            "status": "CONFIRMED",
        }

    # ============================================================
    # BUSINESS FAILURE: INVENTORY
    # ============================================================

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
            performed_by=performed_by,
        )

        return {
            "order_id": order_id,
            "status": "FAILED",
            "reason": reason,
        }

    # ============================================================
    # BUSINESS FAILURE: VALIDATION
    # ============================================================

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
            performed_by=performed_by,
        )

        return {
            "order_id": order_id,
            "status": "FAILED",
            "reason": str(exc),
        }

    # ============================================================
    # TRANSIENT / SYSTEM FAILURE
    # ============================================================

    except Exception as exc:

        if connection:

            connection.rollback()

        print(
            f"ORDER PROCESSING ERROR "
            f"{order_id}: "
            f"{type(exc).__name__}: {exc}"
        )

        put_metric(
            "OrderProcessingFailures"
        )

        # --------------------------------------------------------
        # IMPORTANT:
        #
        # Do not permanently mark the order FAILED for transient
        # infrastructure/database errors.
        #
        # Raising causes Lambda/SQS to retry.
        # --------------------------------------------------------

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
            default=json_default
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
                    default=json_default
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
            # Raise so Lambda reports failure to SQS.
            # SQS will retry and eventually move to DLQ.
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
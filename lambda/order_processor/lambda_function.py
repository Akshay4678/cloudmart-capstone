import json
import os
from decimal import Decimal

import boto3
import pymysql
from botocore.config import Config


# =========================================================
# AWS CLIENTS
# =========================================================

events_client = boto3.client(
    "events",
    config=Config(
        connect_timeout=3,
        read_timeout=3,
        retries={
            "max_attempts": 1
        }
    )
)

dynamodb = boto3.resource("dynamodb")

cloudwatch = boto3.client(
    "cloudwatch"
)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

ORDERS_TABLE = os.environ["ORDERS_TABLE"]

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

ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "dev"
)


# =========================================================
# DYNAMODB TABLE
# =========================================================

orders_table = dynamodb.Table(
    ORDERS_TABLE
)


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_connection():

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )


# =========================================================
# CLOUDWATCH METRIC
# =========================================================

def publish_metric(
    metric_name
):

    try:

        cloudwatch.put_metric_data(
            Namespace="CloudMart/Application",
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Dimensions": [
                        {
                            "Name": "Environment",
                            "Value": ENVIRONMENT
                        },
                        {
                            "Name": "Service",
                            "Value": "OrderProcessing"
                        }
                    ],
                    "Value": 1,
                    "Unit": "Count"
                }
            ]
        )

    except Exception as error:

        print(
            "METRIC ERROR:",
            type(error).__name__,
            str(error)
        )


# =========================================================
# EVENTBRIDGE
# =========================================================

def publish_order_event(
    detail_type,
    detail
):

    result = events_client.put_events(
        Entries=[
            {
                "Source": "cloudmart.orders",
                "DetailType": detail_type,
                "Detail": json.dumps(
                    detail,
                    default=str
                )
            }
        ]
    )

    print(
        "EVENTBRIDGE RESULT:",
        json.dumps(result)
    )

    failed_count = result.get(
        "FailedEntryCount",
        0
    )

    if failed_count:
        raise RuntimeError(
            "Failed to publish EventBridge order event"
        )


# =========================================================
# GET ORDER FROM DYNAMODB
# =========================================================

def get_order(
    order_id
):

    result = orders_table.get_item(
        Key={
            "order_id": order_id
        }
    )

    return result.get(
        "Item"
    )


# =========================================================
# UPDATE ORDER STATUS
# =========================================================

def update_order_status(
    order_id,
    status
):

    orders_table.update_item(
        Key={
            "order_id": order_id
        },
        UpdateExpression="SET #status = :status",
        ExpressionAttributeNames={
            "#status": "status"
        },
        ExpressionAttributeValues={
            ":status": status
        }
    )


# =========================================================
# PROCESS ONE ORDER
# =========================================================

def process_order(
    order_id
):

    print(
        "Processing order:",
        order_id
    )

    # -----------------------------------------------------
    # READ ORDER FROM DYNAMODB
    # -----------------------------------------------------

    order = get_order(
        order_id
    )

    if not order:

        raise ValueError(
            f"Order {order_id} was not found"
        )

    customer_id = order.get(
        "customer_id"
    )

    items = order.get(
        "items",
        []
    )

    if not items:

        raise ValueError(
            f"Order {order_id} has no items"
        )

    # -----------------------------------------------------
    # CONNECT TO RDS
    # -----------------------------------------------------

    connection = None

    try:

        connection = get_connection()

        processed_items = []

        total_amount = Decimal("0")

        # -------------------------------------------------
        # PROCESS EACH PRODUCT
        # -------------------------------------------------

        for item in items:

            product_id = int(
                item["product_id"]
            )

            quantity = int(
                item["quantity"]
            )

            # ---------------------------------------------
            # READ PRODUCT
            # ---------------------------------------------

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    SELECT
                        product_id,
                        name,
                        price,
                        stock_count
                    FROM products
                    WHERE product_id = %s
                    FOR UPDATE
                    """,
                    (
                        product_id,
                    )
                )

                product = cursor.fetchone()

            if not product:

                raise ValueError(
                    f"Product {product_id} not found"
                )

            # ---------------------------------------------
            # CHECK STOCK
            # ---------------------------------------------

            stock_count = int(
                product["stock_count"]
            )

            if stock_count < quantity:

                raise ValueError(
                    f"Insufficient stock for product "
                    f"{product_id}"
                )

            # ---------------------------------------------
            # PRICE
            # ---------------------------------------------

            price = Decimal(
                str(
                    product["price"]
                )
            )

            item_total = (
                price * quantity
            )

            total_amount += item_total

            # ---------------------------------------------
            # UPDATE PRODUCT STOCK
            # ---------------------------------------------

            new_stock = (
                stock_count - quantity
            )

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    UPDATE products
                    SET stock_count = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_stock,
                        product_id
                    )
                )

            # ---------------------------------------------
            # UPDATE INVENTORY TABLE
            #
            # If an inventory row exists, update it.
            # ---------------------------------------------

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    UPDATE inventory
                    SET quantity = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_stock,
                        product_id
                    )
                )

            processed_items.append(
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "price": price,
                    "remaining_stock": new_stock
                }
            )

            # ---------------------------------------------
            # LOW STOCK METRIC
            # ---------------------------------------------

            if new_stock <= 5:

                publish_metric(
                    "LowStockEvents"
                )

                print(
                    "LOW STOCK:",
                    product_id,
                    new_stock
                )

        # -------------------------------------------------
        # COMMIT RDS CHANGES
        # -------------------------------------------------

        connection.commit()

        # -------------------------------------------------
        # UPDATE ORDER IN DYNAMODB
        # -------------------------------------------------

        orders_table.update_item(
            Key={
                "order_id": order_id
            },
            UpdateExpression="""
                SET #status = :status,
                    total_amount = :total_amount
            """,
            ExpressionAttributeNames={
                "#status": "status"
            },
            ExpressionAttributeValues={
                ":status": "CONFIRMED",
                ":total_amount": total_amount
            }
        )

        # -------------------------------------------------
        # EVENTBRIDGE ORDER PROCESSED
        # -------------------------------------------------

        publish_order_event(
            "OrderProcessed",
            {
                "order_id": order_id,
                "customer_id": customer_id,
                "status": "CONFIRMED",
                "total_amount": total_amount,
                "items": processed_items
            }
        )

        # -------------------------------------------------
        # CUSTOM METRIC
        # -------------------------------------------------

        publish_metric(
            "OrdersProcessed"
        )

        print(
            "Order processed successfully:",
            order_id
        )

        return {
            "order_id": order_id,
            "status": "CONFIRMED"
        }

    except Exception:

        if connection:

            try:
                connection.rollback()
            except Exception:
                pass

        raise

    finally:

        if connection:

            try:
                connection.close()
            except Exception:
                pass


# =========================================================
# HANDLE FAILED ORDER
# =========================================================

def handle_order_failure(
    order_id,
    error
):

    print(
        "ORDER PROCESSING FAILED:",
        order_id
    )

    print(
        "ERROR TYPE:",
        type(error).__name__
    )

    print(
        "ERROR MESSAGE:",
        str(error)
    )

    publish_metric(
        "OrdersProcessingFailures"
    )

    # -----------------------------------------------------
    # UPDATE ORDER STATUS
    # -----------------------------------------------------

    try:

        update_order_status(
            order_id,
            "FAILED"
        )

    except Exception as update_error:

        print(
            "FAILED TO UPDATE ORDER STATUS:",
            type(update_error).__name__
        )

        print(
            "UPDATE ERROR MESSAGE:",
            str(update_error)
        )

    # -----------------------------------------------------
    # EVENTBRIDGE ORDER FAILED
    # -----------------------------------------------------

    try:

        publish_order_event(
            "OrderFailed",
            {
                "order_id": order_id,
                "status": "FAILED",
                "error_type": type(
                    error
                ).__name__,
                "error": str(error)
            }
        )

    except Exception as event_error:

        print(
            "FAILED TO PUBLISH FAILURE EVENT:",
            type(event_error).__name__
        )

        print(
            "EVENT ERROR MESSAGE:",
            str(event_error)
        )


# =========================================================
# LAMBDA HANDLER
# SQS EVENT SOURCE
# =========================================================

def lambda_handler(
    event,
    context
):

    print(
        "========== ORDER PROCESSOR START =========="
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
            "processed": 0
        }

    processed_count = 0

    # -----------------------------------------------------
    # PROCESS EACH SQS MESSAGE
    # -----------------------------------------------------

    for record in records:

        body = record.get(
            "body",
            "{}"
        )

        try:

            message = json.loads(
                body
            )

        except json.JSONDecodeError as error:

            print(
                "INVALID SQS MESSAGE"
            )

            print(
                "ERROR TYPE:",
                type(error).__name__
            )

            print(
                "ERROR MESSAGE:",
                str(error)
            )

            # Raising causes SQS retry/DLQ.
            raise

        order_id = message.get(
            "order_id"
        )

        if not order_id:

            print(
                "SQS MESSAGE DOES NOT CONTAIN order_id"
            )

            raise ValueError(
                "order_id is missing from SQS message"
            )

        print(
            "SQS ORDER:",
            order_id
        )

        try:

            process_order(
                order_id
            )

            processed_count += 1

        except Exception as error:

            handle_order_failure(
                order_id,
                error
            )

            # -------------------------------------------------
            # IMPORTANT
            #
            # Raise the error so Lambda/SQS knows that the
            # message failed. SQS can retry it and eventually
            # move it to the DLQ.
            # -------------------------------------------------

            raise

    print(
        "========== ORDER PROCESSOR COMPLETE =========="
    )

    return {
        "processed": processed_count
    }
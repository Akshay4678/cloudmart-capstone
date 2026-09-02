import json
import os
from decimal import Decimal

import boto3
import pymysql


# =========================================================
# AWS CLIENTS
# =========================================================

dynamodb = boto3.resource("dynamodb")
events_client = boto3.client("events")
cloudwatch = boto3.client("cloudwatch")


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

ORDERS_TABLE_NAME = os.environ["ORDERS_TABLE"]

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))

ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "dev"
)

orders_table = dynamodb.Table(
    ORDERS_TABLE_NAME
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

def put_metric(metric_name):

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

    except Exception as e:

        print(
            "CloudWatch metric failed:",
            type(e).__name__
        )


# =========================================================
# EVENTBRIDGE
# =========================================================

def publish_event(detail_type, detail):

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
        "EventBridge FailedEntryCount:",
        result.get("FailedEntryCount", 0)
    )

    if result.get("FailedEntryCount", 0) > 0:
        raise RuntimeError(
            f"Failed to publish {detail_type}"
        )


# =========================================================
# PROCESS ONE ORDER
# =========================================================

def process_order(message):

    order_id = None
    connection = None

    try:

        order_id = message.get(
            "order_id"
        )

        if not order_id:
            raise ValueError(
                "order_id is missing"
            )

        print(
            "Processing order:",
            order_id
        )

        # -------------------------------------------------
        # GET ORDER FROM DYNAMODB
        # -------------------------------------------------

        result = orders_table.get_item(
            Key={
                "order_id": order_id
            }
        )

        order = result.get("Item")

        if not order:

            raise ValueError(
                f"Order {order_id} not found"
            )

        current_status = order.get(
            "status"
        )

        # -------------------------------------------------
        # IDEMPOTENCY
        # -------------------------------------------------

        if current_status == "CONFIRMED":

            print(
                f"Order {order_id} already confirmed"
            )

            return

        if current_status == "CANCELLED":

            print(
                f"Order {order_id} was cancelled"
            )

            return

        # -------------------------------------------------
        # GET ITEMS
        # -------------------------------------------------

        items = order.get(
            "items",
            []
        )

        if not items:

            raise ValueError(
                f"Order {order_id} has no items"
            )

        # -------------------------------------------------
        # CONNECT TO RDS
        # -------------------------------------------------

        connection = get_connection()

        total_amount = Decimal("0")

        processed_items = []

        # -------------------------------------------------
        # TRANSACTION
        # -------------------------------------------------

        with connection.cursor() as cursor:

            for item in items:

                product_id = int(
                    item["product_id"]
                )

                quantity = int(
                    item["quantity"]
                )

                if quantity <= 0:

                    raise ValueError(
                        "Quantity must be greater than zero"
                    )

                # -------------------------------------------------
                # LOCK PRODUCT
                # -------------------------------------------------

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
                    (product_id,)
                )

                product = cursor.fetchone()

                if not product:

                    raise ValueError(
                        f"Product {product_id} not found"
                    )

                available_stock = int(
                    product["stock_count"]
                )

                # -------------------------------------------------
                # INVENTORY CHECK
                # -------------------------------------------------

                if available_stock < quantity:

                    reason = (
                        f"Insufficient stock for "
                        f"product {product_id}"
                    )

                    # Mark order as failed
                    orders_table.update_item(
                        Key={
                            "order_id": order_id
                        },
                        UpdateExpression=(
                            "SET #status = :status, "
                            "failure_reason = :reason"
                        ),
                        ExpressionAttributeNames={
                            "#status": "status"
                        },
                        ExpressionAttributeValues={
                            ":status": "FAILED",
                            ":reason": reason
                        }
                    )

                    connection.rollback()

                    put_metric(
                        "OrderProcessingFailures"
                    )

                    publish_event(
                        "OrderFailed",
                        {
                            "order_id": order_id,
                            "reason": "InventoryUnavailable"
                        }
                    )

                    return

                # -------------------------------------------------
                # CALCULATE TOTAL
                # -------------------------------------------------

                price = Decimal(
                    str(product["price"])
                )

                line_total = (
                    price * quantity
                )

                total_amount += line_total

                processed_items.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                        "price": price
                    }
                )

                # -------------------------------------------------
                # UPDATE PRODUCTS STOCK
                # -------------------------------------------------

                new_stock = (
                    available_stock
                    - quantity
                )

                if new_stock < 0:

                    raise ValueError(
                        "Inventory cannot become negative"
                    )

                cursor.execute(
                    """
                    UPDATE products
                    SET
                        stock_count = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_stock,
                        product_id
                    )
                )

                # -------------------------------------------------
                # UPDATE INVENTORY TABLE
                # -------------------------------------------------

                cursor.execute(
                    """
                    UPDATE inventory
                    SET
                        quantity = %s
                    WHERE product_id = %s
                    """,
                    (
                        new_stock,
                        product_id
                    )
                )

            # -------------------------------------------------
            # COMMIT RDS TRANSACTION
            # -------------------------------------------------

            connection.commit()

        # -----------------------------------------------------
        # UPDATE DYNAMODB ORDER
        # -----------------------------------------------------

        orders_table.update_item(
            Key={
                "order_id": order_id
            },
            UpdateExpression=(
                "SET #status = :status, "
                "total_amount = :total, "
                "items = :items"
            ),
            ExpressionAttributeNames={
                "#status": "status"
            },
            ExpressionAttributeValues={
                ":status": "CONFIRMED",
                ":total": total_amount,
                ":items": processed_items
            }
        )

        # -----------------------------------------------------
        # PUBLISH ORDER PROCESSED EVENT
        # -----------------------------------------------------

        publish_event(
            "OrderProcessed",
            {
                "order_id": order_id,
                "customer_id": order.get(
                    "customer_id"
                ),
                "status": "CONFIRMED",
                "total_amount": total_amount
            }
        )

        # -----------------------------------------------------
        # METRIC
        # -----------------------------------------------------

        put_metric(
            "OrdersProcessed"
        )

        print(
            f"Order {order_id} processed successfully"
        )

    except Exception as e:

        print(
            "Order processing failed"
        )

        print(
            "Order ID:",
            order_id
        )

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)
        )

        if connection:

            try:
                connection.rollback()
            except Exception:
                pass

        # -------------------------------------------------
        # FAILURE METRIC
        # -------------------------------------------------

        put_metric(
            "OrderProcessingFailures"
        )

        # -------------------------------------------------
        # ORDER FAILED EVENT
        # -------------------------------------------------

        try:

            publish_event(
                "OrderFailed",
                {
                    "order_id": order_id,
                    "reason": "ProcessingError"
                }
            )

        except Exception as event_error:

            print(
                "Failed to publish OrderFailed event:",
                type(event_error).__name__
            )

        # -------------------------------------------------
        # IMPORTANT:
        # Raise the exception so Lambda/SQS retries
        # the message.
        # -------------------------------------------------

        raise

    finally:

        if connection:

            try:
                connection.close()
            except Exception:
                pass


# =========================================================
# SQS LAMBDA HANDLER
# =========================================================

def lambda_handler(event, context):

    print(
        "========== ORDER PROCESSOR START =========="
    )

    records = event.get(
        "Records",
        []
    )

    for record in records:

        try:

            body = record.get(
                "body",
                "{}"
            )

            message = json.loads(body)

            process_order(message)

        except Exception as e:

            print(
                "SQS record processing failed:",
                type(e).__name__
            )

            # Raise so SQS/Lambda does not delete
            # the message and can retry it.
            raise

    print(
        "========== ORDER PROCESSOR END =========="
    )

    return {
        "statusCode": 200,
        "message": "Messages processed"
    }
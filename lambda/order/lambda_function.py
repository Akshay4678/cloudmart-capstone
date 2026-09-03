import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key


# =========================================================
# AWS CLIENTS
# =========================================================

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")
cloudwatch = boto3.client("cloudwatch")


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

ORDERS_TABLE = os.environ["ORDERS_TABLE"]
ORDER_QUEUE_URL = os.environ["ORDER_QUEUE_URL"]
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")


# =========================================================
# DYNAMODB TABLE
# =========================================================

orders_table = dynamodb.Table(ORDERS_TABLE)


# =========================================================
# API RESPONSE
# =========================================================

def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, default=str)
    }


# =========================================================
# CLOUDWATCH CUSTOM METRIC
# =========================================================

def publish_metric(metric_name):
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
                            "Value": "Order"
                        }
                    ],
                    "Value": 1,
                    "Unit": "Count"
                }
            ]
        )
    except Exception as error:
        # Metric failure must not make an otherwise successful
        # order request fail.
        print(
            "METRIC ERROR:",
            type(error).__name__,
            str(error)
        )


# =========================================================
# CREATE ORDER
# POST /orders
# =========================================================

def create_order(event):

    try:

        # -------------------------------------------------
        # READ REQUEST BODY
        # -------------------------------------------------

        body = event.get("body")

        if not body:
            return response(
                400,
                {
                    "message": "Request body is required"
                }
            )

        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                return response(
                    400,
                    {
                        "message": "Request body must be valid JSON"
                    }
                )

        if not isinstance(body, dict):
            return response(
                400,
                {
                    "message": "Request body must be a JSON object"
                }
            )

        # -------------------------------------------------
        # CUSTOMER ID
        # -------------------------------------------------

        customer_id = body.get("customer_id")

        if not customer_id:
            return response(
                400,
                {
                    "message": "customer_id is required"
                }
            )

        if not isinstance(customer_id, str):
            return response(
                400,
                {
                    "message": "customer_id must be a string"
                }
            )

        customer_id = customer_id.strip()

        if not customer_id:
            return response(
                400,
                {
                    "message": "customer_id cannot be empty"
                }
            )

        # -------------------------------------------------
        # ITEMS
        # -------------------------------------------------

        items = body.get("items")

        if not items:
            return response(
                400,
                {
                    "message": "items is required"
                }
            )

        if not isinstance(items, list):
            return response(
                400,
                {
                    "message": "items must be a list"
                }
            )

        if len(items) == 0:
            return response(
                400,
                {
                    "message": "items cannot be empty"
                }
            )

        # -------------------------------------------------
        # VALIDATE ITEMS
        # -------------------------------------------------

        validated_items = []
        total_amount = Decimal("0")

        for item in items:

            if not isinstance(item, dict):
                return response(
                    400,
                    {
                        "message": "Each item must be an object"
                    }
                )

            product_id = item.get("product_id")
            quantity = item.get("quantity")

            # ---------------------------------------------
            # PRODUCT ID
            # ---------------------------------------------

            if product_id is None:
                return response(
                    400,
                    {
                        "message": "product_id is required"
                    }
                )

            try:
                product_id = int(product_id)
            except (TypeError, ValueError):
                return response(
                    400,
                    {
                        "message": "product_id must be an integer"
                    }
                )

            if product_id <= 0:
                return response(
                    400,
                    {
                        "message": "product_id must be greater than 0"
                    }
                )

            # ---------------------------------------------
            # QUANTITY
            # ---------------------------------------------

            if quantity is None:
                return response(
                    400,
                    {
                        "message": "quantity is required"
                    }
                )

            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                return response(
                    400,
                    {
                        "message": "quantity must be an integer"
                    }
                )

            if quantity <= 0:
                return response(
                    400,
                    {
                        "message": "quantity must be greater than 0"
                    }
                )

            # ---------------------------------------------
            # PRICE
            #
            # Price is optional because the documented
            # request only contains product_id and quantity.
            #
            # If the client supplies price, preserve it.
            # ---------------------------------------------

            validated_item = {
                "product_id": product_id,
                "quantity": quantity
            }

            price = item.get("price")

            if price is not None:

                try:
                    price_decimal = Decimal(str(price))
                except Exception:
                    return response(
                        400,
                        {
                            "message": "price must be a valid number"
                        }
                    )

                if price_decimal < 0:
                    return response(
                        400,
                        {
                            "message": "price cannot be negative"
                        }
                    )

                validated_item["price"] = price_decimal

                total_amount += (
                    price_decimal * quantity
                )

            validated_items.append(validated_item)

        # -------------------------------------------------
        # CREATE ORDER ID
        # -------------------------------------------------

        order_id = (
            "ORD"
            + uuid.uuid4().hex[:10].upper()
        )

        created_at = datetime.now(
            timezone.utc
        ).isoformat()

        # -------------------------------------------------
        # ORDER OBJECT
        # -------------------------------------------------

        order = {
            "order_id": order_id,
            "customer_id": customer_id,
            "created_at": created_at,
            "status": "PROCESSING",
            "total_amount": total_amount,
            "items": validated_items
        }

        # -------------------------------------------------
        # SAVE ORDER TO DYNAMODB
        # -------------------------------------------------

        print(
            "Creating order:",
            order_id
        )

        orders_table.put_item(
            Item=order,
            ConditionExpression=(
                "attribute_not_exists(order_id)"
            )
        )

        # -------------------------------------------------
        # SEND ORDER TO SQS
        # -------------------------------------------------

        message = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": validated_items,
            "total_amount": total_amount
        }

        sqs.send_message(
            QueueUrl=ORDER_QUEUE_URL,
            MessageBody=json.dumps(
                message,
                default=str
            )
        )

        # -------------------------------------------------
        # CUSTOM METRICS
        # -------------------------------------------------

        publish_metric("OrdersCreated")
        publish_metric("OrderRequests")

        # -------------------------------------------------
        # SUCCESS
        # -------------------------------------------------

        print(
            "Order accepted:",
            order_id
        )

        return response(
            202,
            {
                "message": "Order accepted for processing",
                "order_id": order_id,
                "status": "PROCESSING"
            }
        )

    except Exception as error:

        print(
            "========== ORDER CREATION ERROR =========="
        )

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(error)
        )

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )


# =========================================================
# GET ORDER
# GET /orders/{orderId}
# =========================================================

def get_order(order_id):

    try:

        if not order_id:
            return response(
                400,
                {
                    "message": "orderId is required"
                }
            )

        result = orders_table.get_item(
            Key={
                "order_id": order_id
            }
        )

        order = result.get("Item")

        if not order:
            return response(
                404,
                {
                    "message": "Order not found"
                }
            )

        return response(
            200,
            {
                "order": order
            }
        )

    except Exception as error:

        print(
            "GET ORDER ERROR:",
            type(error).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(error)
        )

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )


# =========================================================
# GET CUSTOMER ORDERS
# GET /orders?customerId=...
# =========================================================

def get_customer_orders(customer_id):

    try:

        if not customer_id:
            return response(
                400,
                {
                    "message": "customerId is required"
                }
            )

        result = orders_table.query(
            IndexName="customer_id-index",
            KeyConditionExpression=Key(
                "customer_id"
            ).eq(customer_id)
        )

        return response(
            200,
            {
                "orders": result.get(
                    "Items",
                    []
                )
            }
        )

    except Exception as error:

        print(
            "GET CUSTOMER ORDERS ERROR:",
            type(error).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(error)
        )

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(event, context):

    print(
        "========== ORDER LAMBDA START =========="
    )

    try:

        http_method = (
            event.get(
                "httpMethod",
                ""
            ).upper()
        )

        path_parameters = (
            event.get("pathParameters")
            or {}
        )

        query_parameters = (
            event.get("queryStringParameters")
            or {}
        )

        order_id = (
            path_parameters.get("orderId")
            or path_parameters.get("id")
        )

        customer_id = (
            query_parameters.get("customerId")
            or query_parameters.get("customer_id")
        )

        print(
            "HTTP METHOD:",
            http_method
        )

        print(
            "ORDER ID:",
            order_id
        )

        print(
            "CUSTOMER ID:",
            customer_id
        )

        # -------------------------------------------------
        # POST /orders
        # -------------------------------------------------

        if (
            http_method == "POST"
            and not order_id
        ):
            return create_order(event)

        # -------------------------------------------------
        # GET /orders/{orderId}
        # -------------------------------------------------

        if (
            http_method == "GET"
            and order_id
        ):
            return get_order(order_id)

        # -------------------------------------------------
        # GET /orders?customerId=...
        # -------------------------------------------------

        if (
            http_method == "GET"
            and customer_id
        ):
            return get_customer_orders(
                customer_id
            )

        # -------------------------------------------------
        # UNSUPPORTED ROUTE
        # -------------------------------------------------

        return response(
            405,
            {
                "message": "Method not allowed"
            }
        )

    except Exception as error:

        print(
            "ORDER HANDLER ERROR:",
            type(error).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(error)
        )

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )
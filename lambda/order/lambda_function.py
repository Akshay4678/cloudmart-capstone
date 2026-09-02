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
# HTTP RESPONSE HELPER
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
    """
    Publishes a CloudMart application metric.

    Metric namespace:
        CloudMart/Application

    Metrics used by Order Lambda:
        OrdersCreated
        OrderRequests

    A metric failure should not cause the order request
    itself to fail.
    """

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
        print(
            "CloudWatch metric error:",
            type(error).__name__
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


        # API Gateway normally sends body as a string.
        if isinstance(body, str):

            try:
                data = json.loads(body)

            except json.JSONDecodeError:
                return response(
                    400,
                    {
                        "message": "Invalid JSON body"
                    }
                )

        else:
            data = body


        # -------------------------------------------------
        # READ REQUEST FIELDS
        # -------------------------------------------------

        customer_id = data.get("customer_id")
        items = data.get("items")


        # -------------------------------------------------
        # VALIDATE CUSTOMER ID
        # -------------------------------------------------

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
                    "message": "customer_id is required"
                }
            )


        # -------------------------------------------------
        # VALIDATE ITEMS
        # -------------------------------------------------

        if not isinstance(items, list) or len(items) == 0:

            return response(
                400,
                {
                    "message": "items must be a non-empty list"
                }
            )


        validated_items = []

        total_amount = Decimal("0")


        # -------------------------------------------------
        # VALIDATE EACH ITEM
        # -------------------------------------------------

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


            # -------------------------------------------------
            # PRODUCT ID VALIDATION
            # -------------------------------------------------

            if product_id is None:

                return response(
                    400,
                    {
                        "message": "product_id is required for each item"
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


            # -------------------------------------------------
            # QUANTITY VALIDATION
            # -------------------------------------------------

            if quantity is None:

                return response(
                    400,
                    {
                        "message": "quantity is required for each item"
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


            # -------------------------------------------------
            # PRICE
            #
            # Price is optional because the documented
            # POST /orders request contains only:
            #
            # product_id
            # quantity
            #
            # If price is supplied, preserve it.
            # -------------------------------------------------

            price = item.get("price")

            validated_item = {
                "product_id": product_id,
                "quantity": quantity
            }


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
        # GENERATE ORDER ID
        # -------------------------------------------------

        order_id = (
            "ORD"
            + uuid.uuid4().hex[:10].upper()
        )


        # -------------------------------------------------
        # CREATE TIMESTAMP
        # -------------------------------------------------

        created_at = datetime.now(
            timezone.utc
        ).isoformat()


        # -------------------------------------------------
        # CREATE ORDER OBJECT
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
        # CREATE SQS MESSAGE
        # -------------------------------------------------

        message = {
            "order_id": order_id,
            "customer_id": customer_id,
            "items": validated_items,
            "total_amount": total_amount
        }


        # -------------------------------------------------
        # SEND ORDER TO SQS
        # -------------------------------------------------

        print(
            "Sending order to SQS:",
            order_id
        )


        sqs.send_message(
            QueueUrl=ORDER_QUEUE_URL,
            MessageBody=json.dumps(
                message,
                default=str
            )
        )


        # -------------------------------------------------
        # CUSTOM CLOUDWATCH METRICS
        # -------------------------------------------------

        publish_metric("OrdersCreated")

        publish_metric("OrderRequests")


        # -------------------------------------------------
        # SUCCESS
        #
        # IMPORTANT:
        # Order processing is asynchronous.
        # Therefore POST /orders returns 202.
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


    # -----------------------------------------------------
    # UNEXPECTED ERROR
    # -----------------------------------------------------

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


        # -------------------------------------------------
        # GET FROM DYNAMODB
        # -------------------------------------------------

        result = orders_table.get_item(
            Key={
                "order_id": order_id
            }
        )


        order = result.get("Item")


        # -------------------------------------------------
        # ORDER NOT FOUND
        # -------------------------------------------------

        if not order:

            return response(
                404,
                {
                    "message": "Order not found"
                }
            )


        # -------------------------------------------------
        # SUCCESS
        # -------------------------------------------------

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
                    "message": "customer_id is required"
                }
            )


        # -------------------------------------------------
        # QUERY CUSTOMER GSI
        # -------------------------------------------------

        result = orders_table.query(
            IndexName="customer_id-index",
            KeyConditionExpression=(
                Key("customer_id").eq(customer_id)
            )
        )


        # -------------------------------------------------
        # SUCCESS
        # -------------------------------------------------

        return response(
            200,
            {
                "orders": result.get("Items", [])
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


    # -------------------------------------------------
    # GET HTTP METHOD
    # -------------------------------------------------

    http_method = (
        event.get("httpMethod", "")
        .upper()
    )


    # -------------------------------------------------
    # GET PATH PARAMETERS
    # -------------------------------------------------

    path_parameters = (
        event.get("pathParameters")
        or {}
    )


    # -------------------------------------------------
    # GET QUERY PARAMETERS
    # -------------------------------------------------

    query_parameters = (
        event.get("queryStringParameters")
        or {}
    )


    # -------------------------------------------------
    # ORDER ID
    # -------------------------------------------------

    order_id = path_parameters.get("orderId")


    if not order_id:
        order_id = path_parameters.get("id")


    # =================================================
    # POST /orders
    # =================================================

    if (
        http_method == "POST"
        and not order_id
    ):

        return create_order(event)


    # =================================================
    # GET /orders/{orderId}
    # =================================================

    if (
        http_method == "GET"
        and order_id
    ):

        return get_order(order_id)


    # =================================================
    # GET /orders?customerId=...
    # =================================================

    if http_method == "GET":

        customer_id = (
            query_parameters.get("customerId")
            or query_parameters.get("customer_id")
        )


        if customer_id:

            return get_customer_orders(
                customer_id
            )


    # =================================================
    # UNSUPPORTED REQUEST
    # =================================================

    return response(
        405,
        {
            "message": "Method not allowed"
        }
    )
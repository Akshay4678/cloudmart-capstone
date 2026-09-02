import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3


# =========================================================
# AWS CLIENTS
# =========================================================

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")
cloudwatch = boto3.client("cloudwatch")


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

ORDERS_TABLE_NAME = os.environ["ORDERS_TABLE"]
ORDER_QUEUE_URL = os.environ["ORDER_QUEUE_URL"]
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

orders_table = dynamodb.Table(ORDERS_TABLE_NAME)


# =========================================================
# HTTP RESPONSE
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
                            "Value": "Order"
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
# CREATE ORDER ID
# =========================================================

def generate_order_id():
    return f"ORD{uuid.uuid4().hex[:10].upper()}"


# =========================================================
# POST /orders
# =========================================================

def create_order(event):

    body = event.get("body")

    if not body:
        return response(
            400,
            {
                "message": "Request body is required"
            }
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return response(
            400,
            {
                "message": "Invalid JSON body"
            }
        )

    # -----------------------------------------------------
    # CUSTOMER
    # -----------------------------------------------------

    customer_id = data.get("customer_id")

    if not customer_id:
        # Also accept camelCase for convenience
        customer_id = data.get("customerId")

    if not customer_id:
        return response(
            400,
            {
                "message": "customer_id is required"
            }
        )

    # -----------------------------------------------------
    # ITEMS
    # -----------------------------------------------------

    items = data.get("items")

    if not isinstance(items, list) or len(items) == 0:
        return response(
            400,
            {
                "message": "items must be a non-empty list"
            }
        )

    validated_items = []

    for item in items:

        if not isinstance(item, dict):
            return response(
                400,
                {
                    "message": "Each item must be an object"
                }
            )

        product_id = item.get("product_id")

        if product_id is None:
            product_id = item.get("productId")

        quantity = item.get("quantity")

        if product_id is None:
            return response(
                400,
                {
                    "message": "product_id is required for each item"
                }
            )

        if quantity is None:
            return response(
                400,
                {
                    "message": "quantity is required for each item"
                }
            )

        try:
            product_id = int(product_id)
            quantity = int(quantity)
        except (TypeError, ValueError):
            return response(
                400,
                {
                    "message": "product_id and quantity must be integers"
                }
            )

        if product_id <= 0:
            return response(
                400,
                {
                    "message": "product_id must be greater than 0"
                }
            )

        if quantity <= 0:
            return response(
                400,
                {
                    "message": "quantity must be greater than 0"
                }
            )

        validated_items.append(
            {
                "product_id": product_id,
                "quantity": quantity
            }
        )

    # -----------------------------------------------------
    # CREATE ORDER
    # -----------------------------------------------------

    order_id = generate_order_id()

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    order = {
        "order_id": order_id,
        "customer_id": str(customer_id),
        "created_at": created_at,
        "status": "PROCESSING",
        "total_amount": Decimal("0"),
        "items": validated_items
    }

    try:

        # -------------------------------------------------
        # SAVE ORDER TO DYNAMODB
        # -------------------------------------------------

        orders_table.put_item(
            Item=order,
            ConditionExpression="attribute_not_exists(order_id)"
        )

        print(
            f"Order {order_id} saved to DynamoDB"
        )

        # -------------------------------------------------
        # SEND ORDER TO SQS
        # -------------------------------------------------

        message = {
            "order_id": order_id,
            "customer_id": str(customer_id),
            "created_at": created_at,
            "items": validated_items
        }

        sqs.send_message(
            QueueUrl=ORDER_QUEUE_URL,
            MessageBody=json.dumps(message)
        )

        print(
            f"Order {order_id} sent to SQS"
        )

        # -------------------------------------------------
        # CUSTOM METRICS
        # -------------------------------------------------

        put_metric("OrdersCreated")
        put_metric("OrderRequests")

        # -------------------------------------------------
        # RESPONSE
        # -------------------------------------------------

        return response(
            202,
            {
                "message": "Order accepted for processing",
                "order_id": order_id,
                "status": "PROCESSING"
            }
        )

    except Exception as e:

        print(
            "Order creation failed:",
            type(e).__name__
        )

        print(
            "Order ID:",
            order_id
        )

        # -------------------------------------------------
        # Try to mark the order as failed if it was saved
        # -------------------------------------------------

        try:
            orders_table.update_item(
                Key={
                    "order_id": order_id
                },
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={
                    "#status": "status"
                },
                ExpressionAttributeValues={
                    ":status": "FAILED"
                }
            )
        except Exception:
            pass

        return response(
            500,
            {
                "message": "Failed to create order"
            }
        )


# =========================================================
# GET /orders/{orderId}
# =========================================================

def get_order(order_id):

    try:

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
                "message": "Order found",
                "order": order
            }
        )

    except Exception as e:

        print(
            "Get order failed:",
            type(e).__name__
        )

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )


# =========================================================
# GET /orders?customerId=X
# =========================================================

def get_customer_orders(customer_id):

    try:

        result = orders_table.query(
            IndexName="customer_id-index",
            KeyConditionExpression="customer_id = :customer_id",
            ExpressionAttributeValues={
                ":customer_id": customer_id
            },
            ScanIndexForward=False
        )

        orders = result.get("Items", [])

        return response(
            200,
            {
                "message": "Orders retrieved successfully",
                "count": len(orders),
                "orders": orders
            }
        )

    except Exception as e:

        print(
            "Get customer orders failed:",
            type(e).__name__
        )

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )


# =========================================================
# PUT /orders/{orderId}
# =========================================================

def update_order(order_id, event):

    body = event.get("body")

    if not body:
        return response(
            400,
            {
                "message": "Request body is required"
            }
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return response(
            400,
            {
                "message": "Invalid JSON body"
            }
        )

    # -----------------------------------------------------
    # CHECK ORDER
    # -----------------------------------------------------

    existing = orders_table.get_item(
        Key={
            "order_id": order_id
        }
    )

    order = existing.get("Item")

    if not order:
        return response(
            404,
            {
                "message": "Order not found"
            }
        )

    # -----------------------------------------------------
    # CUSTOMER ID
    # -----------------------------------------------------

    customer_id = data.get(
        "customer_id",
        data.get("customerId")
    )

    # -----------------------------------------------------
    # ITEMS
    # -----------------------------------------------------

    items = data.get("items")

    if items is None:
        items = order.get("items")

    if not isinstance(items, list) or len(items) == 0:
        return response(
            400,
            {
                "message": "items must be a non-empty list"
            }
        )

    validated_items = []

    for item in items:

        product_id = item.get(
            "product_id",
            item.get("productId")
        )

        quantity = item.get("quantity")

        if product_id is None or quantity is None:
            return response(
                400,
                {
                    "message": "Each item requires product_id and quantity"
                }
            )

        try:
            product_id = int(product_id)
            quantity = int(quantity)
        except (TypeError, ValueError):
            return response(
                400,
                {
                    "message": "product_id and quantity must be integers"
                }
            )

        if product_id <= 0 or quantity <= 0:
            return response(
                400,
                {
                    "message": "product_id and quantity must be greater than 0"
                }
            )

        validated_items.append(
            {
                "product_id": product_id,
                "quantity": quantity
            }
        )

    # -----------------------------------------------------
    # UPDATE DYNAMODB
    # -----------------------------------------------------

    try:

        expression_values = {
            ":items": validated_items,
            ":status": "PROCESSING"
        }

        expression_names = {
            "#items": "items",
            "#status": "status"
        }

        update_expression = (
            "SET #items = :items, "
            "#status = :status"
        )

        if customer_id:

            expression_values[":customer_id"] = str(
                customer_id
            )

            expression_names["#customer_id"] = (
                "customer_id"
            )

            update_expression += (
                ", #customer_id = :customer_id"
            )

        orders_table.update_item(
            Key={
                "order_id": order_id
            },
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values
        )

        # -------------------------------------------------
        # SEND UPDATED ORDER TO SQS
        # -------------------------------------------------

        sqs.send_message(
            QueueUrl=ORDER_QUEUE_URL,
            MessageBody=json.dumps(
                {
                    "order_id": order_id,
                    "items": validated_items,
                    "operation": "UPDATE"
                }
            )
        )

        return response(
            200,
            {
                "message": "Order updated successfully",
                "order_id": order_id,
                "status": "PROCESSING"
            }
        )

    except Exception as e:

        print(
            "Update order failed:",
            type(e).__name__
        )

        return response(
            500,
            {
                "message": "Internal server error"
            }
        )


# =========================================================
# POST /orders/{orderId}/cancel
# =========================================================

def cancel_order(order_id):

    try:

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

        current_status = order.get("status")

        if current_status in (
            "CONFIRMED",
            "CANCELLED",
            "FAILED"
        ):
            return response(
                409,
                {
                    "message": "Order cannot be cancelled",
                    "status": current_status
                }
            )

        orders_table.update_item(
            Key={
                "order_id": order_id
            },
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={
                "#status": "status"
            },
            ExpressionAttributeValues={
                ":status": "CANCELLED"
            }
        )

        return response(
            200,
            {
                "order_id": order_id,
                "status": "CANCELLED",
                "message": "Order cancelled successfully"
            }
        )

    except Exception as e:

        print(
            "Cancel order failed:",
            type(e).__name__
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

    http_method = (
        event.get("httpMethod", "")
        .upper()
    )

    path_parameters = (
        event.get("pathParameters")
        or {}
    )

    query_parameters = (
        event.get("queryStringParameters")
        or {}
    )

    # API Gateway template uses {orderId}
    # but support {id} as well.
    order_id = (
        path_parameters.get("orderId")
        or path_parameters.get("id")
    )

    customer_id = (
        query_parameters.get("customerId")
        or query_parameters.get("customer_id")
    )

    print(
        "HTTP Method:",
        http_method
    )

    print(
        "Order ID:",
        order_id
    )

    # -----------------------------------------------------
    # POST /orders/{orderId}/cancel
    # -----------------------------------------------------

    resource = event.get("resource", "")

    if (
        http_method == "POST"
        and order_id
        and resource.endswith("/cancel")
    ):
        return cancel_order(order_id)

    # -----------------------------------------------------
    # POST /orders
    # -----------------------------------------------------

    if http_method == "POST" and not order_id:
        return create_order(event)

    # -----------------------------------------------------
    # GET /orders/{orderId}
    # -----------------------------------------------------

    if http_method == "GET" and order_id:
        return get_order(order_id)

    # -----------------------------------------------------
    # GET /orders?customerId=X
    # -----------------------------------------------------

    if http_method == "GET" and customer_id:
        return get_customer_orders(
            customer_id
        )

    # -----------------------------------------------------
    # PUT /orders/{orderId}
    # -----------------------------------------------------

    if http_method == "PUT" and order_id:
        return update_order(
            order_id,
            event
        )

    # -----------------------------------------------------
    # INVALID REQUEST
    # -----------------------------------------------------

    return response(
        405,
        {
            "message": "Method not allowed"
        }
    )
import json
import os
import boto3
import pymysql


# =========================================================
# AWS CLIENTS
# =========================================================

events_client = boto3.client("events")


# =========================================================
# DATABASE CONFIGURATION
# =========================================================

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ.get("DB_NAME", "cloudmart")
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))


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
# PUBLISH EVENTBRIDGE EVENT
# =========================================================

def publish_event(detail_type, detail):
    result = events_client.put_events(
        Entries=[
            {
                "Source": "cloudmart.orders",
                "DetailType": detail_type,
                "Detail": json.dumps(detail)
            }
        ]
    )

    print(
        "EventBridge result:",
        json.dumps(result, default=str)
    )

    if result.get("FailedEntryCount", 0) > 0:
        raise Exception(
            f"EventBridge failed to publish {detail_type}"
        )


# =========================================================
# POST /orders
# =========================================================

def create_order(event):
    connection = None

    try:
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

        customer_id = data.get("customerId")
        product_id = data.get("productId")
        quantity = data.get("quantity")

        # -------------------------------------------------
        # VALIDATION
        # -------------------------------------------------

        if not customer_id:
            return response(
                400,
                {
                    "message": "customerId is required"
                }
            )

        if product_id is None:
            return response(
                400,
                {
                    "message": "productId is required"
                }
            )

        if quantity is None:
            return response(
                400,
                {
                    "message": "quantity is required"
                }
            )

        try:
            product_id = int(product_id)
            quantity = int(quantity)

        except (TypeError, ValueError):
            return response(
                400,
                {
                    "message": "productId and quantity must be integers"
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
        # CONNECT TO RDS
        # -------------------------------------------------

        connection = get_connection()

        with connection.cursor() as cursor:

            # -------------------------------------------------
            # CHECK PRODUCT
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
                """,
                (product_id,)
            )

            product = cursor.fetchone()

            if not product:
                connection.rollback()

                return response(
                    404,
                    {
                        "message": "Product not found"
                    }
                )

            # -------------------------------------------------
            # CHECK STOCK
            # -------------------------------------------------

            if product["stock_count"] < quantity:
                connection.rollback()

                return response(
                    409,
                    {
                        "message": "Insufficient stock",
                        "available_stock": product["stock_count"]
                    }
                )

            # -------------------------------------------------
            # CALCULATE TOTAL
            # -------------------------------------------------

            total_amount = float(product["price"]) * quantity

            # -------------------------------------------------
            # CREATE ORDER
            # -------------------------------------------------

            cursor.execute(
                """
                INSERT INTO orders (
                    customer_id,
                    product_id,
                    quantity,
                    total_amount,
                    status
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    'PENDING'
                )
                """,
                (
                    customer_id,
                    product_id,
                    quantity,
                    total_amount
                )
            )

            order_id = cursor.lastrowid

        # -------------------------------------------------
        # COMMIT ORDER
        # -------------------------------------------------

        connection.commit()

        print(
            "Order created:",
            order_id
        )

        # -------------------------------------------------
        # PUBLISH ORDER PLACED EVENT
        # -------------------------------------------------

        publish_event(
            "OrderPlaced",
            {
                "order_id": int(order_id),
                "customer_id": customer_id,
                "product_id": int(product_id),
                "quantity": int(quantity),
                "total_amount": total_amount
            }
        )

        return response(
            201,
            {
                "message": "Order placed successfully",
                "order_id": int(order_id),
                "status": "PENDING"
            }
        )

    except Exception as e:

        print("========== ORDER CREATION ERROR ==========")
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR MESSAGE:", str(e))

        if connection:
            try:
                connection.rollback()
            except Exception:
                pass

        return response(
            500,
            {
                "message": "Failed to place order",
                "error": str(e)
            }
        )

    finally:

        if connection:
            try:
                connection.close()
                print("RDS connection closed")
            except Exception:
                pass


# =========================================================
# GET /orders/{id}
# =========================================================

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
                    o.product_id,
                    p.name AS product_name,
                    o.quantity,
                    o.total_amount,
                    o.status,
                    o.failure_reason,
                    o.created_at,
                    o.updated_at
                FROM orders o
                LEFT JOIN products p
                    ON o.product_id = p.product_id
                WHERE o.order_id = %s
                """,
                (order_id,)
            )

            order = cursor.fetchone()

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
                "message": "Order retrieved successfully",
                "order": order
            }
        )

    except Exception as e:

        print("========== GET ORDER ERROR ==========")
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR MESSAGE:", str(e))

        return response(
            500,
            {
                "message": "Failed to retrieve order",
                "error": str(e)
            }
        )

    finally:

        if connection:
            try:
                connection.close()
                print("RDS connection closed")
            except Exception:
                pass


# =========================================================
# GET /orders?customerId=X
# =========================================================

def get_customer_orders(customer_id):
    connection = None

    try:

        connection = get_connection()

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    o.order_id,
                    o.customer_id,
                    o.product_id,
                    p.name AS product_name,
                    o.quantity,
                    o.total_amount,
                    o.status,
                    o.failure_reason,
                    o.created_at,
                    o.updated_at
                FROM orders o
                LEFT JOIN products p
                    ON o.product_id = p.product_id
                WHERE o.customer_id = %s
                ORDER BY o.created_at DESC
                """,
                (customer_id,)
            )

            orders = cursor.fetchall()

        return response(
            200,
            {
                "message": "Orders retrieved successfully",
                "count": len(orders),
                "orders": orders
            }
        )

    except Exception as e:

        print("========== GET CUSTOMER ORDERS ERROR ==========")
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR MESSAGE:", str(e))

        return response(
            500,
            {
                "message": "Failed to retrieve orders",
                "error": str(e)
            }
        )

    finally:

        if connection:
            try:
                connection.close()
                print("RDS connection closed")
            except Exception:
                pass


# =========================================================
# PROCESS ORDER
#
# This function is called when EventBridge sends
# an OrderPlaced event back to this Lambda.
# =========================================================

def process_order(event):
    connection = None

    try:

        print("========== ORDER PROCESSING ==========")
        print(
            "EventBridge event:",
            json.dumps(event, default=str)
        )

        detail = event.get("detail", {})

        order_id = detail.get("order_id")

        if not order_id:
            raise Exception(
                "order_id missing from OrderPlaced event"
            )

        connection = get_connection()

        with connection.cursor() as cursor:

            # -------------------------------------------------
            # LOCK ORDER
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    order_id,
                    product_id,
                    quantity,
                    status
                FROM orders
                WHERE order_id = %s
                FOR UPDATE
                """,
                (order_id,)
            )

            order = cursor.fetchone()

            if not order:
                raise Exception(
                    f"Order {order_id} not found"
                )

            # -------------------------------------------------
            # IDEMPOTENCY CHECK
            # -------------------------------------------------

            if order["status"] == "CONFIRMED":

                connection.commit()

                print(
                    f"Order {order_id} already confirmed"
                )

                return {
                    "message": "Order already confirmed"
                }

            if order["status"] == "FAILED":

                connection.commit()

                print(
                    f"Order {order_id} already failed"
                )

                return {
                    "message": "Order already failed"
                }

            # -------------------------------------------------
            # LOCK PRODUCT
            # -------------------------------------------------

            cursor.execute(
                """
                SELECT
                    product_id,
                    name,
                    stock_count,
                    price
                FROM products
                WHERE product_id = %s
                FOR UPDATE
                """,
                (order["product_id"],)
            )

            product = cursor.fetchone()

            if not product:

                cursor.execute(
                    """
                    UPDATE orders
                    SET
                        status = 'FAILED',
                        failure_reason = %s
                    WHERE order_id = %s
                    """,
                    (
                        "Product not found",
                        order_id
                    )
                )

                connection.commit()

                publish_event(
                    "OrderFailed",
                    {
                        "order_id": int(order_id),
                        "reason": "Product not found"
                    }
                )

                return {
                    "message": "Order failed"
                }

            # -------------------------------------------------
            # CHECK INVENTORY
            # -------------------------------------------------

            if product["stock_count"] < order["quantity"]:

                reason = "Insufficient stock"

                cursor.execute(
                    """
                    UPDATE orders
                    SET
                        status = 'FAILED',
                        failure_reason = %s
                    WHERE order_id = %s
                    """,
                    (
                        reason,
                        order_id
                    )
                )

                connection.commit()

                publish_event(
                    "OrderFailed",
                    {
                        "order_id": int(order_id),
                        "reason": reason,
                        "available_stock": int(
                            product["stock_count"]
                        )
                    }
                )

                return {
                    "message": "Order failed",
                    "reason": reason
                }

            # -------------------------------------------------
            # DEDUCT INVENTORY
            # -------------------------------------------------

            new_stock = (
                product["stock_count"]
                - order["quantity"]
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
                    order["product_id"]
                )
            )

            # -------------------------------------------------
            # CONFIRM ORDER
            # -------------------------------------------------

            cursor.execute(
                """
                UPDATE orders
                SET
                    status = 'CONFIRMED',
                    failure_reason = NULL
                WHERE order_id = %s
                """,
                (order_id,)
            )

        # -------------------------------------------------
        # COMMIT TRANSACTION
        # -------------------------------------------------

        connection.commit()

        print(
            f"Order {order_id} confirmed"
        )

        # -------------------------------------------------
        # PUBLISH ORDER CONFIRMED EVENT
        # -------------------------------------------------

        publish_event(
            "OrderConfirmed",
            {
                "order_id": int(order_id),
                "product_id": int(order["product_id"]),
                "quantity": int(order["quantity"]),
                "remaining_stock": int(new_stock)
            }
        )

        return {
            "message": "Order confirmed",
            "order_id": int(order_id)
        }

    except Exception as e:

        print("========== ORDER PROCESSING ERROR ==========")
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR MESSAGE:", str(e))

        if connection:
            try:
                connection.rollback()
            except Exception:
                pass

        # -------------------------------------------------
        # TRY TO MARK ORDER AS FAILED
        # -------------------------------------------------

        try:

            if connection:

                with connection.cursor() as cursor:

                    cursor.execute(
                        """
                        UPDATE orders
                        SET
                            status = 'FAILED',
                            failure_reason = %s
                        WHERE order_id = %s
                        """,
                        (
                            str(e)[:255],
                            order_id
                        )
                    )

                connection.commit()

            publish_event(
                "OrderFailed",
                {
                    "order_id": int(order_id),
                    "reason": str(e)
                }
            )

        except Exception as failure_event_error:

            print(
                "Failed to publish OrderFailed event:",
                str(failure_event_error)
            )

        raise

    finally:

        if connection:
            try:
                connection.close()
                print("RDS connection closed")
            except Exception:
                pass


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(event, context):

    print("========== ORDER LAMBDA START ==========")

    print(
        "Event:",
        json.dumps(event, default=str)
    )

    # =====================================================
    # EVENTBRIDGE EVENT
    # =====================================================

    if event.get("source") == "cloudmart.orders":

        detail_type = event.get("detail-type")

        if detail_type == "OrderPlaced":

            return process_order(event)

        print(
            "Ignoring EventBridge event:",
            detail_type
        )

        return {
            "message": "Event ignored"
        }

    # =====================================================
    # API GATEWAY REQUEST
    # =====================================================

    http_method = event.get(
        "httpMethod",
        ""
    ).upper()

    path_parameters = (
        event.get("pathParameters")
        or {}
    )

    query_parameters = (
        event.get("queryStringParameters")
        or {}
    )

    order_id = path_parameters.get("id")

    customer_id = query_parameters.get(
        "customerId"
    )

    print(
        "HTTP Method:",
        http_method
    )

    print(
        "Order ID:",
        order_id
    )

    print(
        "Customer ID:",
        customer_id
    )

    # =====================================================
    # POST /orders
    # =====================================================

    if http_method == "POST":

        return create_order(event)

    # =====================================================
    # GET /orders/{id}
    # =====================================================

    if http_method == "GET" and order_id:

        try:
            order_id = int(order_id)

        except ValueError:

            return response(
                400,
                {
                    "message": "Invalid order ID"
                }
            )

        return get_order(order_id)

    # =====================================================
    # GET /orders?customerId=X
    # =====================================================

    if http_method == "GET" and customer_id:

        return get_customer_orders(
            customer_id
        )

    # =====================================================
    # INVALID REQUEST
    # =====================================================

    return response(
        405,
        {
            "message": "Method not allowed"
        }
    )
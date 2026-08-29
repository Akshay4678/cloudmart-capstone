import json
import os
import pymysql
import boto3
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


# =========================================================
# DATABASE CONFIGURATION
# =========================================================

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_connection():

    print("BEFORE DB CONNECTION")

    connection = pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor
    )

    print("AFTER DB CONNECTION")

    return connection


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
# PUBLISH INVENTORY CHANGE EVENT
# =========================================================

def publish_inventory_event(product_id, stock_count):

    print("BEFORE EVENTBRIDGE")

    event_detail = {
        "product_id": int(product_id),
        "stock_count": int(stock_count)
    }

    try:

        result = events_client.put_events(
            Entries=[
                {
                    "Source": "cloudmart.inventory",
                    "DetailType": "InventoryChanged",
                    "Detail": json.dumps(event_detail)
                }
            ]
        )

        print(
            "EventBridge result:",
            json.dumps(result, default=str)
        )

        # Check if EventBridge rejected the event
        if result.get("FailedEntryCount", 0) > 0:

            print(
                "WARNING: EventBridge failed to publish event:",
                json.dumps(result, default=str)
            )

        else:

            print("AFTER EVENTBRIDGE - Event published successfully")

    except Exception as e:

        # Do not fail the product operation if EventBridge
        # is temporarily unreachable.
        print("WARNING: EventBridge publish failed")
        print("EVENTBRIDGE ERROR TYPE:", type(e).__name__)
        print("EVENTBRIDGE ERROR MESSAGE:", str(e))


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(event, context):

    print("========== LAMBDA START ==========")

    print(
        "Event:",
        json.dumps(event, default=str)
    )

    connection = None

    try:

        # =====================================================
        # GET REQUEST INFORMATION
        # =====================================================

        http_method = event.get("httpMethod", "GET")

        path_parameters = event.get("pathParameters") or {}

        product_id = path_parameters.get("id")

        print("HTTP Method:", http_method)
        print("Product ID:", product_id)


        # =====================================================
        # GET /products/{id}
        # =====================================================

        if http_method == "GET" and product_id:

            connection = get_connection()

            with connection.cursor() as cursor:

                print("BEFORE SELECT PRODUCT")

                cursor.execute(
                    """
                    SELECT *
                    FROM products
                    WHERE product_id = %s
                    """,
                    (product_id,)
                )

                product = cursor.fetchone()

                print("AFTER SELECT PRODUCT")


            if not product:

                return response(
                    404,
                    {
                        "message": "Product not found"
                    }
                )


            return response(
                200,
                {
                    "message": "Product retrieved successfully",
                    "product": product
                }
            )


        # =====================================================
        # GET /products
        # =====================================================

        if http_method == "GET":

            connection = get_connection()

            with connection.cursor() as cursor:

                print("BEFORE SELECT ALL PRODUCTS")

                cursor.execute(
                    """
                    SELECT *
                    FROM products
                    """
                )

                products = cursor.fetchall()

                print("AFTER SELECT ALL PRODUCTS")


            return response(
                200,
                {
                    "message": "Products retrieved successfully",
                    "count": len(products),
                    "products": products
                }
            )


        # =====================================================
        # POST /products
        # =====================================================

        if http_method == "POST":

            body = event.get("body")

            print("Request body:", body)


            if not body:

                return response(
                    400,
                    {
                        "message": "Request body is required"
                    }
                )


            # =================================================
            # PARSE JSON
            # =================================================

            try:

                data = json.loads(body)

            except json.JSONDecodeError:

                return response(
                    400,
                    {
                        "message": "Invalid JSON body"
                    }
                )


            name = data.get("name")
            description = data.get("description")
            price = data.get("price")
            stock_count = data.get("stock_count")


            # =================================================
            # VALIDATION
            # =================================================

            if not name or price is None or stock_count is None:

                return response(
                    400,
                    {
                        "message": "name, price and stock_count are required"
                    }
                )


            # =================================================
            # DATABASE INSERT
            # =================================================

            connection = get_connection()

            with connection.cursor() as cursor:

                print("BEFORE INSERT")

                cursor.execute(
                    """
                    INSERT INTO products
                    (
                        name,
                        description,
                        price,
                        stock_count
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        name,
                        description,
                        price,
                        stock_count
                    )
                )

                new_product_id = cursor.lastrowid

                print(
                    "AFTER INSERT - Product ID:",
                    new_product_id
                )


            # =================================================
            # COMMIT
            # =================================================

            connection.commit()

            print("AFTER COMMIT")


            # =================================================
            # PUBLISH EVENT
            # =================================================

            publish_inventory_event(
                new_product_id,
                stock_count
            )


            # =================================================
            # RESPONSE
            # =================================================

            return response(
                201,
                {
                    "message": "Product created successfully",
                    "product_id": new_product_id
                }
            )


        # =====================================================
        # PUT /products/{id}
        # =====================================================

        if http_method == "PUT" and product_id:

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


            name = data.get("name")
            description = data.get("description")
            price = data.get("price")
            stock_count = data.get("stock_count")


            if not name or price is None or stock_count is None:

                return response(
                    400,
                    {
                        "message": "name, price and stock_count are required"
                    }
                )


            # =================================================
            # DATABASE UPDATE
            # =================================================

            connection = get_connection()

            with connection.cursor() as cursor:

                print("BEFORE UPDATE")

                cursor.execute(
                    """
                    UPDATE products
                    SET
                        name = %s,
                        description = %s,
                        price = %s,
                        stock_count = %s
                    WHERE product_id = %s
                    """,
                    (
                        name,
                        description,
                        price,
                        stock_count,
                        product_id
                    )
                )

                affected_rows = cursor.rowcount

                print(
                    "AFTER UPDATE - Rows:",
                    affected_rows
                )


            connection.commit()

            print("AFTER COMMIT")


            if affected_rows == 0:

                return response(
                    404,
                    {
                        "message": "Product not found"
                    }
                )


            # =================================================
            # PUBLISH INVENTORY EVENT
            # =================================================

            publish_inventory_event(
                product_id,
                stock_count
            )


            return response(
                200,
                {
                    "message": "Product updated successfully",
                    "product_id": int(product_id)
                }
            )


        # =====================================================
        # DELETE /products/{id}
        # =====================================================

        if http_method == "DELETE" and product_id:

            connection = get_connection()

            with connection.cursor() as cursor:

                print("BEFORE DELETE")

                cursor.execute(
                    """
                    DELETE FROM products
                    WHERE product_id = %s
                    """,
                    (product_id,)
                )

                affected_rows = cursor.rowcount

                print(
                    "AFTER DELETE - Rows:",
                    affected_rows
                )


            connection.commit()

            print("AFTER COMMIT")


            if affected_rows == 0:

                return response(
                    404,
                    {
                        "message": "Product not found"
                    }
                )


            return response(
                200,
                {
                    "message": "Product deleted successfully",
                    "product_id": int(product_id)
                }
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


    # =========================================================
    # ERROR HANDLING
    # =========================================================

    except Exception as e:

        print("========== ERROR ==========")

        print(
            "ERROR TYPE:",
            type(e).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(e)
        )


        if connection:

            try:

                connection.rollback()

                print("Database transaction rolled back")

            except Exception as rollback_error:

                print(
                    "Rollback error:",
                    str(rollback_error)
                )


        return response(
            500,
            {
                "message": "Internal server error",
                "error": str(e)
            }
        )


    # =========================================================
    # CLEANUP
    # =========================================================

    finally:

        if connection:

            try:

                connection.close()

                print("RDS connection closed")

            except Exception as close_error:

                print(
                    "Connection close error:",
                    str(close_error)
                )

        print("========== LAMBDA END ==========")
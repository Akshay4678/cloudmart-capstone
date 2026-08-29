import json
import os
import pymysql

DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))


def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor
    )


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, default=str)
    }


def lambda_handler(event, context):

    print("========== LAMBDA START ==========")
    print("Event:", json.dumps(event))

    connection = None

    try:

        http_method = event.get("httpMethod", "GET")

        path_parameters = event.get("pathParameters") or {}
        product_id = path_parameters.get("id")

        print("HTTP Method:", http_method)
        print("Product ID:", product_id)

        # =========================================================
        # GET /products/{id}
        # =========================================================

        if http_method == "GET" and product_id:

            print("Getting product:", product_id)

            connection = get_connection()

            with connection.cursor() as cursor:

                cursor.execute(
                    "SELECT * FROM products WHERE product_id = %s",
                    (product_id,)
                )

                product = cursor.fetchone()

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

        # =========================================================
        # GET /products
        # =========================================================

        if http_method == "GET":

            print("Getting all products")

            connection = get_connection()

            with connection.cursor() as cursor:

                cursor.execute("SELECT * FROM products")

                products = cursor.fetchall()

            return response(
                200,
                {
                    "message": "Products retrieved successfully",
                    "count": len(products),
                    "products": products
                }
            )

        # =========================================================
        # POST /products
        # =========================================================

        if http_method == "POST":

            print("Creating new product")

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

            connection = get_connection()

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    INSERT INTO products
                    (name, description, price, stock_count)
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

            connection.commit()

            print("Product created:", new_product_id)

            return response(
                201,
                {
                    "message": "Product created successfully",
                    "product_id": new_product_id
                }
            )

        # =========================================================
        # PUT /products/{id}
        # =========================================================

        if http_method == "PUT" and product_id:

            print("Updating product:", product_id)

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

            connection = get_connection()

            with connection.cursor() as cursor:

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

            connection.commit()

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
                    "message": "Product updated successfully",
                    "product_id": int(product_id)
                }
            )

        # =========================================================
        # DELETE /products/{id}
        # =========================================================

        if http_method == "DELETE" and product_id:

            print("Deleting product:", product_id)

            connection = get_connection()

            with connection.cursor() as cursor:

                cursor.execute(
                    """
                    DELETE FROM products
                    WHERE product_id = %s
                    """,
                    (product_id,)
                )

                affected_rows = cursor.rowcount

            connection.commit()

            if affected_rows == 0:
                return response(
                    404,
                    {
                        "message": "Product not found"
                    }
                )

            print("Product deleted:", product_id)

            return response(
                200,
                {
                    "message": "Product deleted successfully",
                    "product_id": int(product_id)
                }
            )

        # =========================================================
        # INVALID REQUEST
        # =========================================================

        return response(
            405,
            {
                "message": "Method not allowed"
            }
        )

    except Exception as e:

        print("========== ERROR ==========")
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
                "message": "Internal server error",
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
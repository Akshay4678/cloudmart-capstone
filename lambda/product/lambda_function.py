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


def lambda_handler(event, context):

    print("========== LAMBDA START ==========")
    print("Event:", json.dumps(event))

    try:

        http_method = event.get("httpMethod", "GET")

        print("HTTP Method:", http_method)

        if http_method != "GET":
            return {
                "statusCode": 405,
                "headers": {
                    "Content-Type": "application/json"
                },
                "body": json.dumps({
                    "message": "Method not allowed"
                })
            }

        print("Connecting to RDS...")

        connection = get_connection()

        print("SUCCESS: Connected to RDS!")

        with connection.cursor() as cursor:

            print("Executing SELECT query...")

            cursor.execute("SELECT * FROM products")

            products = cursor.fetchall()

            print("Products found:", len(products))

        connection.close()

        print("RDS connection closed")

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "message": "Products retrieved successfully",
                "count": len(products),
                "products": products
            }, default=str)
        }

    except Exception as e:

        print("========== ERROR ==========")
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR MESSAGE:", str(e))

        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "message": "Failed to retrieve products",
                "error": str(e)
            })
        }
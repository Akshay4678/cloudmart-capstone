import json
import os

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
        read_timeout=10,
        write_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
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
        "body": json.dumps(
            body,
            default=str
        )
    }


# =========================================================
# AUDIT ACTOR
# =========================================================

def get_performed_by(event):

    """
    Try to identify the user who performed the operation.

    If API Gateway authorizer information is available,
    use the user/customer identifier.

    Otherwise use 'api'.
    """

    try:

        request_context = (
            event.get("requestContext")
            or {}
        )

        authorizer = (
            request_context.get("authorizer")
            or {}
        )

        # JWT authorizer
        jwt_claims = (
            authorizer.get("jwt", {})
            .get("claims", {})
        )

        if jwt_claims:

            for key in [
                "sub",
                "username",
                "email"
            ]:

                if jwt_claims.get(key):

                    return str(
                        jwt_claims[key]
                    )

        # Lambda authorizer
        for key in [
            "user",
            "username",
            "email",
            "customer_id"
        ]:

            if authorizer.get(key):

                return str(
                    authorizer[key]
                )

    except Exception as exc:

        print(
            "Could not determine audit actor:",
            str(exc)
        )

    return "api"


# =========================================================
# AUDIT LOG
# =========================================================

def write_audit_log(
    connection,
    entity_type,
    entity_id,
    action,
    old_value=None,
    new_value=None,
    performed_by="api"
):

    """
    Write an entry into audit_logs.

    old_value:
        State before the operation.

    new_value:
        State after the operation.
    """

    print(
        "Writing audit log:",
        action,
        entity_type,
        entity_id
    )

    old_json = None

    if old_value is not None:

        old_json = json.dumps(
            old_value,
            default=str
        )

    new_json = None

    if new_value is not None:

        new_json = json.dumps(
            new_value,
            default=str
        )

    with connection.cursor() as cursor:

        cursor.execute(
            """
            INSERT INTO audit_logs
            (
                entity_type,
                entity_id,
                action,
                old_value,
                new_value,
                performed_by
            )
            VALUES
            (
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
                old_json,
                new_json,
                performed_by
            )
        )


# =========================================================
# PUBLISH INVENTORY CHANGE EVENT
# =========================================================

def publish_inventory_event(
    product_id,
    stock_count
):

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
                    "Detail": json.dumps(
                        event_detail
                    )
                }
            ]
        )

        print(
            "EventBridge result:",
            json.dumps(
                result,
                default=str
            )
        )

        if result.get(
            "FailedEntryCount",
            0
        ) > 0:

            print(
                "WARNING: EventBridge failed "
                "to publish event:",
                json.dumps(
                    result,
                    default=str
                )
            )

        else:

            print(
                "AFTER EVENTBRIDGE - "
                "Event published successfully"
            )

    except Exception as exc:

        # EventBridge failure should not undo
        # the already committed database operation.

        print(
            "WARNING: EventBridge publish failed"
        )

        print(
            "EVENTBRIDGE ERROR TYPE:",
            type(exc).__name__
        )

        print(
            "EVENTBRIDGE ERROR MESSAGE:",
            str(exc)
        )


# =========================================================
# CREATE PRODUCT
# =========================================================

def create_product(
    event,
    connection
):

    body = event.get("body")

    print(
        "Request body:",
        body
    )

    if not body:

        return response(
            400,
            {
                "message": (
                    "Request body is required"
                )
            }
        )

    # ---------------------------------------------------------
    # PARSE JSON
    # ---------------------------------------------------------

    try:

        data = json.loads(body)

    except json.JSONDecodeError:

        return response(
            400,
            {
                "message": "Invalid JSON body"
            }
        )

    # ---------------------------------------------------------
    # READ DATA
    # ---------------------------------------------------------

    name = data.get("name")
    description = data.get("description")
    price = data.get("price")
    stock_count = data.get("stock_count")

    # ---------------------------------------------------------
    # VALIDATION
    # ---------------------------------------------------------

    if (
        not name
        or price is None
        or stock_count is None
    ):

        return response(
            400,
            {
                "message": (
                    "name, price and "
                    "stock_count are required"
                )
            }
        )

    try:

        stock_count = int(
            stock_count
        )

    except (TypeError, ValueError):

        return response(
            400,
            {
                "message": (
                    "stock_count must be an integer"
                )
            }
        )

    if stock_count < 0:

        return response(
            400,
            {
                "message": (
                    "stock_count cannot be negative"
                )
            }
        )

    # ---------------------------------------------------------
    # DETERMINE INITIAL STATUS
    # ---------------------------------------------------------

    status = (
        "ACTIVE"
        if stock_count > 0
        else "INACTIVE"
    )

    performed_by = get_performed_by(
        event
    )

    # ---------------------------------------------------------
    # INSERT PRODUCT
    # ---------------------------------------------------------

    with connection.cursor() as cursor:

        print("BEFORE INSERT")

        cursor.execute(
            """
            INSERT INTO products
            (
                name,
                description,
                price,
                stock_count,
                status
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                name,
                description,
                price,
                stock_count,
                status
            )
        )

        new_product_id = cursor.lastrowid

        print(
            "AFTER INSERT - Product ID:",
            new_product_id
        )

    # ---------------------------------------------------------
    # CREATE INVENTORY ROW
    # ---------------------------------------------------------

    with connection.cursor() as cursor:

        cursor.execute(
            """
            INSERT INTO inventory
            (
                product_id,
                quantity
            )
            VALUES
            (
                %s,
                %s
            )
            """,
            (
                new_product_id,
                stock_count
            )
        )

    # ---------------------------------------------------------
    # AUDIT CREATE
    # ---------------------------------------------------------

    new_product = {
        "product_id": new_product_id,
        "name": name,
        "description": description,
        "price": price,
        "stock_count": stock_count,
        "status": status
    }

    write_audit_log(
        connection=connection,
        entity_type="PRODUCT",
        entity_id=new_product_id,
        action="CREATE_PRODUCT",
        old_value=None,
        new_value=new_product,
        performed_by=performed_by
    )

    # ---------------------------------------------------------
    # COMMIT
    # ---------------------------------------------------------

    connection.commit()

    print("AFTER COMMIT")

    # ---------------------------------------------------------
    # PUBLISH EVENT
    # ---------------------------------------------------------

    publish_inventory_event(
        new_product_id,
        stock_count
    )

    # ---------------------------------------------------------
    # RESPONSE
    # ---------------------------------------------------------

    return response(
        201,
        {
            "message": (
                "Product created successfully"
            ),
            "product_id": new_product_id,
            "status": status
        }
    )


# =========================================================
# GET SINGLE ACTIVE PRODUCT
# =========================================================

def get_product(
    product_id,
    connection
):

    with connection.cursor() as cursor:

        print(
            "BEFORE SELECT PRODUCT"
        )

        cursor.execute(
            """
            SELECT
                product_id,
                name,
                description,
                price,
                stock_count,
                status,
                created_at,
                updated_at
            FROM products
            WHERE product_id = %s
              AND status = 'ACTIVE'
            """,
            (product_id,)
        )

        product = cursor.fetchone()

        print(
            "AFTER SELECT PRODUCT"
        )

    if not product:

        return response(
            404,
            {
                "message": (
                    "Product not found"
                )
            }
        )

    return response(
        200,
        {
            "message": (
                "Product retrieved successfully"
            ),
            "product": product
        }
    )


# =========================================================
# GET ACTIVE PRODUCTS
# =========================================================

def get_products(
    connection
):

    with connection.cursor() as cursor:

        print(
            "BEFORE SELECT ACTIVE PRODUCTS"
        )

        cursor.execute(
            """
            SELECT
                product_id,
                name,
                description,
                price,
                stock_count,
                status,
                created_at,
                updated_at
            FROM products
            WHERE status = 'ACTIVE'
            ORDER BY product_id
            """
        )

        products = cursor.fetchall()

        print(
            "AFTER SELECT ACTIVE PRODUCTS"
        )

    return response(
        200,
        {
            "message": (
                "Products retrieved successfully"
            ),
            "count": len(products),
            "products": products
        }
    )


# =========================================================
# UPDATE PRODUCT
# =========================================================

def update_product(
    event,
    product_id,
    connection
):

    body = event.get("body")

    if not body:

        return response(
            400,
            {
                "message": (
                    "Request body is required"
                )
            }
        )

    # ---------------------------------------------------------
    # PARSE JSON
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # VALIDATION
    # ---------------------------------------------------------

    if (
        not name
        or price is None
        or stock_count is None
    ):

        return response(
            400,
            {
                "message": (
                    "name, price and "
                    "stock_count are required"
                )
            }
        )

    try:

        stock_count = int(
            stock_count
        )

    except (TypeError, ValueError):

        return response(
            400,
            {
                "message": (
                    "stock_count must be an integer"
                )
            }
        )

    if stock_count < 0:

        return response(
            400,
            {
                "message": (
                    "stock_count cannot be negative"
                )
            }
        )

    performed_by = get_performed_by(
        event
    )

    # ---------------------------------------------------------
    # GET EXISTING PRODUCT
    # ---------------------------------------------------------

    with connection.cursor() as cursor:

        cursor.execute(
            """
            SELECT
                product_id,
                name,
                description,
                price,
                stock_count,
                status,
                created_at,
                updated_at
            FROM products
            WHERE product_id = %s
            FOR UPDATE
            """,
            (product_id,)
        )

        old_product = cursor.fetchone()

    if not old_product:

        connection.rollback()

        return response(
            404,
            {
                "message": (
                    "Product not found"
                )
            }
        )

    # ---------------------------------------------------------
    # OLD STOCK
    # ---------------------------------------------------------

    old_stock = int(
        old_product["stock_count"]
    )

    # ---------------------------------------------------------
    # DETERMINE NEW STATUS
    # ---------------------------------------------------------
    #
    # stock > 0 -> ACTIVE
    # stock = 0 -> INACTIVE
    #
    # This also means:
    #
    # INACTIVE + stock increased above 0
    #     -> ACTIVE
    #
    # ---------------------------------------------------------

    new_status = (
        "ACTIVE"
        if stock_count > 0
        else "INACTIVE"
    )

    # ---------------------------------------------------------
    # UPDATE PRODUCT
    # ---------------------------------------------------------

    with connection.cursor() as cursor:

        print("BEFORE UPDATE")

        cursor.execute(
            """
            UPDATE products
            SET
                name = %s,
                description = %s,
                price = %s,
                stock_count = %s,
                status = %s
            WHERE product_id = %s
            """,
            (
                name,
                description,
                price,
                stock_count,
                new_status,
                product_id
            )
        )

        print(
            "AFTER UPDATE - Rows:",
            cursor.rowcount
        )

    # ---------------------------------------------------------
    # SYNCHRONIZE INVENTORY
    # ---------------------------------------------------------

    with connection.cursor() as cursor:

        cursor.execute(
            """
            UPDATE inventory
            SET
                quantity = %s
            WHERE product_id = %s
            """,
            (
                stock_count,
                product_id
            )
        )

        inventory_rows = cursor.rowcount

        # If an inventory row does not exist,
        # create one.

        if inventory_rows == 0:

            cursor.execute(
                """
                INSERT INTO inventory
                (
                    product_id,
                    quantity
                )
                VALUES
                (
                    %s,
                    %s
                )
                """,
                (
                    product_id,
                    stock_count
                )
            )

    # ---------------------------------------------------------
    # NEW PRODUCT SNAPSHOT
    # ---------------------------------------------------------

    new_product = {
        "product_id": int(product_id),
        "name": name,
        "description": description,
        "price": price,
        "stock_count": stock_count,
        "status": new_status
    }

    # ---------------------------------------------------------
    # AUDIT PRODUCT UPDATE
    # ---------------------------------------------------------

    write_audit_log(
        connection=connection,
        entity_type="PRODUCT",
        entity_id=product_id,
        action="UPDATE_PRODUCT",
        old_value=old_product,
        new_value=new_product,
        performed_by=performed_by
    )

    # ---------------------------------------------------------
    # STOCK AUDIT
    # ---------------------------------------------------------

    if stock_count > old_stock:

        write_audit_log(
            connection=connection,
            entity_type="PRODUCT",
            entity_id=product_id,
            action="STOCK_INCREASED",
            old_value={
                "stock_count": old_stock
            },
            new_value={
                "stock_count": stock_count
            },
            performed_by=performed_by
        )

    elif stock_count < old_stock:

        write_audit_log(
            connection=connection,
            entity_type="PRODUCT",
            entity_id=product_id,
            action="STOCK_DECREASED",
            old_value={
                "stock_count": old_stock
            },
            new_value={
                "stock_count": stock_count
            },
            performed_by=performed_by
        )

    # ---------------------------------------------------------
    # STATUS CHANGE AUDIT
    # ---------------------------------------------------------

    if old_product["status"] != new_status:

        write_audit_log(
            connection=connection,
            entity_type="PRODUCT",
            entity_id=product_id,
            action="STATUS_CHANGED",
            old_value={
                "status": old_product["status"]
            },
            new_value={
                "status": new_status
            },
            performed_by=performed_by
        )

    # ---------------------------------------------------------
    # COMMIT
    # ---------------------------------------------------------

    connection.commit()

    print(
        "PRODUCT UPDATE COMMITTED"
    )

    # ---------------------------------------------------------
    # PUBLISH EVENT
    # ---------------------------------------------------------

    publish_inventory_event(
        product_id,
        stock_count
    )

    return response(
        200,
        {
            "message": (
                "Product updated successfully"
            ),
            "product_id": int(product_id),
            "stock_count": stock_count,
            "status": new_status
        }
    )


# =========================================================
# SOFT DELETE PRODUCT
# =========================================================

def delete_product(
    event,
    product_id,
    connection
):

    performed_by = get_performed_by(
        event
    )

    # ---------------------------------------------------------
    # GET CURRENT PRODUCT
    # ---------------------------------------------------------

    with connection.cursor() as cursor:

        cursor.execute(
            """
            SELECT
                product_id,
                name,
                description,
                price,
                stock_count,
                status,
                created_at,
                updated_at
            FROM products
            WHERE product_id = %s
            FOR UPDATE
            """,
            (product_id,)
        )

        old_product = cursor.fetchone()

    if not old_product:

        connection.rollback()

        return response(
            404,
            {
                "message": (
                    "Product not found"
                )
            }
        )

    # ---------------------------------------------------------
    # ALREADY INACTIVE
    # ---------------------------------------------------------

    if old_product["status"] == "INACTIVE":

        connection.rollback()

        return response(
            409,
            {
                "message": (
                    "Product is already inactive"
                ),
                "product_id": int(product_id),
                "status": "INACTIVE"
            }
        )

    # ---------------------------------------------------------
    # SOFT DELETE
    # ---------------------------------------------------------
    #
    # DO NOT:
    #
    # DELETE FROM products
    #
    # We keep the row so:
    #
    # - order history remains valid
    # - product history remains available
    # - audit records remain meaningful
    #
    # ---------------------------------------------------------

    with connection.cursor() as cursor:

        print(
            "BEFORE SOFT DELETE"
        )

        cursor.execute(
            """
            UPDATE products
            SET
                status = 'INACTIVE'
            WHERE product_id = %s
            """,
            (product_id,)
        )

        print(
            "AFTER SOFT DELETE - Rows:",
            cursor.rowcount
        )

    # ---------------------------------------------------------
    # AUDIT SOFT DELETE
    # ---------------------------------------------------------

    new_product = dict(
        old_product
    )

    new_product["status"] = "INACTIVE"

    write_audit_log(
        connection=connection,
        entity_type="PRODUCT",
        entity_id=product_id,
        action="SOFT_DELETE_PRODUCT",
        old_value=old_product,
        new_value=new_product,
        performed_by=performed_by
    )

    # ---------------------------------------------------------
    # COMMIT
    # ---------------------------------------------------------

    connection.commit()

    print(
        "SOFT DELETE COMMITTED"
    )

    return response(
        200,
        {
            "message": (
                "Product deleted successfully"
            ),
            "product_id": int(product_id),
            "status": "INACTIVE"
        }
    )


# =========================================================
# LAMBDA HANDLER
# =========================================================

def lambda_handler(
    event,
    context
):

    print(
        "========== LAMBDA START =========="
    )

    print(
        "Event:",
        json.dumps(
            event,
            default=str
        )
    )

    connection = None

    try:

        # =====================================================
        # GET REQUEST INFORMATION
        # =====================================================

        http_method = (
            event.get(
                "httpMethod",
                "GET"
            )
            .upper()
        )

        path_parameters = (
            event.get("pathParameters")
            or {}
        )

        product_id = (
            path_parameters.get(
                "productId"
            )
        )

        print(
            "HTTP Method:",
            http_method
        )

        print(
            "Product ID:",
            product_id
        )

        # =====================================================
        # DATABASE CONNECTION
        # =====================================================

        connection = get_connection()

        # =====================================================
        # GET /products/{id}
        # =====================================================

        if (
            http_method == "GET"
            and product_id
        ):

            return get_product(
                product_id,
                connection
            )

        # =====================================================
        # GET /products
        # =====================================================

        if http_method == "GET":

            return get_products(
                connection
            )

        # =====================================================
        # POST /products
        # =====================================================

        if http_method == "POST":

            return create_product(
                event,
                connection
            )

        # =====================================================
        # PUT /products/{id}
        # =====================================================

        if (
            http_method == "PUT"
            and product_id
        ):

            return update_product(
                event,
                product_id,
                connection
            )

        # =====================================================
        # DELETE /products/{id}
        # =====================================================

        if (
            http_method == "DELETE"
            and product_id
        ):

            return delete_product(
                event,
                product_id,
                connection
            )

        # =====================================================
        # INVALID REQUEST
        # =====================================================

        return response(
            405,
            {
                "message": (
                    "Method not allowed"
                )
            }
        )

    # =========================================================
    # ERROR HANDLING
    # =========================================================

    except Exception as exc:

        print(
            "========== ERROR =========="
        )

        print(
            "ERROR TYPE:",
            type(exc).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(exc)
        )

        if connection:

            try:

                connection.rollback()

                print(
                    "Database transaction rolled back"
                )

            except Exception as rollback_error:

                print(
                    "Rollback error:",
                    str(rollback_error)
                )

        return response(
            500,
            {
                "message": (
                    "Internal server error"
                ),
                "error": str(exc)
            }
        )

    finally:

        if connection:

            try:

                connection.close()

                print(
                    "RDS connection closed"
                )

            except Exception as close_error:

                print(
                    "Connection close error:",
                    str(close_error)
                )

        print(
            "========== LAMBDA END =========="
        )
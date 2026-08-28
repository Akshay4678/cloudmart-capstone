import json

def lambda_handler(event, context):

    method = event["httpMethod"]

    if method == "GET":
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "GET Products"
            })
        }

    if method == "POST":
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "POST Product"
            })
        }

    if method == "PUT":
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "PUT Product"
            })
        }

    if method == "DELETE":
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "DELETE Product"
            })
        }

    return {
        "statusCode": 400,
        "body": json.dumps({
            "message": "Unsupported Method"
        })
    }
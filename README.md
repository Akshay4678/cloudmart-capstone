
# CloudMart – Complete Deployment Runbook

> **Purpose:** This runbook explains how to take the CloudMart repository from GitHub, configure the required AWS/GitHub connection, create the required IAM/OIDC setup, configure GitHub Secrets, configure SSM Parameter Store, deploy the CloudFormation stacks through GitHub Actions, verify the application, test the API, and troubleshoot common failures.
>
> The instructions below are based on the current CloudMart repository structure and workflow. Values such as AWS account ID, passwords, email addresses, and tokens are intentionally represented as placeholders.

---

## 1. Project Overview

CloudMart is an AWS-based shop/order management application.

### Main components

- **Amazon VPC**
  - Public subnet for the Flask dashboard EC2 instance.
  - Private application subnets for Lambda functions.
  - Private database subnet for RDS MySQL.
  - VPC endpoints for AWS services used by private workloads.
- **Amazon RDS MySQL**
  - Customers
  - Products
  - Orders
  - Order items
  - Audit logs
- **AWS Lambda**
  - Authorizer
  - Customer
  - Product
  - Order
  - Order Processor
  - Report
- **Amazon API Gateway**
  - REST API
  - Custom Lambda authorizer
- **Amazon SQS**
  - Asynchronous order processing
  - Dead-letter queue
- **Amazon EventBridge**
  - Order processing events
  - Daily reporting schedule
  - Failure/event routing
- **Amazon SNS**
  - Order confirmations
  - Low-stock notifications
  - Failure notifications
- **Amazon S3**
  - Lambda deployment artifacts
  - Dashboard package
  - Generated reports
- **Amazon EC2**
  - Flask/Gunicorn dashboard
  - NGINX reverse proxy
- **AWS Systems Manager**
  - Secure parameter storage
  - EC2 management/deployment through SSM Run Command
- **Amazon CloudWatch**
  - Logs
  - Metrics
  - Alarms
  - Operations dashboard
- **GitHub Actions**
  - Builds Lambda packages
  - Uploads artifacts
  - Deploys CloudFormation
  - Deploys the dashboard to EC2 through SSM

---

# 2. Repository Structure

After cloning the repository, the important directories/files are:

```text
cloudmart-capstone/
│
├── .github/
│   └── workflows/
│       └── deploy.yml
│
├── cloudformation/
│   ├── network-stack.yaml
│   ├── data-stack.yaml
│   ├── iam-stack.yaml
│   ├── auth-stack.yaml
│   ├── api-stack.yaml
│   ├── monitoring-stack.yaml
│   └── README.md
│
├── lambda/
│   ├── authorizer/
│   │   ├── lambda_function.py
│   │   └── requirements.txt
│   ├── customer/
│   │   ├── lambda_function.py
│   │   └── requirements.txt
│   ├── product/
│   │   ├── lambda_function.py
│   │   └── requirements.txt
│   ├── order/
│   │   ├── lambda_function.py
│   │   └── requirements.txt
│   ├── order_processor/
│   │   ├── lambda_function.py
│   │   └── requirements.txt
│   └── report/
│       ├── lambda_function.py
│       └── requirements.txt
│
├── dashboard/
│   ├── app.py
│   └── requirements.txt
│
├── docs/
│
├── schema.sql
└── README.md
```

---

# 3. Prerequisites

The person deploying the project needs:

### Required

1. A GitHub account.
2. A GitHub repository containing this project.
3. An AWS account.
4. AWS CLI installed locally.
5. Git installed locally.
6. VS Code or another Git-capable editor.
7. Permission to create IAM/OIDC resources in AWS.
8. Permission to configure GitHub repository secrets.
9. An AWS region selected for the project.

### Project region

The current GitHub Actions workflow uses:

```text
ap-south-1
```

which is the AWS Mumbai region.

If another region is required, update the workflow:

```yaml
AWS_REGION: ap-south-1
```

before deployment.

---

# 4. Install and Verify Local Tools

## 4.1 Check Git

```powershell
git --version
```

Example:

```text
git version 2.x.x
```

## 4.2 Check AWS CLI

```powershell
aws --version
```

## 4.3 Configure AWS CLI

For the initial AWS setup, use an AWS administrator or sufficiently privileged IAM identity.

```powershell
aws configure
```

Enter:

```text
AWS Access Key ID: <your-access-key>
AWS Secret Access Key: <your-secret-key>
Default region name: ap-south-1
Default output format: json
```

Verify:

```powershell
aws sts get-caller-identity
```

Expected output contains:

```json
{
  "Account": "<AWS_ACCOUNT_ID>",
  "Arn": "...",
  "UserId": "..."
}
```

> These local AWS credentials are only for initial AWS administration. The GitHub Actions deployment itself uses GitHub OIDC and does **not** require storing AWS access keys in GitHub Secrets.

---

# 5. Clone the GitHub Repository

If the repository already exists:

```powershell
git clone https://github.com/<GITHUB_OWNER>/cloudmart-capstone.git
cd cloudmart-capstone
```

For the original repository, the remote was:

```text
https://github.com/Akshay4678/cloudmart-capstone
```

A new owner/fork must replace the repository owner in all GitHub OIDC trust-policy instructions.

Verify:

```powershell
git remote -v
```

---

# 6. IMPORTANT: GitHub OIDC Authentication

## Why OIDC is used

GitHub Actions needs permission to deploy resources into AWS.

Instead of storing:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

in GitHub, this project uses:

```text
GitHub Actions
       |
       | OIDC token
       v
AWS IAM OIDC Provider
       |
       v
GitHub Actions IAM Role
       |
       v
AWS services
```

The workflow contains:

```yaml
permissions:
  id-token: write
  contents: read
```

and:

```yaml
uses: aws-actions/configure-aws-credentials@v4
```

with:

```yaml
role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
```

---

# 7. Create the GitHub OIDC Identity Provider

This is a **one-time AWS account setup**.

Go to:

```text
AWS Console
→ IAM
→ Identity providers
→ Add provider
```

Select:

```text
Provider type:
OpenID Connect
```

Provider URL:

```text
https://token.actions.githubusercontent.com
```

Audience:

```text
sts.amazonaws.com
```

Create the provider.

### Important

Only create the provider once per AWS account.

If it already exists, do not create another one.

The provider ARN will be similar to:

```text
arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com
```

---

# 8. Create the GitHub Actions IAM Role

Create an IAM role:

```text
cloudmart-github-actions-role
```

Choose:

```text
Trusted entity:
Web identity
```

Identity provider:

```text
token.actions.githubusercontent.com
```

Audience:

```text
sts.amazonaws.com
```

---

# 9. GitHub OIDC Trust Policy

The most important part is the role trust policy.

Use:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:<GITHUB_OWNER>/<GITHUB_REPO>:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

Replace:

```text
<AWS_ACCOUNT_ID>
<GITHUB_OWNER>
<GITHUB_REPO>
```

For the current repository:

```text
<GITHUB_OWNER> = Akshay4678
<GITHUB_REPO>  = cloudmart-capstone
```

Therefore the subject becomes:

```text
repo:Akshay4678/cloudmart-capstone:ref:refs/heads/main
```

### Why this restriction is important

It prevents an unrelated GitHub repository from assuming the AWS deployment role.

Only the `main` branch of the trusted repository can use the role.

---

# 10. GitHub Actions Deployment Role Permissions

The exact external OIDC role used by the original deployment is not stored inside the repository. Therefore, when rebuilding the project in another AWS account, a new deployment role must be created.

The role must be able to deploy the services used by the CloudMart CloudFormation stacks and workflow.

At minimum, the deployment role needs permissions for:

```text
CloudFormation
EC2
RDS
S3
Lambda
IAM
API Gateway
SQS
SNS
EventBridge
CloudWatch
CloudWatch Logs
SSM
STS
```

It also needs:

```text
iam:PassRole
```

because CloudFormation creates Lambda and EC2 roles.

## Recommended bootstrap approach

For a capstone/rebuild account, the simplest reliable approach is to create the deployment role with sufficiently broad deployment permissions first.

A broad service-scoped policy can contain:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudformation:*",
        "ec2:*",
        "rds:*",
        "s3:*",
        "lambda:*",
        "apigateway:*",
        "sqs:*",
        "sns:*",
        "events:*",
        "logs:*",
        "cloudwatch:*",
        "ssm:*",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:CreateInstanceProfile",
        "iam:DeleteInstanceProfile",
        "iam:GetInstanceProfile",
        "iam:AddRoleToInstanceProfile",
        "iam:RemoveRoleFromInstanceProfile",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "iam:PassRole",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:CreateServiceLinkedRole",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

> This is intentionally broad for deployment reliability. After the project is working, it can be reduced to a least-privilege deployment policy.

---

# 11. Create the Role Using AWS CLI

Create the trust policy file:

```powershell
notepad github-trust-policy.json
```

Paste the trust policy from Section 9.

Create the role:

```powershell
aws iam create-role `
  --role-name cloudmart-github-actions-role `
  --assume-role-policy-document file://github-trust-policy.json
```

Create the deployment policy:

```powershell
notepad github-deploy-policy.json
```

Paste the policy from Section 10.

Create the policy:

```powershell
aws iam create-policy `
  --policy-name CloudMartGitHubDeploymentPolicy `
  --policy-document file://github-deploy-policy.json
```

Get your AWS account ID:

```powershell
$ACCOUNT_ID = aws sts get-caller-identity `
  --query Account `
  --output text
```

Attach the policy:

```powershell
aws iam attach-role-policy `
  --role-name cloudmart-github-actions-role `
  --policy-arn "arn:aws:iam::$ACCOUNT_ID`:policy/CloudMartGitHubDeploymentPolicy"
```

Get the role ARN:

```powershell
aws iam get-role `
  --role-name cloudmart-github-actions-role `
  --query "Role.Arn" `
  --output text
```

Save the returned ARN. It will look like:

```text
arn:aws:iam::<AWS_ACCOUNT_ID>:role/cloudmart-github-actions-role
```

---

# 12. GitHub Repository Secrets

Go to:

```text
GitHub repository
→ Settings
→ Secrets and variables
→ Actions
→ New repository secret
```

Create these **three secrets**.

| Secret | Value |
|---|---|
| `AWS_ROLE_ARN` | ARN of `cloudmart-github-actions-role` |
| `DB_PASSWORD` | RDS master password |
| `EMAIL_NOTIFICATION` | Email address for CloudMart notifications |

### Example

```text
AWS_ROLE_ARN
arn:aws:iam::<AWS_ACCOUNT_ID>:role/cloudmart-github-actions-role
```

```text
DB_PASSWORD
<your-secure-database-password>
```

```text
EMAIL_NOTIFICATION
<your-email@example.com>
```

### Do not create

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

The workflow is designed to use OIDC instead.

---

# 13. SSM Parameter Store

CloudMart uses AWS Systems Manager Parameter Store.

The parameters are environment-specific.

For `dev`:

```text
/cloudmart/dev/database/password
/cloudmart/dev/notification-email
/cloudmart/dev/reports-bucket
/cloudmart/dev/lambda-artifact-bucket
```

For `prod`:

```text
/cloudmart/prod/database/password
/cloudmart/prod/notification-email
/cloudmart/prod/reports-bucket
/cloudmart/prod/lambda-artifact-bucket
```

---

# 14. Database Password SSM Parameter

The GitHub workflow creates this automatically:

```text
/cloudmart/dev/database/password
```

Type:

```text
SecureString
```

Value:

```text
DB_PASSWORD GitHub Secret
```

The workflow command is effectively:

```bash
aws ssm put-parameter \
  --region "$AWS_REGION" \
  --name "/cloudmart/$ENVIRONMENT_NAME/database/password" \
  --type "SecureString" \
  --value "$DB_PASSWORD" \
  --overwrite
```

Verify:

```powershell
aws ssm get-parameter `
  --region ap-south-1 `
  --name /cloudmart/dev/database/password `
  --with-decryption
```

Do not print the value in normal logs.

---

# 15. Notification Email SSM Parameter

The workflow creates:

```text
/cloudmart/dev/notification-email
```

Type:

```text
String
```

Value:

```text
EMAIL_NOTIFICATION GitHub Secret
```

Verify:

```powershell
aws ssm get-parameter `
  --region ap-south-1 `
  --name /cloudmart/dev/notification-email
```

---

# 16. Reports Bucket SSM Parameter

The Reports S3 bucket is created by the Data CloudFormation stack.

After Data Stack deployment, the workflow gets the bucket output and stores it in:

```text
/cloudmart/dev/reports-bucket
```

This is consumed by the dashboard and related workloads.

---

# 17. Lambda Artifact Bucket SSM Parameter

The Data Stack creates the Lambda artifact bucket.

The workflow obtains the bucket name and stores it in:

```text
/cloudmart/dev/lambda-artifact-bucket
```

The Lambda packages are uploaded to this bucket.

---

# 18. CloudFormation Deployment Order

Do **not** deploy the stacks randomly.

The required order is:

```text
1. Network
       ↓
2. Data
       ↓
3. IAM
       ↓
4. Auth
       ↓
5. API
       ↓
6. Dashboard deployment through SSM
       ↓
7. Database schema initialization
       ↓
8. Monitoring
```

The GitHub Actions workflow performs this sequence automatically.

### Why the order matters

Network creates:

```text
VPC
Subnets
Security Groups
VPC Endpoints
EC2
EC2 IAM role
```

Data depends on Network:

```text
RDS
S3 buckets
```

IAM depends on Data exports:

```text
Lambda roles
```

Auth depends on:

```text
Network
Data
IAM
Lambda artifact
```

API depends on:

```text
Network
Data
IAM
Auth
Lambda artifacts
```

Monitoring depends on the application/data resources.

---

# 19. Environment Name

The current workflow contains:

```yaml
ENVIRONMENT_NAME: dev
```

The project supports:

```text
dev
prod
```

For a development deployment:

```text
ENVIRONMENT_NAME=dev
```

Stack names become:

```text
cloudmart-network-stack-dev
cloudmart-data-stack-dev
cloudmart-iam-stack-dev
cloudmart-auth-stack-dev
cloudmart-api-stack-dev
cloudmart-monitoring-stack-dev
```

For production:

```text
ENVIRONMENT_NAME=prod
```

the corresponding names become:

```text
cloudmart-network-stack-prod
cloudmart-data-stack-prod
cloudmart-iam-stack-prod
cloudmart-auth-stack-prod
cloudmart-api-stack-prod
cloudmart-monitoring-stack-prod
```

Before using `prod`, make sure the same GitHub OIDC role is allowed to deploy the branch/workflow you intend to use.

---

# 20. First Deployment

After:

- AWS OIDC provider exists
- GitHub OIDC role exists
- role trust policy is correct
- deployment permissions are attached
- GitHub Secrets are configured

push the project to GitHub.

The workflow file is:

```text
.github/workflows/deploy.yml
```

It supports:

```yaml
workflow_dispatch:
```

and:

```yaml
push:
  branches:
    - main
    - master
```

---

# 21. Run Deployment from GitHub

Go to:

```text
GitHub
→ Repository
→ Actions
→ Deploy CloudMart Infrastructure
→ Run workflow
```

Or push a commit to `main`.

The workflow will:

1. Checkout repository.
2. Assume the AWS IAM role using OIDC.
3. Verify AWS identity.
4. Verify GitHub Secrets.
5. Store database password in SSM.
6. Store notification email in SSM.
7. Validate CloudFormation.
8. Validate Python files.
9. Verify Lambda requirements.
10. Package Lambda functions.
11. Verify packages.
12. Clean legacy stacks if present.
13. Deploy Network.
14. Deploy Data.
15. Store Reports Bucket in SSM.
16. Deploy IAM.
17. Upload Lambda packages to S3.
18. Deploy Auth.
19. Deploy API.
20. Package dashboard.
21. Upload dashboard to S3.
22. Deploy dashboard to EC2 through SSM.
23. Initialize RDS schema.
24. Deploy Monitoring.
25. Verify reporting schedule.
26. Verify CloudWatch monitoring.
27. Print API URL and dashboard URL.

---

# 22. Lambda Packaging

The workflow packages these functions:

```text
Authorizer
Product
Order
Order Processor
Customer
Report
```

Each package uses the GitHub commit SHA.

Example:

```text
order/order-lambda-<GITHUB_SHA>.zip
```

This is important because every deployment gets a unique artifact version.

---

# 23. Order Lambda and Order Processor

These are separate Lambda functions.

### Order Lambda

Handles:

```text
POST /orders
GET /orders
GET /orders/{orderId}
PUT /orders/{orderId}
PATCH /orders/{orderId}
```

It sends asynchronous orders to SQS.

### Order Processor

Consumes messages from:

```text
cloudmart-orders-dev
```

It processes stock/order state and publishes events.

Do not package the Order Processor code as the Order Lambda.

The workflow keeps them as separate packages:

```text
build/order-lambda-<SHA>.zip
build/order-processor-<SHA>.zip
```

---

# 24. RDS Database Initialization

The workflow invokes:

```text
cloudmart-order-dev
```

with:

```json
{
  "action": "initialize_schema"
}
```

The Order Lambda executes the database schema from:

```text
schema.sql
```

The workflow checks:

```text
statusCode == 200
statements_failed == 0
```

Only after successful initialization does the workflow continue to Monitoring.

---

# 25. Database Schema

The database name is:

```text
cloudmart
```

Default username:

```text
cloudmartadmin
```

The schema contains:

```text
customers
products
orders
order_items
audit_logs
```

The schema also creates sample products and sample customer records.

### Important security recommendation

Before using this project outside a demo/capstone environment, replace any real personal seed data and use a newly generated administrator credential/token.

Never commit real production passwords, tokens, or personal information to GitHub.

---

# 26. Customer Registration

Customer registration is public:

```http
POST /customers
```

No Authorization header is required for registration.

Example:

```json
{
  "name": "Test User",
  "email": "test@example.com",
  "phone": "9000000000"
}
```

The API returns:

```text
customer_id
role
token
```

The customer token must be saved securely because the authorizer uses it for future requests.

---

# 27. Authentication

For protected API requests:

```http
Authorization: Bearer <TOKEN>
```

The Lambda authorizer:

1. Reads the Bearer token.
2. Hashes it using SHA-256.
3. Finds the matching customer.
4. Reads the customer role.
5. Checks the role against the requested HTTP method/path.
6. Returns the authenticated customer ID and role to API Gateway/Lambda.

---

# 28. User and Admin Authorization

## USER

Allowed:

```text
GET    /products
GET    /products/{productId}

GET    /orders
POST   /orders

GET    /orders/{orderId}
PUT    /orders/{orderId}

GET    /customers/{customerId}
PUT    /customers/{customerId}
```

A USER can only access their own customer/order data where the application enforces ownership.

## ADMIN

Allowed:

```text
GET    /products
POST   /products

GET    /products/{productId}
PUT    /products/{productId}
PATCH  /products/{productId}
DELETE /products/{productId}

GET    /orders
GET    /orders/{orderId}
PUT    /orders/{orderId}
PATCH  /orders/{orderId}

GET    /customers/{customerId}
PUT    /customers/{customerId}
```

ADMIN is intentionally **not allowed to POST `/orders`**.

---

# 29. Order Authorization Rules

The intended behavior is:

### USER placing an order

```http
POST /orders
Authorization: Bearer <USER_TOKEN>
```

The request's:

```json
{
  "customer_id": "CUST001"
}
```

must match the authenticated customer identity.

A USER cannot place an order for another customer.

### ADMIN placing an order

```http
POST /orders
Authorization: Bearer <ADMIN_TOKEN>
```

must return:

```text
403 Forbidden
```

### Get all orders

ADMIN:

```http
GET /orders
Authorization: Bearer <ADMIN_TOKEN>
```

returns all orders.

### Get own orders

USER:

```http
GET /orders
Authorization: Bearer <USER_TOKEN>
```

returns only that customer's orders.

### Customer ID query parameter

The API does not use:

```text
GET /orders?customer_id=...
```

The supported operation is:

```text
GET /orders
```

The authenticated identity determines the data returned.

---

# 30. API URL

After deployment, the workflow prints:

```text
API: https://<api-id>.execute-api.ap-south-1.amazonaws.com/dev
```

Save this as:

```text
API_BASE_URL
```

For example:

```text
API_BASE_URL=https://xxxxxxxxxx.execute-api.ap-south-1.amazonaws.com/dev
```

---

# 31. Basic API Tests

## Test public customer registration

```powershell
$body = @{
  name = "Test User"
  email = "test@example.com"
  phone = "9000000000"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "$API_BASE_URL/customers" `
  -ContentType "application/json" `
  -Body $body
```

Save the returned:

```text
customer_id
token
```

---

## Test products

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "$API_BASE_URL/products"
```

---

## Test authenticated orders

```powershell
$headers = @{
  Authorization = "Bearer <USER_TOKEN>"
}

Invoke-RestMethod `
  -Method Get `
  -Uri "$API_BASE_URL/orders" `
  -Headers $headers
```

---

# 32. Test POST /orders

Example request:

```powershell
$headers = @{
  Authorization = "Bearer <USER_TOKEN>"
}

$body = @{
  customer_id = "<AUTHENTICATED_CUSTOMER_ID>"
  items = @(
    @{
      product_id = 1
      quantity = 1
    }
  )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri "$API_BASE_URL/orders" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

The order may initially return:

```text
202 Accepted
```

because order processing is asynchronous.

The flow is:

```text
API Gateway
   ↓
Order Lambda
   ↓
DynamoDB/SQS processing flow
   ↓
Order Processor
   ↓
RDS/order state
   ↓
EventBridge
   ↓
SNS/reporting/metrics
```

Use the actual response from the deployed version when documenting final status values.

---

# 33. Test ADMIN Cannot Place Orders

Use the ADMIN token:

```powershell
$headers = @{
  Authorization = "Bearer <ADMIN_TOKEN>"
}
```

Then:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "$API_BASE_URL/orders" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Expected:

```text
403 Forbidden
```

---

# 34. Test ADMIN GET /orders

```powershell
$headers = @{
  Authorization = "Bearer <ADMIN_TOKEN>"
}

Invoke-RestMethod `
  -Method Get `
  -Uri "$API_BASE_URL/orders" `
  -Headers $headers
```

Expected:

```text
All orders
```

---

# 35. Test USER GET /orders

```powershell
$headers = @{
  Authorization = "Bearer <USER_TOKEN>"
}

Invoke-RestMethod `
  -Method Get `
  -Uri "$API_BASE_URL/orders" `
  -Headers $headers
```

Expected:

```text
Only authenticated user's orders
```

---

# 36. Test Unsupported Query Parameter

This is intentionally not the supported way to filter orders:

```text
GET /orders?customer_id=...
```

The Order Lambda rejects the query parameter rather than trusting a client-supplied customer ID.

Use:

```text
GET /orders
```

instead.

---

# 37. Dashboard Deployment

The dashboard is deployed to the EC2 instance automatically.

The flow is:

```text
GitHub Actions
      ↓
Dashboard ZIP
      ↓
S3
      ↓
AWS Systems Manager
      ↓
EC2
      ↓
Python virtual environment
      ↓
Gunicorn
      ↓
NGINX
      ↓
Browser
```

The workflow does not require manually SSHing into the EC2 instance.

---

# 38. EC2 SSM Requirements

The Network Stack creates an EC2 IAM role containing:

```text
AmazonSSMManagedInstanceCore
```

The EC2 bootstrap also installs and starts:

```text
amazon-ssm-agent
```

The workflow waits until:

```text
PingStatus = Online
```

before sending the dashboard deployment command.

---

# 39. Dashboard Services

The dashboard runs as:

```text
cloudmart-dashboard.service
```

using:

```text
Gunicorn
```

on:

```text
127.0.0.1:5000
```

NGINX listens on:

```text
port 80
```

and reverse proxies to:

```text
127.0.0.1:5000
```

The workflow installs missing EC2 packages automatically when required.

---

# 40. Dashboard URL

After successful deployment:

```text
http://<EC2_PUBLIC_IP>
```

The workflow prints:

```text
Dashboard: http://<public-ip>
```

Open that address in a browser.

---

# 41. Verify EC2 SSM

Get the EC2 instance ID:

```powershell
aws cloudformation describe-stacks `
  --region ap-south-1 `
  --stack-name cloudmart-network-stack-dev `
  --query "Stacks[0].Outputs[?OutputKey=='EC2InstanceId'].OutputValue" `
  --output text
```

Check SSM:

```powershell
aws ssm describe-instance-information `
  --region ap-south-1
```

The instance should show:

```text
PingStatus = Online
```

---

# 42. Verify Dashboard Through SSM

If the dashboard fails, retrieve the latest SSM command output from the GitHub Actions run first.

On the EC2 instance, the important services are:

```bash
systemctl status amazon-ssm-agent
systemctl status cloudmart-dashboard
systemctl status nginx
```

Logs:

```bash
journalctl -u cloudmart-dashboard --no-pager -n 100
```

```bash
journalctl -u nginx --no-pager -n 100
```

Health test:

```bash
curl http://127.0.0.1:5000/health
```

NGINX test:

```bash
nginx -t
```

---

# 43. CloudFormation Verification

List stacks:

```powershell
aws cloudformation list-stacks `
  --region ap-south-1 `
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE
```

Check individual stacks:

```powershell
aws cloudformation describe-stacks `
  --region ap-south-1 `
  --stack-name cloudmart-network-stack-dev
```

Repeat for:

```text
cloudmart-data-stack-dev
cloudmart-iam-stack-dev
cloudmart-auth-stack-dev
cloudmart-api-stack-dev
cloudmart-monitoring-stack-dev
```

---

# 44. Check CloudFormation Errors

For Network:

```powershell
aws cloudformation describe-stack-events `
  --region ap-south-1 `
  --stack-name cloudmart-network-stack-dev `
  --query "StackEvents[0:40].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" `
  --output table
```

For Data:

```powershell
aws cloudformation describe-stack-events `
  --region ap-south-1 `
  --stack-name cloudmart-data-stack-dev `
  --query "StackEvents[0:60].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" `
  --output table
```

Repeat the same pattern for IAM, Auth, API, and Monitoring.

---

# 45. Verify SSM Parameters

List CloudMart parameters:

```powershell
aws ssm get-parameters-by-path `
  --region ap-south-1 `
  --path /cloudmart/dev `
  --recursive
```

Expected parameters include:

```text
/cloudmart/dev/database/password
/cloudmart/dev/notification-email
/cloudmart/dev/reports-bucket
/cloudmart/dev/lambda-artifact-bucket
```

---

# 46. Verify Lambda Functions

List CloudMart functions:

```powershell
aws lambda list-functions `
  --region ap-south-1 `
  --query "Functions[?starts_with(FunctionName,'cloudmart-')].FunctionName" `
  --output table
```

Expected functions include names similar to:

```text
cloudmart-authorizer-dev
cloudmart-product-dev
cloudmart-customer-dev
cloudmart-order-dev
cloudmart-order-processor-dev
cloudmart-report-dev
```

---

# 47. Check Lambda Logs

Example:

```powershell
aws logs tail `
  /aws/lambda/cloudmart-order-dev `
  --region ap-south-1 `
  --since 30m
```

Order Processor:

```powershell
aws logs tail `
  /aws/lambda/cloudmart-order-processor-dev `
  --region ap-south-1 `
  --since 30m
```

Authorizer:

```powershell
aws logs tail `
  /aws/lambda/cloudmart-authorizer-dev `
  --region ap-south-1 `
  --since 30m
```

---

# 48. Check S3 Lambda Artifacts

Get the bucket:

```powershell
aws cloudformation describe-stacks `
  --region ap-south-1 `
  --stack-name cloudmart-data-stack-dev `
  --query "Stacks[0].Outputs[?OutputKey=='LambdaBucket'].OutputValue" `
  --output text
```

List objects:

```powershell
aws s3 ls s3://<LAMBDA_BUCKET>/ --recursive
```

Expected paths include:

```text
auth/
product/
customer/
order/
order-processor/
report/
dashboard/
```

---

# 49. Check SQS

List queues:

```powershell
aws sqs list-queues `
  --region ap-south-1
```

The order queue is environment-specific and follows:

```text
cloudmart-orders-dev
```

The project also creates a dead-letter queue.

---

# 50. Check EventBridge

List CloudMart rules:

```powershell
aws events list-rules `
  --region ap-south-1 `
  --query "Rules[?starts_with(Name,'cloudmart-')].[Name,State,ScheduleExpression]" `
  --output table
```

The daily reporting rule follows:

```text
cloudmart-daily-report-rule-dev
```

Verify it:

```powershell
aws events describe-rule `
  --region ap-south-1 `
  --name cloudmart-daily-report-rule-dev
```

---

# 51. Check CloudWatch Dashboard

The Monitoring Stack creates an operations dashboard similar to:

```text
cloudmart-operations-dev
```

Verify:

```powershell
aws cloudwatch get-dashboard `
  --region ap-south-1 `
  --dashboard-name cloudmart-operations-dev
```

---

# 52. Git Workflow Used in VS Code

After changing code:

## Step 1 – Check status

```powershell
git status
```

## Step 2 – Review changes

```powershell
git diff
```

For a specific file:

```powershell
git diff -- lambda/order/lambda_function.py
```

## Step 3 – Stage changes

To stage everything:

```powershell
git add .
```

Or stage selected files:

```powershell
git add cloudformation/
git add lambda/
git add .github/workflows/deploy.yml
```

## Step 4 – Check staged changes

```powershell
git diff --cached
```

## Step 5 – Commit

Example:

```powershell
git commit -m "Update CloudMart deployment"
```

## Step 6 – Push

```powershell
git push origin main
```

---

# 53. Recommended Single-Commit Workflow

If several related fixes are made, they do not need separate commits.

Use:

```powershell
git status
git add .
git diff --cached
git commit -m "Fix CloudMart deployment and authorization"
git push origin main
```

The GitHub Actions workflow will then run using the new commit SHA.

Because Lambda package names contain:

```text
${{ github.sha }}
```

the new commit automatically produces new Lambda artifacts.

---

# 54. If Git Says the Branch Is Behind

Run:

```powershell
git pull --rebase origin main
```

Then:

```powershell
git push origin main
```

If conflicts occur:

```powershell
git status
```

Resolve the conflicted files, then:

```powershell
git add .
git rebase --continue
git push origin main
```

---

# 55. If GitHub Actions Does Not Start

Check:

```text
.github/workflows/deploy.yml
```

Confirm the workflow contains:

```yaml
on:
  workflow_dispatch:
  push:
    branches:
      - main
      - master
```

Then verify the file is committed:

```powershell
git status
git log -1 -- .github/workflows/deploy.yml
```

---

# 56. GitHub OIDC Error

Typical error:

```text
Could not assume role with OIDC:
Not authorized to perform sts:AssumeRoleWithWebIdentity
```

Check all of these:

### 1. OIDC provider exists

```text
token.actions.githubusercontent.com
```

### 2. Audience is correct

```text
sts.amazonaws.com
```

### 3. Trust policy repository is correct

```text
repo:<OWNER>/<REPO>:ref:refs/heads/main
```

### 4. GitHub workflow has:

```yaml
permissions:
  id-token: write
  contents: read
```

### 5. `AWS_ROLE_ARN` is correct

### 6. Workflow is running from the branch allowed by the trust policy.

---

# 57. CloudFormation Export Errors

If an error says an export is already in use:

```text
Export ... is in use
```

find the owner:

```powershell
aws cloudformation list-exports `
  --region ap-south-1 `
  --output table
```

Check old stacks:

```powershell
aws cloudformation list-stacks `
  --region ap-south-1
```

The current workflow contains legacy stack cleanup because older stack names can retain exports.

Do not manually delete a stack that is still required by another stack without checking dependencies.

---

# 58. Data Stack Resource-Existence Errors

If CloudFormation reports:

```text
AWS::EarlyValidation::ResourceExistenceCheck
```

check the stack events:

```powershell
aws cloudformation describe-stack-events `
  --region ap-south-1 `
  --stack-name cloudmart-data-stack-dev `
  --query "StackEvents[0:60].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" `
  --output table
```

The important field is:

```text
ResourceStatusReason
```

This identifies the actual resource collision.

---

# 59. API Gateway 502 – Malformed Lambda Proxy Response

If API Gateway returns:

```json
{
  "message": "Internal server error"
}
```

and CloudWatch shows:

```text
Malformed Lambda proxy response
```

verify the Lambda is returning:

```json
{
  "statusCode": 200,
  "headers": {},
  "body": "..."
}
```

not:

```json
{
  "statusCode": 200,
  "message": "..."
}
```

The Lambda package attached to API Gateway must also be the correct application Lambda.

For:

```text
POST /orders
```

API Gateway must invoke:

```text
cloudmart-order-dev
```

and not:

```text
cloudmart-order-processor-dev
```

The Order Processor expects SQS events.

---

# 60. Dashboard Not Opening

Check the workflow step:

```text
Deploy Dashboard to EC2
```

Then check:

```text
SSM Online
S3 dashboard ZIP
DB host configuration
Reports bucket SSM parameter
Gunicorn
NGINX
```

On EC2:

```bash
systemctl status amazon-ssm-agent
systemctl status cloudmart-dashboard
systemctl status nginx
```

Check:

```bash
journalctl -u cloudmart-dashboard --no-pager -n 100
```

and:

```bash
journalctl -u nginx --no-pager -n 100
```

---

# 61. Dashboard Shows NGINX Welcome Page

This normally means NGINX is running but the CloudMart reverse-proxy configuration is not active.

Check:

```bash
nginx -t
```

Check:

```bash
cat /etc/nginx/conf.d/cloudmart.conf
```

The proxy should point to:

```text
127.0.0.1:5000
```

Then:

```bash
systemctl restart nginx
```

---

# 62. Gunicorn Is Not Running

Check:

```bash
systemctl status cloudmart-dashboard
```

Then:

```bash
journalctl -u cloudmart-dashboard --no-pager -n 100
```

Test directly:

```bash
curl http://127.0.0.1:5000/health
```

If the virtual environment is missing:

```text
/opt/cloudmart-dashboard/venv
```

rerun the GitHub Actions dashboard deployment rather than manually installing random packages.

---

# 63. SSM EC2 Instance Is Offline

Check:

```powershell
aws ssm describe-instance-information `
  --region ap-south-1
```

The EC2 instance must have:

```text
AmazonSSMManagedInstanceCore
```

through its instance role.

The instance also needs access to SSM through the VPC/network configuration.

Check EC2:

```powershell
aws ec2 describe-instances `
  --region ap-south-1 `
  --instance-ids <INSTANCE_ID>
```

---

# 64. Database Connection Problems

Check the Data Stack:

```powershell
aws cloudformation describe-stacks `
  --region ap-south-1 `
  --stack-name cloudmart-data-stack-dev
```

Get DB endpoint:

```powershell
aws cloudformation describe-stacks `
  --region ap-south-1 `
  --stack-name cloudmart-data-stack-dev `
  --query "Stacks[0].Outputs[?OutputKey=='DatabaseEndpoint'].OutputValue" `
  --output text
```

Check SSM password parameter:

```powershell
aws ssm get-parameter `
  --region ap-south-1 `
  --name /cloudmart/dev/database/password
```

Do not use `--with-decryption` unless you actually need to inspect the secret.

---

# 65. Security Group Expectations

The intended design is:

```text
Internet
   |
   v
Public EC2
   |
   v
Private application resources
   |
   v
Private RDS
```

RDS MySQL should not be open to:

```text
0.0.0.0/0
```

Port:

```text
3306
```

should be reachable only from authorized application resources.

---

# 66. VPC Endpoints

The Network Stack creates private connectivity for services including:

```text
S3
SSM
SNS
SQS
EventBridge
CloudWatch Logs
CloudWatch Monitoring
```

This allows private workloads to access the required AWS services without relying on a NAT Gateway for these services.

---

# 67. Monitoring

The Monitoring Stack creates CloudWatch alarms for areas such as:

```text
Reports generation failures
Orders failed
Low stock events
Order Processor Lambda errors
RDS CPU
API Gateway latency
API Gateway 4XX/throttling indicator
```

The workflow verifies that at least four CloudWatch alarms exist and that the operations dashboard is available.

---

# 68. Daily Reports

The daily report process uses:

```text
EventBridge
    ↓
Report Lambda
    ↓
S3 Reports Bucket
```

The rule follows:

```text
cloudmart-daily-report-rule-dev
```

Verify:

```powershell
aws events describe-rule `
  --region ap-south-1 `
  --name cloudmart-daily-report-rule-dev
```

List report objects:

```powershell
aws s3 ls s3://<REPORTS_BUCKET>/ --recursive
```

---

# 69. SNS Email Notifications

The project uses the notification email from:

```text
/cloudmart/dev/notification-email
```

SNS topics include:

```text
Order confirmations
Low-stock alerts
Order failures
```

If the email subscription requires confirmation, check the email inbox and confirm the SNS subscription.

---

# 70. Final Deployment Checklist

Before considering deployment complete, verify:

### GitHub

- [ ] Repository exists.
- [ ] `.github/workflows/deploy.yml` exists.
- [ ] Workflow is enabled.
- [ ] `AWS_ROLE_ARN` secret exists.
- [ ] `DB_PASSWORD` secret exists.
- [ ] `EMAIL_NOTIFICATION` secret exists.

### AWS IAM

- [ ] GitHub OIDC provider exists.
- [ ] GitHub deployment role exists.
- [ ] Trust policy references correct repository/branch.
- [ ] Deployment role has required permissions.
- [ ] `iam:PassRole` works.

### SSM

- [ ] `/cloudmart/dev/database/password`
- [ ] `/cloudmart/dev/notification-email`
- [ ] `/cloudmart/dev/reports-bucket`
- [ ] `/cloudmart/dev/lambda-artifact-bucket`

### CloudFormation

- [ ] Network stack = `CREATE_COMPLETE` or `UPDATE_COMPLETE`
- [ ] Data stack = `CREATE_COMPLETE` or `UPDATE_COMPLETE`
- [ ] IAM stack = `CREATE_COMPLETE` or `UPDATE_COMPLETE`
- [ ] Auth stack = `CREATE_COMPLETE` or `UPDATE_COMPLETE`
- [ ] API stack = `CREATE_COMPLETE` or `UPDATE_COMPLETE`
- [ ] Monitoring stack = `CREATE_COMPLETE` or `UPDATE_COMPLETE`

### Lambda

- [ ] Authorizer deployed.
- [ ] Product Lambda deployed.
- [ ] Customer Lambda deployed.
- [ ] Order Lambda deployed.
- [ ] Order Processor deployed.
- [ ] Report Lambda deployed.
- [ ] Lambda packages exist in S3.

### Database

- [ ] RDS available.
- [ ] `cloudmart` database initialized.
- [ ] Tables exist.
- [ ] Sample products/customers available.

### EC2

- [ ] EC2 instance is running.
- [ ] SSM PingStatus = Online.
- [ ] Dashboard service is active.
- [ ] NGINX is active.
- [ ] `/health` works.

### API

- [ ] API URL is available.
- [ ] `GET /products` works.
- [ ] `POST /customers` works.
- [ ] USER token works.
- [ ] ADMIN token works.
- [ ] USER can only see own orders.
- [ ] ADMIN can see all orders.
- [ ] ADMIN cannot POST `/orders`.
- [ ] USER cannot place an order for another customer.

### Monitoring

- [ ] CloudWatch dashboard exists.
- [ ] CloudWatch alarms exist.
- [ ] EventBridge daily report rule is enabled.
- [ ] SQS queue exists.
- [ ] SNS topics exist.

---

# 71. Complete Deployment Flow – Short Version

For a new person taking the repository:

```text
1. Clone GitHub repository
          ↓
2. Create AWS account/choose region
          ↓
3. Create GitHub OIDC provider in IAM
          ↓
4. Create GitHub Actions IAM role
          ↓
5. Restrict role trust to GitHub repo/main
          ↓
6. Attach deployment permissions
          ↓
7. Add GitHub Secrets
       AWS_ROLE_ARN
       DB_PASSWORD
       EMAIL_NOTIFICATION
          ↓
8. Push/run GitHub Actions
          ↓
9. Workflow creates SSM parameters
          ↓
10. Network Stack
          ↓
11. Data Stack
          ↓
12. IAM Stack
          ↓
13. Lambda packages uploaded to S3
          ↓
14. Auth Stack
          ↓
15. API Stack
          ↓
16. Dashboard package uploaded to S3
          ↓
17. SSM deploys dashboard to EC2
          ↓
18. RDS schema initialized
          ↓
19. Monitoring Stack
          ↓
20. Test API
          ↓
21. Test Dashboard
          ↓
22. Verify CloudWatch/SQS/SNS/EventBridge
```

---

# 72. Cleanup / Delete Project

Because the project creates many AWS resources, deletion should be performed carefully.

First list stacks:

```powershell
aws cloudformation list-stacks `
  --region ap-south-1
```

Delete in reverse dependency order:

```powershell
aws cloudformation delete-stack `
  --region ap-south-1 `
  --stack-name cloudmart-monitoring-stack-dev
```

```powershell
aws cloudformation delete-stack `
  --region ap-south-1 `
  --stack-name cloudmart-api-stack-dev
```

```powershell
aws cloudformation delete-stack `
  --region ap-south-1 `
  --stack-name cloudmart-auth-stack-dev
```

```powershell
aws cloudformation delete-stack `
  --region ap-south-1 `
  --stack-name cloudmart-iam-stack-dev
```

```powershell
aws cloudformation delete-stack `
  --region ap-south-1 `
  --stack-name cloudmart-data-stack-dev
```

```powershell
aws cloudformation delete-stack `
  --region ap-south-1 `
  --stack-name cloudmart-network-stack-dev
```

Wait for completion before deleting dependent resources.

Check:

```powershell
aws cloudformation describe-stacks `
  --region ap-south-1 `
  --stack-name cloudmart-data-stack-dev
```

> S3 buckets may contain objects and can therefore prevent CloudFormation deletion. Empty them first if CloudFormation reports that the bucket is not empty.

---

# 73. Important Secrets Rule

Never commit:

```text
DB_PASSWORD
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN
GitHub tokens
API tokens
private keys
production credentials
```

to GitHub.

Only the secret **names** belong in documentation:

```text
AWS_ROLE_ARN
DB_PASSWORD
EMAIL_NOTIFICATION
```

Actual values must remain in:

```text
GitHub Secrets
AWS SSM Parameter Store
```

as appropriate.

---

# 74. Important Rebuild Note

The repository contains the application code and CloudFormation templates, but the external GitHub OIDC provider and GitHub deployment role are account-level AWS resources and are not recreated simply by cloning the repository.

Therefore, a new AWS account must perform the IAM/OIDC bootstrap in Sections 7–11 before the GitHub Actions workflow can deploy the application.

Once that one-time bootstrap is complete, normal deployments are:

```powershell
git add .
git commit -m "Your change"
git push origin main
```

and GitHub Actions performs the AWS deployment automatically.

---

# 75. Final "Take This Project and Run It" Procedure

If a new developer receives only the GitHub repository, they should follow exactly this order:

```text
A. Clone repository
B. Install Git/AWS CLI
C. Configure temporary/local AWS admin access
D. Create GitHub OIDC provider
E. Create GitHub Actions deployment role
F. Configure OIDC trust policy
G. Attach deployment permissions
H. Create GitHub Secrets
I. Confirm ENVIRONMENT_NAME=dev
J. Run GitHub Actions
K. Wait for all six CloudFormation stacks
L. Confirm EC2 SSM Online
M. Confirm dashboard deployment
N. Confirm RDS schema initialization
O. Get API URL
P. Register/test a customer
Q. Test USER authorization
R. Test ADMIN authorization
S. Test order flow
T. Verify SQS/EventBridge/SNS
U. Verify CloudWatch monitoring
```

At that point the complete CloudMart environment is deployed and operational.

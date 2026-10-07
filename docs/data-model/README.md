
# CloudMart Data Model Documentation

## 1. Overview

CloudMart uses **Amazon RDS MySQL** as the relational database for the
application’s core business data.

The current database schema is:

- `customers`
- `products`
- `orders`
- `order_items`
- `audit_logs`

The schema is defined in `schema.sql`.

The database is named `cloudmart`.


------------------------------------------------------------------------

## 2. Database

| Property               | Value                                                |
|------------------------|------------------------------------------------------|
| Database               | `cloudmart`                                          |
| Database engine        | MySQL                                                |
| Primary storage        | Amazon RDS MySQL                                     |
| Main business entities | Customers, Products, Orders, Order Items, Audit Logs |

The schema starts by creating the database if it does not already exist
and then selects it with `USE cloudmart`.

------------------------------------------------------------------------

## 3. Entity Relationship Overview

 
                    ┌──────────────────┐
                    │    CUSTOMERS     │
                    │──────────────────│
                    │ PK customer_id   │
                    │ name             │
                    │ email            │
                    │ phone            │
                    │ auth_token_hash  │
                    │ role             │
                    │ created_at       │
                    │ updated_at       │
                    └────────┬─────────┘
                             │
                   1         │        N
                             ▼
                    ┌──────────────────┐
                    │      ORDERS      │
                    │──────────────────│
                    │ PK order_id     │
                    │ FK customer_id  │
                    │ status           │
                    │ total_amount     │
                    │ created_at       │
                    │ updated_at       │
                    └────────┬─────────┘
                             │
                   1         │        N
                             ▼
                    ┌──────────────────┐
                    │   ORDER_ITEMS    │
                    │──────────────────│
                    │ PK order_item_id│
                    │ FK order_id     │
                    │ FK product_id   │
                    │ quantity        │
                    │ unit_price      │
                    │ subtotal        │
                    │ created_at      │
                    └────────┬─────────┘
                             │
                   N         │        1
                             ▼
                    ┌──────────────────┐
                    │     PRODUCTS     │
                    │──────────────────│
                    │ PK product_id   │
                    │ name             │
                    │ description      │
                    │ price            │
                    │ stock_count      │
                    │ status           │
                    │ created_at       │
                    │ updated_at       │
                    └──────────────────┘

CUSTOMERS
    │
    │ 1:N
    ▼
AUDIT_LOGS
  

### Relationship summary

 
Customer 1 ──── N Orders
Order    1 ──── N Order Items
Product  1 ──── N Order Items
Customer 1 ──── N Audit Logs
  

`order_items` acts as the relationship between orders and products.

------------------------------------------------------------------------

# 4. Customers Table

## Purpose

The `customers` table stores customer identity, contact information,
authentication information, and role.

### Columns

| Column            | Type           | Key / Constraint          | Purpose                           |
|-------------------|----------------|---------------------------|-----------------------------------|
| `customer_id`     | `VARCHAR(100)` | PK                        | Unique customer identifier        |
| `name`            | `VARCHAR(100)` | NOT NULL                  | Customer name                     |
| `email`           | `VARCHAR(255)` | UNIQUE, NOT NULL          | Customer email                    |
| `phone`           | `VARCHAR(20)`  | —                         | Customer phone number             |
| `auth_token_hash` | `VARCHAR(64)`  | NOT NULL                  | SHA-256 authentication token hash |
| `role`            | `VARCHAR(20)`  | NOT NULL                  | User authorization role           |
| `created_at`      | `TIMESTAMP`    | Default current timestamp | Creation time                     |
| `updated_at`      | `DATETIME`     | Auto-updated              | Last modification time            |



### Important design point

The database stores `auth_token_hash`, rather than storing the raw
authentication token.

The Lambda Authorizer uses customer authentication information when
validating requests.

------------------------------------------------------------------------

# 5. Products Table

## Purpose

The `products` table stores product information and current inventory
quantity.

### Columns

| Column        | Type            | Key / Constraint          | Purpose                 |
|---------------|-----------------|---------------------------|-------------------------|
| `product_id`  | `INT`           | PK, AUTO_INCREMENT        | Product identifier      |
| `name`        | `VARCHAR(255)`  | NOT NULL                  | Product name            |
| `description` | `TEXT`          | —                         | Product description     |
| `price`       | `DECIMAL(10,2)` | NOT NULL                  | Product price           |
| `stock_count` | `INT`           | NOT NULL                  | Current available stock |
| `status`      | `VARCHAR(20)`   | NOT NULL                  | Product status          |
| `created_at`  | `TIMESTAMP`     | Default current timestamp | Creation time           |
| `updated_at`  | `TIMESTAMP`     | Auto-updated              | Last modification time  |



### Product status

The current schema defaults product status to:

 
ACTIVE
  

------------------------------------------------------------------------

# 6. Orders Table

## Purpose

The `orders` table represents a customer’s order and its processing
state.

### Columns

| Column         | Type            | Key / Constraint          | Purpose                       |
|----------------|-----------------|---------------------------|-------------------------------|
| `order_id`     | `VARCHAR(50)`   | PK                        | Unique order identifier       |
| `customer_id`  | `VARCHAR(100)`  | FK, NOT NULL              | Customer who placed the order |
| `status`       | `VARCHAR(30)`   | NOT NULL                  | Current order state           |
| `total_amount` | `DECIMAL(12,2)` | NOT NULL                  | Total order amount            |
| `created_at`   | `DATETIME`      | Default current timestamp | Order creation time           |
| `updated_at`   | `DATETIME`      | Auto-updated              | Last update time              |

### Order status values

The schema allows:

 
PROCESSING
CONFIRMED
SHIPPED
DELIVERED
CANCELLED
FAILED
  

The default status for a new order is:

 
PROCESSING
  

This matches the asynchronous order-processing architecture:

 
POST /orders
     ↓
Order Lambda
     ↓
Order stored as PROCESSING
     ↓
SQS
     ↓
Order Processor Lambda
     ↓
CONFIRMED or FAILED
  

### Customer relationship

 
orders.customer_id
        ↓
customers.customer_id
  

The foreign key uses:

 
ON DELETE RESTRICT
ON UPDATE CASCADE
  

Therefore, a customer cannot be deleted if existing orders still
reference that customer.

------------------------------------------------------------------------

# 7. Order Items Table

## Purpose

The `order_items` table stores the individual products included in each
order.

An order can contain multiple products.

### Columns

| Column          | Type            | Key / Constraint          | Purpose                      |
|-----------------|-----------------|---------------------------|------------------------------|
| `order_item_id` | `INT`           | PK, AUTO_INCREMENT        | Unique order-item identifier |
| `order_id`      | `VARCHAR(50)`   | FK, NOT NULL              | Parent order                 |
| `product_id`    | `INT`           | FK, NOT NULL              | Product included in order    |
| `quantity`      | `INT`           | NOT NULL                  | Number of units ordered      |
| `unit_price`    | `DECIMAL(12,2)` | NOT NULL                  | Price per unit               |
| `subtotal`      | `DECIMAL(12,2)` | NOT NULL                  | Line-item total              |
| `created_at`    | `DATETIME`      | Default current timestamp | Creation time                |

### Relationships

 
orders.order_id
      │
      │ 1:N
      ▼
order_items.order_id
  

and:

 
products.product_id
      │
      │ 1:N
      ▼
order_items.product_id
  


  

### Delete behavior

For an order:

 
Order
  ↓ delete
Order Items
  ↓
CASCADE
  

Deleting an order cascades to its order items.

For products:

 
Product
  ↓ delete
Referenced Order Items
  

Deletion is restricted because historical order items may refer to the
product.

------------------------------------------------------------------------

# 8. Audit Logs Table

## Purpose

The `audit_logs` table records important actions performed against
CloudMart entities.

This provides an operational history for important business activities.

### Columns

| Column        | Type           | Key / Constraint          | Purpose                         |
|---------------|----------------|---------------------------|---------------------------------|
| `audit_id`    | `BIGINT`       | PK, AUTO_INCREMENT        | Audit record identifier         |
| `customer_id` | `VARCHAR(100)` | FK                        | Customer associated with action |
| `action`      | `VARCHAR(100)` | NOT NULL                  | Action performed                |
| `entity_type` | `VARCHAR(100)` | —                         | Type of entity affected         |
| `entity_id`   | `VARCHAR(100)` | —                         | Identifier of affected entity   |
| `details`     | `TEXT`         | —                         | Additional action information   |
| `created_at`  | `DATETIME`     | Default current timestamp | Time of audit event             |

### Customer relationship

 
customers.customer_id
        │
        │ 1:N
        ▼
audit_logs.customer_id
  

The foreign key uses:

 
ON DELETE SET NULL
ON UPDATE CASCADE
  

Therefore, if the associated customer is deleted, the audit record can
remain while its `customer_id` becomes NULL.

------------------------------------------------------------------------

# 9. Relationship Details

## Customer → Orders

 
customers
    │
    │ customer_id
    ▼
orders.customer_id
  

Relationship:

 
1 Customer → Many Orders
  

A customer can place multiple orders.

------------------------------------------------------------------------

## Order → Order Items

 
orders
   │
   │ order_id
   ▼
order_items.order_id
  

Relationship:

 
1 Order → Many Order Items
  

For example:

 
Order #1001
│
├── Laptop × 1
├── Mouse × 2
└── Keyboard × 1
  

Each line is represented by a row in `order_items`.

------------------------------------------------------------------------

## Product → Order Items

 
products
   │
   │ product_id
   ▼
order_items.product_id
  

Relationship:

 
1 Product → Many Order Items
  

A product can appear in many different orders.

------------------------------------------------------------------------

## Customer → Audit Logs

 
customers
   │
   │ customer_id
   ▼
audit_logs.customer_id
  

Relationship:

 
1 Customer → Many Audit Logs
  

------------------------------------------------------------------------

# 10. Why `order_items` is necessary

You should be able to explain this in a project review.

An `orders` row represents the order itself:

 
order_id
customer_id
status
total_amount
  

But it doesn’t tell us which products were purchased.

That information belongs in `order_items`.

Example:

 
orders

ORDER001 | CUST101 | CONFIRMED | 68200
  

and:

 
order_items

ORDER001 | Laptop    | 1 | 65000
ORDER001 | Mouse     | 2 | 1200
  

This separates:

 
Order header
  

from:

 
Order line items
  

and allows one order to contain multiple products.

------------------------------------------------------------------------

# 11. Inventory Model

Inventory is represented in the current schema through the
`products.stock_count` column.

There is **not a separate `inventory` table** in the current
`schema.sql`.

The current model is:

 
products
│
├── product_id
├── name
├── price
├── stock_count
└── status
  

The `stock_count` value represents current available inventory.

Therefore:

 
Product
   │
   └── stock_count = current stock
  

The Order Processor Lambda checks and updates this value while
processing an order.

------------------------------------------------------------------------

# 12. Order Processing and Data Model

The database model supports the asynchronous order flow:

 
Client
  │
  ▼
API Gateway
  │
  ▼
Order Lambda
  │
  ├── Create Order
  │       status = PROCESSING
  │
  └── Send SQS message
            │
            ▼
      Order Processor Lambda
            │
            ▼
        RDS MySQL
            │
            ├── Read Order
            ├── Read Order Items
            ├── Check Product Stock
            ├── Update Product Stock
            ├── Update Order Status
            └── Write Audit Log
  

Possible final order states include:

 
PROCESSING
CONFIRMED
FAILED
  

and the schema also permits:


SHIPPED
DELIVERED
CANCELLED
  

------------------------------------------------------------------------

# 13. Database Constraints

The schema uses database-level constraints to protect data integrity.

### Primary keys

Every main table has a primary key:


customers.customer_id
products.product_id
orders.order_id
order_items.order_item_id
audit_logs.audit_id


### Foreign keys


orders.customer_id
        → customers.customer_id

order_items.order_id
        → orders.order_id

order_items.product_id
        → products.product_id

audit_logs.customer_id
        → customers.customer_id




These constraints prevent invalid data from being inserted into the
database.

------------------------------------------------------------------------

# 14. Indexing

The current `schema.sql` defines the table structures and verification
queries. The database design also relies on primary keys and foreign-key
relationships for common lookups.

When additional indexes are introduced for operational queries, useful
candidates include:


orders.customer_id
orders.status
orders.created_at
order_items.product_id
audit_logs.entity_type + entity_id
audit_logs.action
audit_logs.created_at



------------------------------------------------------------------------

# 15. Data Model and Application Components

| Component              | Main Data Responsibility                                   |
|------------------------|------------------------------------------------------------|
| Customer Lambda        | Customer creation and customer-related operations          |
| Product Lambda         | Product and inventory operations                           |
| Order Lambda           | Creates and stores orders                                  |
| Order Processor Lambda | Processes orders, checks stock, updates order/product data |
| Authorizer Lambda      | Uses customer authentication/role information              |
| Report Lambda          | Reads required data and generates reports                  |
| EC2 Flask Dashboard    | Reads database data and displays metrics/reports           |
| RDS MySQL              | Persistent relational data store                           |

------------------------------------------------------------------------

# 17. Data Security

The RDS database is private.

The application uses:


Lambda
   ↓
Private VPC networking
   ↓
RDS MySQL



The database security model should therefore be explained as:


Private RDS
    +
Security Groups
    +
Private Lambda networking
    +
SSM parameter for password


------------------------------------------------------------------------

# 18. Schema Initialization

The current deployment design does not require the Dashboard to create
database tables.

The flow is:


Data Stack
    │
    ▼
Creates RDS MySQL
    │
    ▼
Order Lambda deployment package
contains schema.sql
    │
    ▼
GitHub Actions invokes
Order Lambda
with:
action = initialize_schema
    │
    ▼
Order Lambda reads schema.sql
    │
    ▼
SQL executed against RDS
    │
    ▼
CloudMart tables created






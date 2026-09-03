-- =====================================================
-- CLOUDMART DATABASE SCHEMA
-- =====================================================
-- Database:
-- cloudmart
--
-- Tables:
-- customers
-- products
-- inventory
-- orders
-- order_items
--
-- Relationships:
--
-- customers
--     |
--     | 1 : many
--     v
-- orders
--
-- products
--     |
--     | 1 : many
--     v
-- inventory
--
-- orders
--     |
--     | 1 : many
--     v
-- order_items
--     ^
--     |
-- products
--
-- =====================================================


-- =====================================================
-- CUSTOMERS TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS customers (

    customer_id VARCHAR(100) NOT NULL,

    name VARCHAR(100) NOT NULL,

    email VARCHAR(255) NOT NULL,

    phone VARCHAR(20),

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (customer_id),

    UNIQUE KEY uk_customers_email (email)

) ENGINE=InnoDB;


-- =====================================================
-- PRODUCTS TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS products (

    product_id INT AUTO_INCREMENT PRIMARY KEY,

    name VARCHAR(255) NOT NULL,

    description TEXT,

    price DECIMAL(10,2) NOT NULL,

    stock_count INT NOT NULL DEFAULT 0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP

) ENGINE=InnoDB;


-- =====================================================
-- INVENTORY TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS inventory (

    inventory_id INT AUTO_INCREMENT PRIMARY KEY,

    product_id INT NOT NULL,

    quantity INT NOT NULL DEFAULT 0,

    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_inventory_product
        FOREIGN KEY (product_id)
        REFERENCES products(product_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    UNIQUE KEY uk_inventory_product (product_id)

) ENGINE=InnoDB;


-- =====================================================
-- ORDERS TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS orders (

    order_id VARCHAR(50) NOT NULL,

    customer_id VARCHAR(100) NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'PROCESSING',

    total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (order_id),

    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE

) ENGINE=InnoDB;


-- =====================================================
-- ORDER ITEMS TABLE
-- =====================================================

CREATE TABLE IF NOT EXISTS order_items (

    order_id VARCHAR(50) NOT NULL,

    product_id INT NOT NULL,

    quantity INT NOT NULL,

    price DECIMAL(10,2) NOT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (order_id, product_id),

    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id)
        REFERENCES orders(order_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id)
        REFERENCES products(product_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE

) ENGINE=InnoDB;


-- =====================================================
-- INDEXES
-- =====================================================

CREATE INDEX idx_product_name
ON products(name);


CREATE INDEX idx_orders_customer
ON orders(customer_id);


CREATE INDEX idx_orders_status
ON orders(status);


CREATE INDEX idx_orders_created_at
ON orders(created_at);


CREATE INDEX idx_order_items_product
ON order_items(product_id);


-- =====================================================
-- SAMPLE CUSTOMERS
-- =====================================================

INSERT INTO customers
(customer_id, name, email, phone)
VALUES
(
    'CUST101',
    'Akshay',
    'akshay@example.com',
    '9876543210'
),
(
    'CUST102',
    'Rahul',
    'rahul@example.com',
    '9876543211'
),
(
    'CUST103',
    'Priya',
    'priya@example.com',
    '9876543212'
)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    phone = VALUES(phone);


-- =====================================================
-- SAMPLE PRODUCTS
-- =====================================================

INSERT INTO products
(name, description, price, stock_count)
VALUES
(
    'Laptop',
    'Gaming Laptop',
    75000.00,
    10
),
(
    'Mouse',
    'Wireless Mouse',
    1500.00,
    25
),
(
    'Keyboard',
    'Mechanical Keyboard',
    3500.00,
    15
);


-- =====================================================
-- SAMPLE INVENTORY
-- =====================================================

INSERT INTO inventory
(product_id, quantity)
SELECT
    product_id,
    stock_count
FROM products
WHERE product_id IN (1, 2, 3)
ON DUPLICATE KEY UPDATE
    quantity = VALUES(quantity);


-- =====================================================
-- SAMPLE ORDER
-- =====================================================
-- This demonstrates the relationship:
--
-- customers -> orders
-- orders -> order_items
-- products -> order_items
--
-- =====================================================

INSERT INTO orders
(
    order_id,
    customer_id,
    status,
    total_amount
)
VALUES
(
    'ORD1001',
    'CUST101',
    'PROCESSING',
    75000.00
)
ON DUPLICATE KEY UPDATE
    customer_id = VALUES(customer_id),
    status = VALUES(status),
    total_amount = VALUES(total_amount);


-- =====================================================
-- SAMPLE ORDER ITEM
-- =====================================================

INSERT INTO order_items
(
    order_id,
    product_id,
    quantity,
    price
)
VALUES
(
    'ORD1001',
    1,
    1,
    75000.00
)
ON DUPLICATE KEY UPDATE
    quantity = VALUES(quantity),
    price = VALUES(price);


-- =====================================================
-- END OF CLOUDMART DATABASE SCHEMA
-- =====================================================
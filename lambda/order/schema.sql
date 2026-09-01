-- =========================================================
-- CLOUDMART ORDER TABLE
-- =========================================================

CREATE TABLE IF NOT EXISTS orders (
    order_id INT AUTO_INCREMENT PRIMARY KEY,

    customer_id VARCHAR(100) NOT NULL,

    product_id INT NOT NULL,

    quantity INT NOT NULL,

    total_amount DECIMAL(10, 2) NOT NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',

    failure_reason VARCHAR(255) NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    -- =====================================================
    -- INDEXES
    -- =====================================================

    INDEX idx_orders_customer_id (customer_id),

    INDEX idx_orders_status (status),

    INDEX idx_orders_product_id (product_id),

    -- =====================================================
    -- PRODUCT FOREIGN KEY
    -- =====================================================

    CONSTRAINT fk_orders_product
        FOREIGN KEY (product_id)
        REFERENCES products(product_id)
);
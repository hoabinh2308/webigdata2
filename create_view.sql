USE ProductAnalytics;
GO
CREATE OR ALTER VIEW dbo.vw_ProductFull AS
SELECT p.product_id AS pid, s.seller_id, s.seller_name, s.seller_province, s.seller_region,
       p.source_product_id, p.name, b.brand_name AS brand, c.category_name AS category,
       p.source_category, p.search_keyword, p.price, p.original_price, p.discount_rate,
       p.rating, p.review_count, p.sold
FROM dbo.Product p
JOIN dbo.Seller   s ON s.seller_id   = p.seller_id
JOIN dbo.Brand    b ON b.brand_id    = p.brand_id
JOIN dbo.Category c ON c.category_id = p.category_id;
GO
SELECT COUNT(*) AS so_dong FROM dbo.vw_ProductFull;  -- phải ra 9598

-- 迁移脚本：添加 parent_id 字段和新状态
-- 用途：支持物品分割追溯和废弃状态
-- 执行方式：psql -U <user> -d <database> -f sql/add_parent_id_and_statuses.sql

-- 1. 添加 parent_id 字段（如果不存在）
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name='inventory' AND column_name='parent_id'
    ) THEN
        ALTER TABLE inventory ADD COLUMN parent_id INTEGER;
        ALTER TABLE inventory ADD CONSTRAINT fk_parent
            FOREIGN KEY (parent_id) REFERENCES inventory(id);
        COMMENT ON COLUMN inventory.parent_id IS '父物品ID，用于追溯分割来源';
    END IF;
END $$;

-- 2. 更新 status 字段的约束（如果需要）
-- 注意：这会删除旧约束并添加新约束
DO $$ 
BEGIN
    -- 删除旧的 check 约束（如果存在）
    IF EXISTS (
        SELECT 1 
        FROM information_schema.constraint_column_usage 
        WHERE table_name='inventory' AND constraint_name LIKE '%status%'
    ) THEN
        ALTER TABLE inventory DROP CONSTRAINT IF EXISTS inventory_status_check;
    END IF;
    
    -- 添加新的 check 约束
    ALTER TABLE inventory ADD CONSTRAINT inventory_status_check
        CHECK (status IN ('in_stock', 'consumed', 'processed', 'waste'));
END $$;

-- 3. 创建索引以提高查询性能
CREATE INDEX IF NOT EXISTS idx_inventory_parent_id ON inventory(parent_id);
CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory(status);

-- 4. 验证
SELECT 
    column_name, 
    data_type, 
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'inventory' 
ORDER BY ordinal_position;

COMMENT ON TABLE inventory IS '库存表，支持物品分割追溯（parent_id）和多种状态（in_stock/consumed/processed/waste）';

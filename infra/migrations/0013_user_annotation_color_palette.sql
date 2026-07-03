-- R1 Reader Record user_highlight color contract
--
-- Product palette is fixed to warm_yellow / soft_mint / soft_rose.
-- No legacy color compatibility is kept. Existing rows with removed color
-- tokens are tombstoned and normalized before tightening the CHECK so the
-- database contract cannot diverge from backend/shared DTO validation.

UPDATE user_annotations
SET deleted_at = COALESCE(deleted_at, NOW()),
    color = 'warm_yellow',
    updated_at = NOW()
WHERE color IN ('soft_green', 'soft_blue', 'soft_purple', 'sage_green');

ALTER TABLE user_annotations
    ALTER COLUMN color SET DEFAULT 'warm_yellow';

ALTER TABLE user_annotations
    DROP CONSTRAINT IF EXISTS user_annotations_color_check;

ALTER TABLE user_annotations
    ADD CONSTRAINT user_annotations_color_check
        CHECK (color IN ('warm_yellow', 'soft_mint', 'soft_rose'));

COMMENT ON COLUMN user_annotations.color IS
    '用户高亮颜色，固定支持 warm_yellow、soft_mint、soft_rose。';

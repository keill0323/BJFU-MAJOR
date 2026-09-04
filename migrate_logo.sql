-- 队伍自定义 Logo 功能：teams 表新增 logo 字段
ALTER TABLE teams ADD COLUMN logo VARCHAR(255) NULL COMMENT '队伍Logo图片路径';

-- 赛程时间协商功能：为 match_rounds 表添加双方确认字段
ALTER TABLE match_rounds
  ADD COLUMN team1_confirmed TINYINT(1) NOT NULL DEFAULT 0 COMMENT '队伍1是否已确认比赛时间',
  ADD COLUMN team2_confirmed TINYINT(1) NOT NULL DEFAULT 0 COMMENT '队伍2是否已确认比赛时间';

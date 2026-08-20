SELECT id, wx_openid, nickname, game_id, student_id, is_verified, role FROM users WHERE id = 96;
SELECT 'teams' AS tbl, id, name, captain_id FROM teams WHERE captain_id = 96 OR id = 96;
SELECT 'team_members' AS tbl, id, team_id, user_id FROM team_members WHERE user_id = 96;
SELECT 'registrations' AS tbl, id, match_id, team_id, user_id FROM registrations WHERE user_id = 96;
SELECT 'rank_applications' AS tbl, id, user_id, status FROM rank_applications WHERE user_id = 96;

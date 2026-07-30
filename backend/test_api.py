"""自动化接口测试"""
import httpx

BASE = 'http://127.0.0.1:8000'
passed = 0
failed = 0

def test(name, resp, expected_status=200):
    global passed, failed
    if resp.status_code == expected_status:
        passed += 1
        print(f'✅ {name}: {resp.status_code}')
    else:
        failed += 1
        print(f'❌ {name}: {resp.status_code} (期望{expected_status}) body={resp.text[:100]}')


print('=== 认证模块 ===')
# 登录三个用户
r1 = httpx.post(f'{BASE}/api/auth/login', json={'code': 't1'})
u1 = r1.json()
test('1. 登录', r1)
token_u1 = u1['access_token']

r1b = httpx.post(f'{BASE}/api/auth/login', json={'code': 't2'})
token_u2 = r1b.json()['access_token']
test('2. 登录用户2', r1b)

r1c = httpx.post(f'{BASE}/api/auth/login', json={'code': 't3'})
token_u3 = r1c.json()['access_token']
test('3. 登录用户3', r1c)

# 修改资料
r2 = httpx.put(f'{BASE}/api/auth/profile', json={'nickname': 'keill', 'game_id': 'keill0323'},
    headers={'Authorization': f'Bearer {token_u1}'})
test('4. 修改资料', r2)

# 普通用户越权
r3 = httpx.put(f'{BASE}/api/auth/admin/users/2', json={'student_id': 'x'},
    headers={'Authorization': f'Bearer {token_u1}'})
test('5. 越权防护(应403)', r3, 403)

print(f'\n=== 队伍模块 ===')
# user1 创建队伍
r4 = httpx.post(f'{BASE}/api/teams', json={'name': '猛虎队'},
    headers={'Authorization': f'Bearer {token_u1}'})
test('6. 创建队伍', r4, 201)
team1_id = r4.json()['id'] if r4.status_code == 201 else 1

# user3 加入 user1 的队伍
r5 = httpx.post(f'{BASE}/api/teams/join', json={'team_id': team1_id, 'user_id': 3},
    headers={'Authorization': f'Bearer {token_u1}'})
test('7. 加入队员', r5)

# 获取队伍详情
r6 = httpx.get(f'{BASE}/api/teams/{team1_id}',
    headers={'Authorization': f'Bearer {token_u1}'})
test('8. 获取队伍详情', r6)
if r6.status_code == 200:
    print(f'   队伍名: {r6.json()["name"]}, 状态: {r6.json()["status"]}')

# 人才市场
r7 = httpx.get(f'{BASE}/api/teams/talent-market?match_id=1',
    headers={'Authorization': f'Bearer {token_u1}'})
test('9. 人才市场', r7)

print(f'\n=== 赛事模块 ===')
# 获取赛事列表
r8 = httpx.get(f'{BASE}/api/matches',
    headers={'Authorization': f'Bearer {token_u1}'})
test('10. 获取赛事列表', r8)
print(f'   赛事数: {len(r8.json())}')

# 搜索赛事
r9 = httpx.get(f'{BASE}/api/matches/search?keyword=春季',
    headers={'Authorization': f'Bearer {token_u1}'})
test('11. 搜索赛事', r9)

print(f'\n=== 测试结果 ===')
print(f'通过: {passed}, 失败: {failed}')

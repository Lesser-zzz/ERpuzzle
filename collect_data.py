import requests
import csv
import time
import os

API_KEY = "6ws22XYyOd4F0Puup5Hps2eK0XZ5uHMC3kqKIrYL"
headers = {"x-api-key": API_KEY, "accept": "application/json"}
csv_filename = "er_makgeumgu_dataset_v2.csv"

# 1. 중복 방지 (Deduplication) 세팅
processed_game_ids = set()

if os.path.exists(csv_filename):
    # 기존 CSV 파일이 있다면 수집했던 game_id를 모두 기억합니다.
    with open(csv_filename, mode='r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader, None) # 헤더 건너뛰기
        for row in reader:
            if row:
                processed_game_ids.add(int(row[0]))
else:
    # 파일이 없다면 새로 만들고 헤더를 작성합니다.
    with open(csv_filename, mode='w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        header = ["game_id"]
        for prefix in ["win", "lose"]:
            for i in range(1, 4):
                header.extend([f"{prefix}_char{i}", f"{prefix}_lv{i}", f"{prefix}_leg{i}", f"{prefix}_tran{i}"])
        writer.writerow(header)

print(f"🚀 가동 시작! 현재 저장된 매치 데이터: {len(processed_game_ids)}개\n")

# 2. 스노우볼 샘플링 (Snowball Sampling) 큐 세팅
target_nicknames = set()
processed_nicknames = set()

# 초기 시드(Seed) 데이터 확보: 랭킹 API에서 3명만 가져와 출발점으로 삼습니다.
url_rank = "https://open-api.bser.io/v1/rank/top/41/3"
response_rank = requests.get(url_rank, headers=headers)
if response_rank.status_code == 200 and response_rank.json().get('topRanks'):
    for p in response_rank.json()['topRanks'][:3]:
        target_nicknames.add(p['nickname'])

# 안전장치: 한 번 코드를 돌릴 때 최대 탐색할 유저 수 (원하는 만큼 늘려도 됩니다)
max_loops = 50
loop_count = 0

# 3. 파도타기 수집 루프 시작
while target_nicknames and loop_count < max_loops:
    nickname = target_nicknames.pop()
    processed_nicknames.add(nickname)
    loop_count += 1

    print(f"🔍 [탐색 {loop_count}/{max_loops}] '{nickname}' 님의 기록을 파헤칩니다...")

    # 닉네임으로 userId 발급
    url_user = f"https://open-api.bser.io/v1/user/nickname?query={nickname}"
    res_user = requests.get(url_user, headers=headers)

    if res_user.status_code == 200 and 'user' in res_user.json():
        user_id = res_user.json()['user']['userId']

        # 최근 매치 조회
        url_games = f"https://open-api.bser.io/v1/user/games/uid/{user_id}"
        res_games = requests.get(url_games, headers=headers)

        if res_games.status_code == 200 and res_games.json().get('userGames'):
            games_data = res_games.json()['userGames']

            # 테스트를 위해 유저당 최근 3게임만 확인합니다. (확장 가능)
            game_id_list = list(set([game['gameId'] for game in games_data]))[:3]

            for gid in game_id_list:
                # 💡 [중복 방지 필터] 이미 수집한 게임이면 통신(시간)을 낭비하지 않고 바로 패스!
                if gid in processed_game_ids:
                    print(f"   ⏩ [게임 {gid}] 이미 수집됨. 스킵합니다.")
                    continue

                # 새로운 게임의 상세 데이터 조회
                url_detail = f"https://open-api.bser.io/v1/games/{gid}"
                res_detail = requests.get(url_detail, headers=headers)

                if res_detail.status_code == 200:
                    detail_data = res_detail.json().get('userGames', [])
                    final_teams = {1: [], 2: []}

                    for p in detail_data:
                        # 💡 [스노우볼 필터] 이 판에 같이 잡힌 23명의 닉네임을 다음 타겟으로 훔쳐옵니다.
                        new_nick = p.get('nickname')
                        if new_nick and new_nick not in processed_nicknames:
                            target_nicknames.add(new_nick)

                        rank = p['gameRank']
                        if rank in [1, 2]:
                            equip_grades = p.get('equipmentGrade', {})
                            leg_cnt = sum(1 for g in equip_grades.values() if g == 5)
                            tran_cnt = sum(1 for g in equip_grades.values() if g == 6)
                            final_teams[rank].extend([p['characterNum'], p['characterLevel'], leg_cnt, tran_cnt])

                    # 최종 교전 데이터 저장
                    if len(final_teams[1]) == 12 and len(final_teams[2]) == 12:
                        with open(csv_filename, mode='a', newline='', encoding='utf-8-sig') as f:
                            writer = csv.writer(f)
                            writer.writerow([gid] + final_teams[1] + final_teams[2])
                        processed_game_ids.add(gid)
                        print(f"   ✅ [게임 {gid}] 추가 완료! (현재 스노우볼 대기자: {len(target_nicknames)}명)")

                time.sleep(1) # 서버 밴 방지용 휴식
        time.sleep(1)

print("\n🎉 스노우볼 수집 사이클 종료! 코드를 다시 실행하면 끊긴 곳부터 이어서 긁어옵니다.")

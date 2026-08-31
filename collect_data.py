import requests
import csv
import time
import os
from collections import deque
from urllib.parse import quote

# ============================================================
# 설정
# ============================================================

API_KEY = os.environ.get("BSER_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "❌ BSER_API_KEY 환경변수를 찾을 수 없습니다.\n"
        "GitHub Actions에서는 secrets.BSER_API_KEY를 env로 전달해주세요."
    )

HEADERS = {
    "x-api-key": API_KEY,
    "accept": "application/json",
}

# 💡 구출 작전으로 만들어낸 새 파일에 데이터를 계속 누적하도록 파일명 변경
CSV_FILENAME = "er_makgeumgu_dataset_v3_rescued.csv"

# ============================================================
# Snowball 상태 파일
# ============================================================
PENDING_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
KNOWN_GAMES_FILE = "known_games.txt"

# ============================================================
# 수집 설정
# ============================================================
SEASON_ID = 41
MATCHING_MODE = 3
INITIAL_SEED_COUNT = 100
RECENT_GAME_LIMIT = 20
MAX_LOOPS = 20
REQUEST_INTERVAL = 1.1
RATE_LIMIT_WAIT = 10
NETWORK_ERROR_WAIT = 5

# ============================================================
# API 요청 (Rate Limit 방어 로직)
# ============================================================
last_request_time = 0.0

def api_get(url):
    global last_request_time
    while True:
        elapsed = time.time() - last_request_time
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            last_request_time = time.time()
            if response.status_code in (403, 429):
                print(f"   ⚠️ Rate Limit 감지 ({response.status_code}) → {RATE_LIMIT_WAIT}초 대기")
                time.sleep(RATE_LIMIT_WAIT)
                continue
            return response
        except requests.RequestException as e:
            last_request_time = time.time()
            print(f"   ⚠️ 네트워크 오류: {e} → {NETWORK_ERROR_WAIT}초 후 재시도")
            time.sleep(NETWORK_ERROR_WAIT)

def load_lines(filename):
    if not os.path.exists(filename): return set()
    result = set()
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            value = line.strip()
            if value: result.add(value)
    return result

def append_line(filename, value):
    with open(filename, "a", encoding="utf-8") as f:
        f.write(f"{value}\n")

# ============================================================
# CSV 초기화 / 기존 데이터 로드
# ============================================================
processed_game_ids = set()

if os.path.exists(CSV_FILENAME):
    with open(CSV_FILENAME, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if not row: continue
            try: processed_game_ids.add(int(row[0]))
            except (ValueError, TypeError): pass
else:
    print(f"ℹ️ {CSV_FILENAME}이 존재하지 않아 새로 생성합니다.")
    with open(CSV_FILENAME, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        header = ["game_id"]
        for prefix in ["win", "lose"]:
            for i in range(1, 4):
                # 💡 헤더에 무기 번호(_weapon) 열 추가
                header.extend([
                    f"{prefix}_char{i}",
                    f"{prefix}_weapon{i}",
                    f"{prefix}_lv{i}",
                    f"{prefix}_leg{i}",
                    f"{prefix}_tran{i}",
                ])
        writer.writerow(header)

# ============================================================
# 상태 파일 로드 & 최초 Seed 확보
# ============================================================
processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_FILE)
known_games = load_lines(KNOWN_GAMES_FILE)
pending_nicknames -= processed_nicknames

print(f"\n🚀 Eternal Return Snowball Collector 가동\n{'='*65}")

if not pending_nicknames and not processed_nicknames:
    print(f"🌱 최초 실행 → 랭킹 상위 {INITIAL_SEED_COUNT}명 확보")
    url_rank = f"https://open-api.bser.io/v1/rank/top/{SEASON_ID}/{MATCHING_MODE}"
    response_rank = api_get(url_rank)
    if response_rank.status_code == 200:
        for player in response_rank.json().get("topRanks", [])[:INITIAL_SEED_COUNT]:
            nick = player.get("nickname")
            if nick and nick not in processed_nicknames and nick not in pending_nicknames:
                pending_nicknames.add(nick)
                append_line(PENDING_FILE, nick)

queue = deque(pending_nicknames)
loop_count = 0

# ============================================================
# Snowball Loop
# ============================================================
while queue and loop_count < MAX_LOOPS:
    nickname = queue.popleft()
    if nickname in processed_nicknames: continue
    loop_count += 1
    
    print(f"\n🔍 [{loop_count}/{MAX_LOOPS}] '{nickname}' 탐색")
    encoded_nickname = quote(nickname, safe="")
    res_user = api_get(f"https://open-api.bser.io/v1/user/nickname?query={encoded_nickname}")
    
    if res_user.status_code != 200 or "user" not in res_user.json():
        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)
        continue
        
    user_id = res_user.json()["user"].get("userNum") or res_user.json()["user"].get("userId") # API 버전에 따른 예외 처리
    
    res_games = api_get(f"https://open-api.bser.io/v1/user/games/uid/{user_id}")
    if res_games.status_code != 200:
        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)
        continue

    games_data = res_games.json().get("userGames", [])
    game_id_list = []
    seen_game_ids = set()
    for game in games_data:
        gid = game.get("gameId")
        if gid and gid not in seen_game_ids:
            seen_game_ids.add(gid)
            game_id_list.append(gid)
            if len(game_id_list) >= RECENT_GAME_LIMIT: break

    for gid in game_id_list:
        gid_str = str(gid)
        if gid_str in known_games: continue

        res_detail = api_get(f"https://open-api.bser.io/v1/games/{gid}")
        if res_detail.status_code != 200: continue
        
        detail_data = res_detail.json().get("userGames", [])
        if not detail_data: continue

        discovered_count = 0
        for player in detail_data:
            new_nick = player.get("nickname")
            if new_nick and new_nick not in processed_nicknames and new_nick not in pending_nicknames:
                pending_nicknames.add(new_nick)
                queue.append(new_nick)
                append_line(PENDING_FILE, new_nick)
                discovered_count += 1

        final_teams = {1: [], 2: []}
        rank_counts = {1: 0, 2: 0}

        for player in detail_data:
            rank = player.get("gameRank")
            if rank in (1, 2):
                rank_counts[rank] += 1
                equip_grades = player.get("equipmentGrade", {})
                if not isinstance(equip_grades, dict): equip_grades = {}
                
                leg_cnt = sum(1 for grade in equip_grades.values() if grade == 5)
                tran_cnt = sum(1 for grade in equip_grades.values() if grade == 6)

                # 💡 캐릭터 번호와 함께 bestWeapon(무기 번호) 추가!
                final_teams[rank].extend([
                    player.get("characterNum"),
                    player.get("bestWeapon", 0),
                    player.get("characterLevel"),
                    leg_cnt,
                    tran_cnt,
                ])

        # 💡 각 유저당 5개의 데이터(캐릭터, 무기, 레벨, 전설, 초월) * 3명 = 팀당 15개의 데이터
        if rank_counts[1] == 3 and rank_counts[2] == 3 and len(final_teams[1]) == 15 and len(final_teams[2]) == 15:
            if gid not in processed_game_ids:
                with open(CSV_FILENAME, "a", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([gid] + final_teams[1] + final_teams[2])
                processed_game_ids.add(gid)
            
            known_games.add(gid_str)
            append_line(KNOWN_GAMES_FILE, gid_str)
            print(f"   ✅ 게임 {gid} 저장 완료 | 새 유저 +{discovered_count} | 전체 {len(processed_game_ids):,}")

    processed_nicknames.add(nickname)
    append_line(PROCESSED_FILE, nickname)
    pending_nicknames.discard(nickname)

print(f"\n🎉 이번 수집 사이클 종료 | 현재 데이터셋: {len(processed_game_ids):,} 게임")

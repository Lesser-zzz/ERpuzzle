import requests
import csv
import time
import os
from collections import deque
from urllib.parse import quote

# ============================================================
# ⏱️ 1.5시간 타이머 및 설정
# ============================================================
START_TIME = time.time()
MAX_EXECUTION_TIME = 85 * 60 # 85분(5100초). 90분 전 안전 종료 방어.

API_KEY = os.environ.get("BSER_API_KEY")
if not API_KEY:
    raise RuntimeError("❌ BSER_API_KEY 환경변수를 찾을 수 없습니다.")

HEADERS = {"x-api-key": API_KEY, "accept": "application/json"}
CSV_FILENAME = "er_makgeumgu_dataset_v3_rescued.csv"

PENDING_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
KNOWN_GAMES_FILE = "known_games.txt"

# 🔥 헤비 듀티 전용 튜닝 파라미터
SEASON_ID = 41
MATCHING_MODE = 3
INITIAL_SEED_COUNT = 100
RECENT_GAME_LIMIT = 50  # 기존 20 -> 50으로 증가. 한 유저의 옛날 전적까지 영혼까지 긁어옴
MAX_LOOPS = 5000        # 사실상 무한 루프
REQUEST_INTERVAL = 1.1
RATE_LIMIT_WAIT = 10
NETWORK_ERROR_WAIT = 5

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
# 데이터 로드 및 초기 세팅
# ============================================================
processed_game_ids = set()
if os.path.exists(CSV_FILENAME):
    with open(CSV_FILENAME, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if not row: continue
            try: processed_game_ids.add(int(row[0]))
            except ValueError: pass
else:
    with open(CSV_FILENAME, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        header = ["game_id"]
        for prefix in ["win", "lose"]:
            for i in range(1, 4):
                header.extend([
                    f"{prefix}_char{i}", f"{prefix}_weapon{i}", 
                    f"{prefix}_lv{i}", f"{prefix}_leg{i}", f"{prefix}_tran{i}"
                ])
        writer.writerow(header)

processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_FILE)
known_games = load_lines(KNOWN_GAMES_FILE)
pending_nicknames -= processed_nicknames

print(f"\n🚀 [V-LONG] Heavy Duty 자동 수집기 가동 시작\n{'='*65}")

if not pending_nicknames and not processed_nicknames:
    response_rank = api_get(f"https://open-api.bser.io/v1/rank/top/{SEASON_ID}/{MATCHING_MODE}")
    if response_rank.status_code == 200:
        for player in response_rank.json().get("topRanks", [])[:INITIAL_SEED_COUNT]:
            nick = player.get("nickname")
            if nick and nick not in processed_nicknames and nick not in pending_nicknames:
                pending_nicknames.add(nick)
                append_line(PENDING_FILE, nick)

queue = deque(pending_nicknames)
loop_count = 0

# ============================================================
# 무한 스노우볼 루프 (타이머 체크 포함)
# ============================================================
while queue and loop_count < MAX_LOOPS:
    # 💡 실행 시간 안전 종료 타이머 체크 (85분 경과 시 종료)
    current_time = time.time()
    if current_time - START_TIME > MAX_EXECUTION_TIME:
        print("\n⏱️ [안전 종료] 실행 시간이 85분을 초과하여 시스템을 안전하게 종료하고 커밋을 준비합니다.")
        break

    nickname = queue.popleft()
    if nickname in processed_nicknames: continue
    loop_count += 1
    
    print(f"\n🔍 [{loop_count}/MAX_∞] '{nickname}' 탐색 중... (실행시간: {(current_time - START_TIME)/60:.2f}분)")
    encoded_nickname = quote(nickname, safe="")
    res_user = api_get(f"https://open-api.bser.io/v1/user/nickname?query={encoded_nickname}")
    
    if res_user.status_code != 200 or "user" not in res_user.json():
        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)
        continue
        
    user_id = res_user.json()["user"].get("userNum") or res_user.json()["user"].get("userId")
    
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

                # 무기 번호(bestWeapon) 포함 추출 로직
                final_teams[rank].extend([
                    player.get("characterNum"),
                    player.get("bestWeapon", 0),
                    player.get("characterLevel"),
                    leg_cnt,
                    tran_cnt,
                ])

        if rank_counts[1] == 3 and rank_counts[2] == 3 and len(final_teams[1]) == 15 and len(final_teams[2]) == 15:
            if gid not in processed_game_ids:
                with open(CSV_FILENAME, "a", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([gid] + final_teams[1] + final_teams[2])
                processed_game_ids.add(gid)
            
            known_games.add(gid_str)
            append_line(KNOWN_GAMES_FILE, gid_str)
            print(f"   ✅ 게임 {gid} 저장 | 새 유저 +{discovered_count} | 전체 {len(processed_game_ids):,}")

    processed_nicknames.add(nickname)
    append_line(PROCESSED_FILE, nickname)
    pending_nicknames.discard(nickname)

print(f"\n🎉 [V-LONG] 수집 사이클 완전 종료 | 획득한 전체 데이터: {len(processed_game_ids):,} 게임")
